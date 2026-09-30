"""Bounded re-reductions for chat: the job's maths at a chat pod's size.

A chat pod has 1 CPU and 2 GiB. `OnePotRIXS` keeps one dense float64 plane
per scan point (83 points = 700 MB before anything else), so the tools do
not call it. They use the pieces the library already ships for exactly this:

  * RIXS: `tender_analysis.live.HerfdAccumulator` -- per-file
    `reduce_frame`, sparse planes, ONE curvature fitted on the sum of all
    points and applied to each, i.e. OnePotRIXS's result (the library's own
    tests and the Tender story both pin the equality). Measured on the
    bundled Na2SO4 series: 83 images in 5 s, 245 MB peak.
  * XES: `extract_signal` one file at a time, summed, then one curvature fit
    on the sum -- OnePot.run's non-evolution path, streamed.

Two small pieces are written out here rather than called, each pinned to the
library by a test (tests/test_equivalence.py):

  * `min_projection`: `background.compute_background` needs every SifFile of
    the measurement alive at once (it holds their decoded frames) and
    refuses a single 1-frame file, so the same arithmetic is streamed.
  * `herfd_from_map`: `OnePotRIXS.herfd`'s I0 division and band sum,
    applied to a cached map so a ROI change costs nothing.

Nothing here writes anywhere except the RIXS-map cache (config.cache_dir).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass

import numpy as np

from .config import ToolError, cache_dir, estimate_frames, max_file_frames, max_frames
from .settings import Reduction, Roi, background_rule


# tender_analysis.curvature.CurvatureCorrection.apply indexes past the frame
# when a poorly constrained fit (few signal bins, noisy offsets) extrapolates
# to a row shift larger than the detector. The job would fail the same way.
_CURVATURE_FAILED = ("the library's curvature fit failed on this signal ({}): too few "
                     "signal columns, or noise, made the fitted shift larger than the "
                     "detector. The tender-herfd/-xes job fails the same way. Check the "
                     "thresholds/background with tender_preview_image.")


class BudgetError(ToolError):
    """The call would reduce more than a chat pod should."""

    def __init__(self, msg: str, details: dict):
        super().__init__(msg)
        self.details = details


def check_budget(paths: list[str], passes: int = 1) -> dict:
    """Refuse BEFORE reading anything if the measurement is too big."""
    per_file = {os.path.basename(p): estimate_frames(p) for p in paths}
    biggest = max(per_file.values(), default=0)
    total = sum(per_file.values()) * passes
    cap, file_cap = max_frames(), max_file_frames()
    info = {"frames_to_read": total, "frame_budget": cap, "largest_file_frames": biggest,
            "file_frame_budget": file_cap, "files": len(paths)}
    if biggest > file_cap:
        raise BudgetError(
            f"a file of this measurement holds ~{biggest} frames; decoding it needs "
            f"~{biggest * 8} MB, over the chat budget of {file_cap} frames per file", info)
    if total > cap:
        raise BudgetError(
            f"this would read ~{total} frames, over the chat budget of {cap}", info)
    return info


def min_projection(paths: list[str]) -> np.ndarray:
    """`background.compute_background(files)` streamed one file at a time:
    per-pixel minimum of (frame - its common mode) over every frame, plus
    the mean common mode (sifBatchBackground.m)."""
    from tender_analysis import SifFile
    from tender_analysis.common import common_mode

    bcg, total_cm, n = None, 0.0, 0
    for p in paths:
        sif = SifFile(p)
        for i in range(sif.num_frames):
            frame = sif.frame(i)
            cm = common_mode(frame, refine=False)
            total_cm += cm
            n += 1
            bcg = frame - cm if bcg is None else np.minimum(bcg, frame - cm)
        del sif
    if n < 2:
        raise ToolError("a min-projection background needs at least two frames")
    return bcg + total_cm / n


def mean_dark(paths: list[str]) -> np.ndarray:
    """Measurement._dark_background: the mean of each dark's frame average."""
    from tender_analysis import SifFile
    return np.mean([SifFile(p).data.mean(axis=0) for p in paths], axis=0)


# ------------------------------------------------------------------- RIXS


@dataclass
class RixsMap:
    """A measurement's RIXS map as the tools cache it: NOT I0-divided,
    columns in file (natural) order, exactly HerfdAccumulator.rixs_map()."""

    map: np.ndarray        # (pixel, point)
    E: np.ndarray          # incident energy per point
    I0: np.ndarray         # raw header I0 per point
    files: list[str]
    meta: dict

    def ordered(self):
        order = np.argsort(self.E, kind="stable")
        return order


def _cache_key(m: dict, red: Reduction) -> str:
    h = hashlib.sha1(json.dumps([m["files"], m["dark_files"]]).encode()).hexdigest()[:10]
    return f"{m['label']}__{red.key()}__{h}"


def rixs_map(m: dict, red: Reduction, *, use_cache: bool = True) -> tuple[RixsMap, dict]:
    """Build (or load) the RIXS map of a scan-report measurement."""
    if m["kind"] != "RIXS":
        raise ToolError(f"{m['label']!r} is an {m['kind']} measurement, not a RIXS series")
    path = cache_dir() / (_cache_key(m, red) + ".npz")
    if use_cache and path.is_file():
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        return RixsMap(z["map"], z["E"], z["I0"], list(z["files"]), meta), {
            "cache": "hit", "seconds": 0.0}

    from tender_analysis.live import HerfdAccumulator

    data, darks = m["_paths"], m["_dark_paths"]
    use_dark = red.dark == "auto" and len(darks) == 1
    budget = check_budget(data + (darks if use_dark else []), passes=1 if use_dark else 2)
    t0 = time.perf_counter()
    bcg = darks[0] if use_dark else min_projection(data)
    acc = HerfdAccumulator(dark=bcg, bcg_adjust=red.bcg_adjust,
                           thresholds=list(red.threshold) if red.threshold else None)
    for p in data:
        acc.add(p)
    try:
        mp, E, i0 = acc.rixs_map()
        cc = acc.curvature()
    except ValueError as e:
        raise ToolError(_CURVATURE_FAILED.format(e)) from None
    t = cc.t
    meta = {
        "background": background_rule("RIXS", red.dark, len(darks)),
        "thresholds [bcg_cutoff, low, xray, hi]": [float(x) for x in red.thresholds().as_array()],
        "bcg_adjust": red.bcg_adjust,
        "curvature_t": (None if t is None else
                        float(t) if np.isscalar(t) else [float(x) for x in np.ravel(t)]),
        "n_points": int(len(E)),
    }
    rm = RixsMap(mp, E, i0, [os.path.basename(p) for p in acc.paths], meta)
    np.savez_compressed(path, map=rm.map, E=rm.E, I0=rm.I0, files=np.array(rm.files),
                        meta=json.dumps(meta))
    return rm, {"cache": "miss", "seconds": round(time.perf_counter() - t0, 1), **budget}


def i0_divided(rm: RixsMap, i0_corr: bool) -> np.ndarray:
    if not i0_corr:
        return rm.map
    i0 = np.where((rm.I0 == 0) | np.isnan(rm.I0), 1.0, rm.I0)
    return rm.map / i0[np.newaxis, :]


def auto_centre(rm: RixsMap, i0_corr: bool) -> int:
    """The library's automatic ROI centre: ONE gaussian on the sum of the
    last 10 points in FILE order (pipeline._fit_central_pixel -- imported,
    not copied, because agreeing with the job is the whole point)."""
    from tender_analysis.pipeline import _fit_central_pixel
    return int(_fit_central_pixel(i0_divided(rm, i0_corr)))


def herfd_from_map(rm: RixsMap, roi: Roi) -> dict:
    """OnePotRIXS.herfd on a cached map: I0-divide, band-sum, TFY, sort by E."""
    rixs = i0_divided(rm, roi.i0_corr)
    cp = roi.central_pix if roi.central_pix is not None else auto_centre(rm, roi.i0_corr)
    band = np.clip(cp + np.arange(roi.n) - roi.n // 2, 0, rixs.shape[0] - 1)
    order = rm.ordered()
    return {"E": rm.E[order], "HERFD": rixs[band, :].sum(axis=0)[order],
            "TFY": rixs.sum(axis=0)[order], "central_pix": int(cp),
            "auto": roi.central_pix is None, "map": rixs[:, order]}


# -------------------------------------------------------------------- XES


def xes_spectrum(m: dict, red: Reduction) -> dict:
    """OnePot.run (non-evolution) over an XES measurement, one file at a time."""
    if m["kind"] != "XES":
        raise ToolError(f"{m['label']!r} is a {m['kind']} measurement, not XES")
    from tender_analysis import CurvatureCorrection, SifFile, extract_signal

    data, darks = m["_paths"], m["_dark_paths"]
    use_dark = red.dark == "auto" and bool(darks)
    budget = check_budget(data + (darks if use_dark else []), passes=1 if use_dark else 2)
    t0 = time.perf_counter()
    bcg = mean_dark(darks) if use_dark else min_projection(data)
    th = red.thresholds()
    planes, i0s = [], []
    for p in data:
        sif = SifFile(p)
        r = extract_signal([sif], [th.low, th.xray, th.hi], bcg=bcg,
                           bcg_adjust=red.bcg_adjust)
        planes.append(r.signal)
        i0s.append(sif.I0)
        del sif, r
    total = np.sum(planes, axis=0)
    cc = CurvatureCorrection()
    try:
        corrected, _ = cc.fit_apply(total)
    except ValueError as e:
        raise ToolError(_CURVATURE_FAILED.format(e)) from None
    per_file = [cc.apply(pl)[0].sum(axis=0) for pl in planes]
    t = cc.t
    return {
        "spectrum": corrected.sum(axis=0),
        "per_file": per_file,
        "files": [os.path.basename(p) for p in data],
        "I0": i0s,
        "meta": {"background": background_rule("XES", red.dark, len(darks)),
                 "thresholds [bcg_cutoff, low, xray, hi]": [float(x) for x in th.as_array()],
                 "bcg_adjust": red.bcg_adjust,
                 "curvature_t": None if t is None else [float(x) for x in np.ravel(t)]},
        "budget": {**budget, "seconds": round(time.perf_counter() - t0, 1)},
    }
