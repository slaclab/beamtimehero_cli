"""The six ``tender`` leaves, in tools_core's handler contract:
``t_<name>(arguments) -> (json_text, [png_b64, ...])``. Registered into
``tools_core._HANDLERS`` by name (``TENDER_HANDLERS``); the tree comes from
``categorize.CATEGORY_OVERRIDES``.

All six are READ-ONLY with respect to data and hardware: they read the raw
share and the processed tree, and write only their RIXS-map cache and the
figures the host saves. Anything that must become a record is returned as
the Processing-tab job that would produce it (`reproduce_with`), never run.
"""

from __future__ import annotations

import json
import math
import os

import numpy as np

from . import diagnostics as dx
from . import discovery, plots, reduce, results
from .config import ToolError, cache_dir, data_dir, max_file_frames, processed_dir, \
    safe_name, workspace_dir
from .settings import Normalisation, Reduction, Roi, background_rule, job_params


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else round(f, 6)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    return o


def _ok(obj: dict, images: list[str] | None = None):
    return json.dumps(_clean(obj), indent=2, default=str), images or []


def _err(e: Exception, **extra):
    body = {"error": str(e), **extra}
    if isinstance(e, reduce.BudgetError):
        body["budget"] = e.details
    return json.dumps(_clean(body), indent=2, default=str), []


def _guard(fn):
    def wrapped(arguments: dict | None = None):
        try:
            return fn(dict(arguments or {}))
        except ToolError as e:
            return _err(e)
        except FileNotFoundError as e:
            return _err(ToolError(f"file not found: {os.path.basename(str(e))}"))
        except ImportError as e:
            if "tender_analysis" not in str(e) and "sif_parser" not in str(e):
                raise
            return _err(ToolError("tender-analysis is not installed "
                                  "(pip install 'beamtimehero_cli[tender]')"))
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


def _sample(a: dict) -> str:
    s = a.get("sample")
    return "" if s in (None, "") else safe_name(s, "sample")


def _brief(names: list[str], k: int = 3) -> list[str]:
    return names if len(names) <= 2 * k else names[:k] + [f"... {len(names) - 2 * k} more ..."] \
        + names[-k:]


# ------------------------------------------------------------------ 1. list


@_guard
def t_tender_list_measurements(a: dict):
    """Compounds of the beamtime, or one compound's measurements."""
    if "sample" not in a:
        out = discovery.list_compounds()
        out["next"] = ("Pass sample=<compound> to group that compound's files into "
                       "measurements.")
        return _ok(out)
    sample = _sample(a)
    rep = discovery.scan(sample)
    have_processed = processed_dir() is not None
    ms = []
    for m in rep["measurements"]:
        row = {k: m[k] for k in ("label", "kind", "emission_line", "incident_energy",
                                  "series_index", "n_files", "n_dark", "energy_min",
                                  "energy_max")}
        row["files"] = _brief(m["files"])
        row["dark_files"] = m["dark_files"]
        row["background_if_default"] = background_rule(m["kind"], "auto", m["n_dark"])
        if have_processed:
            last = results.latest_for(sample, m["label"])
            row["processed"] = (None if last is None else
                                {"output": last["output"], "origin": last["origin"]})
        ms.append(row)
    groups: dict = {}
    for m in rep["measurements"]:
        if m["kind"] == "RIXS":
            groups.setdefault((m["sample"], m["emission_line"]), []).append(m["label"])
    repeats = [{"sample": k[0], "emission_line": k[1], "series": v}
               for k, v in groups.items() if len(v) > 1]
    rej: dict = {}
    for r in rep["rejected"]:
        g = rej.setdefault(r["reason"], {"n": 0, "files": [],
                                         "meaning": rep["reasons"].get(r["reason"])})
        g["n"] += 1
        if len(g["files"]) < 8:
            g["files"].append(r["file"])
    return _ok({"sample": sample, "n_files": rep["n_files"], "measurements": ms,
                "repeats": repeats, "not_included": rej,
                "grouped_from": "filenames only (file contents not checked)",
                "processed_tree_in_scope": have_processed})


# --------------------------------------------------------------- 2. inspect


@_guard
def t_tender_inspect_measurement(a: dict):
    """Header-level facts about one measurement, and whether its dark is dark."""
    sample = _sample(a)
    m = discovery.find_measurement(sample, a.get("measurement", ""))
    paths, darks = m["_paths"], m["_dark_paths"]
    if len(paths) > 3000:
        raise ToolError(f"{len(paths)} files: too many to read headers for in chat")
    rows = []
    for p in paths:
        h = discovery.header(p)
        h["name_eV"] = discovery.energy_from_name(p)
        rows.append(h)
    dark_rows = [discovery.header(p) for p in darks]
    frames = sum(r.get("frames_est", 0) for r in rows)
    out = {"measurement": m["label"], "kind": m["kind"], "emission_line": m["emission_line"],
           "n_files": len(paths), "n_dark": len(darks), "frames_est": frames,
           "energy_range_eV": [m["energy_min"], m["energy_max"]],
           "exposure_s": sorted({r.get("exptime_s") for r in rows if r.get("exptime_s")}),
           "non_andor_headers": [r["file"] for r in rows if r.get("andor_header") is False],
           "energies": dx.energy_check(rows),
           "i0": dx.i0_check([r.get("I0") for r in rows]),
           "darks": dark_rows,
           "background": {"dark=auto": background_rule(m["kind"], "auto", len(darks)),
                          "dark=none": background_rule(m["kind"], "none", len(darks))}}
    if len(rows) <= 40:
        out["files"] = [{k: r.get(k) for k in ("file", "mono_eV", "I0", "exptime_s",
                                               "frames_est")} for r in rows]
    try:
        reduce.check_budget(paths + darks, passes=1)
        out["chat_can_rereduce"] = True
    except reduce.BudgetError as e:
        out["chat_can_rereduce"] = False
        out["chat_budget"] = {"reason": str(e), **e.details}

    images = []
    rec = {"dark": a.get("dark", "auto")}
    if a.get("check_dark", True) and darks:
        from tender_analysis import SifFile

        if max(discovery.header(d).get("frames_est", 1) for d in darks[:1]) > max_file_frames():
            out["dark_check"] = {"skipped": "dark file too large to decode in chat"}
        else:
            # Compare against the HIGHEST-energy point (RIXS: above the edge,
            # where the emission band is strongest) or the first XES scan.
            i = int(np.nanargmax([r.get("mono_eV") or r.get("name_eV") or -1 for r in rows])) \
                if m["kind"] == "RIXS" else 0
            try:
                reduce.check_budget([darks[0], paths[i]])
            except reduce.BudgetError as e:
                out["dark_check"] = {"skipped": f"not within this session's budget ({e})"}
        if "dark_check" not in out:
            dark = SifFile(darks[0]).data.mean(axis=0)
            data = SifFile(paths[i]).data.mean(axis=0)
            dc = dx.dark_check(dark, data, dark_rows[0], rows[i])
            dc["dark_file"] = os.path.basename(darks[0])
            out["dark_check"] = dc
            if dc["verdict"] == "lit":
                rec["dark"] = "none"
            images.append(plots.dark_profiles(
                dx.column_profile(dark, dx.pedestal(dark)["common_mode_adu"]),
                dx.column_profile(data, dx.pedestal(data)["common_mode_adu"]),
                os.path.basename(darks[0]), rows[i]["file"], dc["verdict"]))
    if processed_dir() is not None:
        last = results.latest_for(sample, m["label"])
        out["processed"] = last and {"output": last["output"], "origin": last["origin"],
                                     "summary": last["summary"], "settings": last["settings"]}
    out["reproduce_with"] = job_params(m["kind"], data_dir().name, sample, m["label"],
                                       Reduction.from_args(rec),
                                       Roi() if m["kind"] == "RIXS" else None)
    return _ok(out, images)


# --------------------------------------------------------------- 3. preview


def _pick_file(sample: str, a: dict) -> tuple[str, dict | None]:
    if a.get("file"):
        p = str(discovery.file_path(sample, a["file"]))
        rep = discovery.scan(sample)
        owner = next((m for m in rep["measurements"]
                      if os.path.basename(p) in m["files"]), None)
        m = discovery.find_measurement(sample, owner["label"]) if owner else None
        return p, m
    if not a.get("measurement"):
        raise ToolError("give file=<basename>, or measurement=<label> with point")
    m = discovery.find_measurement(sample, a["measurement"])
    paths = m["_paths"]
    point = a.get("point", "last" if m["kind"] == "RIXS" else "first")
    if point in ("last", "first"):
        es = [discovery.energy_from_name(p) or 0 for p in paths]
        i = int(np.argmax(es)) if point == "last" else int(np.argmin(es))
    elif abs(float(point)) < 1000 and float(point) == int(float(point)):
        # an index: incident energies are all > 1900 eV, indices are small
        i = int(float(point))
        if not -len(paths) <= i < len(paths):
            raise ToolError(f"point index {i} out of range for {len(paths)} files")
    else:
        e = float(point)
        es = [discovery.energy_from_name(p) or np.inf for p in paths]
        i = int(np.argmin([abs(x - e) for x in es]))
    return paths[i], m


@_guard
def t_tender_preview_image(a: dict):
    """One detector image through the per-frame chain, with its histograms."""
    from tender_analysis import SifFile, extract_signal
    from tender_analysis.preview import preview

    sample = _sample(a)
    path, m = _pick_file(sample, a)
    red = Reduction.from_args({**a, "dark": "auto"})  # thresholds + bcg_adjust only
    mode = a.get("dark", "auto")
    if mode not in ("auto", "none", "flat"):
        raise ToolError("dark is 'auto', 'none' (min-projection over the measurement) or "
                        "'flat' (pedestal only, a diagnostic)")
    reduce.check_budget([path])
    shape = None
    if mode == "auto" and m and m["_dark_paths"]:
        bcg = reduce.mean_dark(m["_dark_paths"])
        bg = f"paired dark ({', '.join(os.path.basename(d) for d in m['_dark_paths'])})"
    elif mode == "none" and m:
        reduce.check_budget(m["_paths"])
        bcg = reduce.min_projection(m["_paths"])
        bg = "min-projection over the measurement's frames"
    else:
        from tender_analysis.common import common_mode
        first = SifFile(path).frame(0)
        shape = first.shape
        bcg = np.full(shape, float(common_mode(first, refine=True)))
        bg = ("flat pedestal (diagnostic; not a job option)" if mode == "flat"
              else "flat pedestal (no paired dark for this file)")
    th = red.thresholds()
    frame = a.get("frame")
    prev = preview(path, dark=bcg, thresholds=list(th.as_array()),
                   frame=None if frame is None else int(frame), bcg_adjust=red.bcg_adjust)
    sif = SifFile(path)
    sel = None if frame is None else [int(frame)]
    r = extract_signal([sif], [th.low, th.xray, th.hi], bcg=bcg, bcg_adjust=red.bcg_adjust,
                       histograms=True, scan_nbrs=sel)
    raw0 = sif.frame(0 if frame is None else int(frame))
    ped = dx.pedestal(raw0)
    spec = np.asarray(prev["spectrum"])
    cp = a.get("central_pix")
    n = int(a.get("n", 7) or 7)
    peaks = dx.emission_peaks(spec, int(cp) if cp is not None else int(np.argmax(spec)))
    out = {"file": os.path.basename(path), "measurement": m and m["label"],
           "shape_frames_rows_cols": list(prev["shape"]),
           "frames_used": "all (summed)" if frame is None else int(frame),
           "mono_eV": prev["mono"], "I0": prev["I0"], "exposure_s": prev["exposure"],
           "background": bg, "bcg_adjust": red.bcg_adjust,
           "thresholds [bcg_cutoff, low, xray, hi]": list(th.as_array()),
           "pedestal": ped, "photon_events": prev["n_events"],
           "grains": dx.grain_peak(r.histograms["xray"], th),
           "spectrum": {"sum": float(spec.sum()), "peak_pixel": int(np.argmax(spec)),
                        "peaks": peaks["peaks"],
                        "doublet": peaks.get("doublet")}}
    images = [plots.image_with_spectrum(prev, central_pix=cp, n=n,
                                        image=a.get("show", "events"),
                                        title=f"{os.path.basename(path)} ({bg})"),
              plots.adu_histograms(r.histograms, th)]
    return _ok(out, images)


# ----------------------------------------------------------------- 4. herfd


def _curve(rm, roi, norm, label):
    h = reduce.herfd_from_map(rm, roi)
    chk, res = dx.normalisation_check(h["E"], h["HERFD"], norm.overrides)
    return {"label": label, "E": h["E"], "HERFD": h["HERFD"], "TFY": h["TFY"],
            "norm": np.asarray(res.norm) if np.isfinite(res.e0) else None,
            "central_pix": h["central_pix"], "auto": h["auto"], "check": chk,
            "map": h["map"]}


@_guard
def t_tender_herfd(a: dict):
    """RIXS map -> HERFD/TFY -> normalised, with the checks and the job."""
    sample = _sample(a)
    m = discovery.find_measurement(sample, a.get("measurement", ""))
    red, roi, norm = Reduction.from_args(a), Roi.from_args(a), Normalisation.from_args(a)
    try:
        rm, build = reduce.rixs_map(m, red, use_cache=not a.get("rebuild"))
    except reduce.BudgetError as e:
        return _from_record(sample, m, e, red, roi, norm)

    rixs = reduce.i0_divided(rm, roi.i0_corr)
    profile = rixs[:, max(0, rixs.shape[1] - 10):].sum(axis=1)
    auto = reduce.auto_centre(rm, roi.i0_corr)
    peaks = dx.emission_peaks(profile, auto)

    main = _curve(rm, roi, norm, f"ROI {roi.central_pix if roi.central_pix is not None else f'auto={auto}'} ± {roi.n // 2}")
    curves = [main]
    cmp_ = a.get("compare_centres")
    if cmp_:
        cand = ([p["pixel"] for p in peaks["peaks"][:3]] + [auto]) if cmp_ == "peaks" else \
            [int(x) for x in (cmp_ if isinstance(cmp_, list) else str(cmp_).split(","))]
        for c in dict.fromkeys(cand):
            if c != main["central_pix"] and len(curves) < 6:
                curves.append(_curve(rm, Roi(c, roi.n, roi.i0_corr), norm, f"ROI {c} ± {roi.n // 2}"))

    out = {"measurement": m["label"], "reduction": rm.meta, "build": build,
           "points": int(len(rm.E)),
           "energy_range_eV": [float(rm.E.min()), float(rm.E.max())],
           "i0_corrected": roi.i0_corr, "roi_width_px": roi.n,
           "auto_centre": auto, "emission": peaks,
           "curves": [{"label": c["label"], "central_pix": c["central_pix"],
                       "normalisation": c["check"]} for c in curves],
           "i0": dx.i0_check(list(rm.I0))}
    if main["check"].get("verdict", "").startswith("'norm' ends") or \
            main["check"].get("post_edge_span_eV", 999) < 60:
        e0 = main["check"].get("e0_eV")
        if e0:
            out["post_edge_candidates"] = dx.suggest_post_edge(main["E"], main["HERFD"], e0)
    if red.dark == "auto" and m["n_dark"] == 1:
        out["dark_note"] = ("background is the paired dark; if not already done, check it is "
                            "really dark with tender_inspect_measurement (a lit dark "
                            "silently distorts every point)")
    if processed_dir() is not None:
        last = results.latest_for(sample, m["label"])
        if last:
            _, _, cols = results.load(last["output"])
            same = _same_settings(last["settings"], red, roi, main["central_pix"])
            entry = {"output": last["output"], "origin": last["origin"],
                     "settings": last["settings"], "same_settings_as_this_call": same}
            if same and "mu" in cols and len(cols["mu"]) == len(main["HERFD"]):
                d = np.nanmax(np.abs(cols["mu"] - main["HERFD"])) / np.nanmax(np.abs(cols["mu"]))
                entry["max_rel_difference_vs_this_call"] = float(d)
            out["processed"] = entry
    out["reproduce_with"] = job_params("RIXS", data_dir().name, sample, m["label"], red,
                                       Roi(roi.central_pix, roi.n, roi.i0_corr), norm)

    class _R:  # the shape plots.rixs_map_with_profile expects
        pass
    r = _R()
    r.rixs_map, r.E, r.meta = main["map"], main["E"], {"i0_corrected": roi.i0_corr}
    images = [plots.rixs_map_with_profile(r, profile, main["central_pix"], roi.n,
                                          peaks["peaks"]),
              plots.herfd_panels(curves)]
    return _ok(out, images)


def _same_settings(s: dict, red: Reduction, roi: Roi, cp_used: int) -> bool:
    th = s.get("threshold")
    th = None if isinstance(th, str) else th
    return (th == (None if red.threshold is None else [int(round(x)) for x in red.threshold])
            and s.get("dark", "auto") == red.dark and s.get("bcg_adjust", True) == red.bcg_adjust
            and int(s.get("n", 7)) == roi.n and s.get("i0_corr", True) == roi.i0_corr
            and str(s.get("central_pix_used")) == str(cp_used))


def _from_record(sample, m, err, red, roi, norm):
    """Over the chat budget: fall back to the processed record, if there is one."""
    last = results.latest_for(sample, m["label"]) if processed_dir() is not None else None
    body = {"measurement": m["label"], "rereduced": False,
            "why": str(err), "budget": err.details,
            "reproduce_with": job_params(m["kind"], data_dir().name, sample, m["label"],
                                         red, roi if m["kind"] == "RIXS" else None, norm)}
    if last is None:
        body["next"] = ("no processed output exists either: submit the job in "
                        "reproduce_with from the Processing tab")
        return _ok(body)
    _, header, cols = results.load(last["output"])
    body["from_record"] = {"output": last["output"], "origin": last["origin"],
                           "settings": last["settings"], "summary": last["summary"]}
    images = []
    if "mu" in cols:
        chk, res = dx.normalisation_check(cols["energy_eV"], cols["mu"], norm.overrides)
        body["from_record"]["normalisation"] = chk
        images.append(plots.overlay([{"E": cols["energy_eV"], "y": res.norm,
                                      "label": last["output"]}], "normalised HERFD"))
    return _ok(body, images)


# ------------------------------------------------------------------- 5. xes


def _load_calibration(a: dict):
    from tender_analysis import ElasticCalibration

    if a.get("calibration_m") is not None and a.get("calibration_b") is not None:
        return ElasticCalibration(m=float(a["calibration_m"]), b=float(a["calibration_b"])), \
            "given m, b"
    name = a.get("calibration_file")
    if name:
        name = safe_name(name, "calibration_file")
        for root in (workspace_dir(), cache_dir()):
            if root is None:
                continue
            for p in (root / name, root / "tender" / name):
                if p.is_file():
                    return ElasticCalibration.load_json(str(p)), f"file {name}"
        raise ToolError(f"no calibration file {name!r} in the workspace")
    elastic = a.get("elastic_sample")
    if elastic:
        from tender_analysis import calibrate_from_directory, parse_sif_name
        d = discovery.sample_dir(safe_name(elastic, "elastic_sample"))
        el = [str(p) for p in sorted(d.glob("*.sif"))
              if (r := parse_sif_name(str(p))) is not None and r.is_elastic]
        if not el:
            raise ToolError(f"no *elastic*.sif files in {elastic!r}")
        reduce.check_budget(el)
        cal = calibrate_from_directory(str(d), threshold=a.get("threshold"))
        return cal, f"fitted now from {len(cal.centers)} elastic scans in {elastic}"
    return None, None


def _peak_stats(x, y, window=None):
    y = np.asarray(y, float)
    i = int(np.nanargmax(y))
    half = y[i] / 2
    lo = i
    while lo > 0 and y[lo] > half:
        lo -= 1
    hi = i
    while hi < y.size - 1 and y[hi] > half:
        hi += 1
    sl = slice(max(0, i - (window or 60)), min(y.size, i + (window or 60) + 1))
    w = np.clip(y[sl], 0, None)
    cen = float(np.sum(np.asarray(x)[sl] * w) / w.sum()) if w.sum() > 0 else float("nan")
    return {"peak": float(np.asarray(x)[i]), "centroid": cen,
            "fwhm": float(abs(np.asarray(x)[hi] - np.asarray(x)[lo]))}


def _elastic_check(x, spec, ei, window_eV=3.0):
    """The sample's own elastic peak: a small LOCAL peak at the incident
    energy, usually riding the emission line's high-energy tail -- so find
    the local maximum nearest ei, never the tallest point in the window."""
    from scipy.signal import find_peaks

    x = np.asarray(x, float)
    near = np.flatnonzero(np.abs(x - ei) < window_eV)
    base = {"incident_eV": ei, "meaning": "the sample's own elastic peak: a free check of "
                                          "the calibration at this energy"}
    if near.size < 5:
        return {**base, "found": False, "why": "incident energy is off the calibrated axis"}
    s = np.convolve(np.asarray(spec, float), np.ones(3) / 3, mode="same")
    seg = s[near]
    idx, pr = find_peaks(seg, prominence=max(1e-9, 0.1 * (seg.max() - seg.min())))
    if not idx.size:
        return {**base, "found": False, "why": f"no local peak within ±{window_eV:g} eV"}
    j = near[idx[np.argmin(np.abs(x[near[idx]] - ei))]]
    return {**base, "found": True, "elastic_peak_on_axis_eV": float(x[j]),
            "offset_eV": float(x[j] - ei)}


@_guard
def t_tender_xes(a: dict):
    """An XES measurement's emission spectrum, optionally on an energy axis."""
    sample = _sample(a)
    m = discovery.find_measurement(sample, a.get("measurement", ""))
    red = Reduction.from_args(a)
    cal, cal_src = _load_calibration(a)
    try:
        r = reduce.xes_spectrum(m, red)
    except reduce.BudgetError as e:
        return _from_record(sample, m, e, red, None, Normalisation())
    spec = r["spectrum"]
    pix = np.arange(spec.size)
    x = cal.to_energy(pix) if cal is not None else pix
    xlabel = "emission energy (eV)" if cal is not None else "dispersive pixel (uncalibrated)"
    stats = _peak_stats(x, spec)
    win = int(a.get("window", 60))
    ip = int(np.argmax(spec))
    sl = slice(max(0, ip - win), ip + win + 1)
    cents = []
    for s in r["per_file"]:
        w = np.clip(np.asarray(s)[sl], 0, None)
        cents.append(float(np.sum(np.asarray(x)[sl] * w) / w.sum()) if w.sum() else np.nan)
    rel = [c - cents[0] for c in cents] if cents else []
    out = {"measurement": m["label"], "incident_energy_eV": m["incident_energy"],
           "emission_line": m["emission_line"], "reduction": r["meta"], "budget": r["budget"],
           "axis": xlabel, "main_peak": stats,
           "peaks": dx.emission_peaks(spec, ip)["peaks"],
           "per_scan_centroid_shift": {"files": r["files"], "shift": rel,
                                       "max_abs": float(np.nanmax(np.abs(rel))) if rel else None,
                                       "unit": "eV" if cal is not None else "px",
                                       "meaning": "a steady trend is beam damage or drift, "
                                                  "not noise; averaging hides it"},
           "i0": dx.i0_check(r["I0"])}
    marks = {}
    if cal is not None:
        out["calibration"] = {"source": cal_src, "m_eV_per_px": cal.m, "b_eV": cal.b,
                              "rms_eV": cal.rms,
                              "span_eV": [float(cal.to_energy(0)), float(cal.to_energy(2047))]}
        if len(cal.energies):
            out["calibration"]["calibrated_between_eV"] = [float(min(cal.energies)),
                                                           float(max(cal.energies))]
            out["calibration"]["caution"] = (
                f"{len(cal.energies)} points, {len(cal.energies) - 2} degree(s) of freedom; "
                "outside the calibrated range the axis is an extrapolation of a straight line")
        ei = m["incident_energy"]
        if ei:
            marks["incident"] = ei
            out["elastic_check"] = _elastic_check(x, spec, ei)
    out["reproduce_with"] = job_params("XES", data_dir().name, sample, m["label"], red)
    if cal is not None:
        out["reproduce_with"]["not_yet_job_fields"] = {
            "calibration": {"m": cal.m, "b": cal.b},
            "note": "tender-xes jobs write a pixel axis; a calibration cannot be attached yet"}
    images = [plots.xes_panels(x, spec, rel, xlabel, marks,
                               unit="eV" if cal is not None else "px")]
    return _ok(out, images)


# --------------------------------------------------------------- 6. results


@_guard
def t_tender_results(a: dict):
    """The processed record: list, compare on one normalisation, or average repeats."""
    from tender_analysis.average import average_series, normalize_average

    action = a.get("action", "list")
    sample = None if a.get("sample") is None else _sample(a)
    norm = Normalisation.from_args(a)
    if action == "list":
        rows = results.outputs(sample, a.get("measurement"))
        return _ok({"n": len(rows), "outputs": rows[:60],
                    "truncated": len(rows) > 60})
    ids = a.get("outputs") or []
    if isinstance(ids, str):
        ids = [x.strip() for x in ids.split(",") if x.strip()]
    if not ids and a.get("repeats_of"):
        if sample is None:
            raise ToolError("repeats_of needs sample")
        rep = discovery.scan(sample)
        me = next((x for x in rep["measurements"] if x["label"] == a["repeats_of"]), None)
        if me is None:
            raise ToolError(f"no measurement {a['repeats_of']!r}")
        sibs = [x["label"] for x in rep["measurements"] if x["kind"] == "RIXS"
                and x["sample"] == me["sample"] and x["emission_line"] == me["emission_line"]]
        missing = []
        for lab in sibs:
            last = results.latest_for(sample, lab)
            (ids.append(last["output"]) if last else missing.append(lab))
        if missing:
            a["_missing"] = missing
    if not ids:
        raise ToolError("give outputs=[<job>:<file>, ...] (from action=list) or "
                        "sample + repeats_of=<measurement>")
    if len(ids) > 8:
        raise ToolError("at most 8 outputs at a time")
    loaded = [results.load(i) for i in ids]
    if any("mu" not in c for _, _, c in loaded):
        raise ToolError("compare/average read HERFD CSVs (energy_eV, mu, ...); an XES output "
                        "was given")
    rows = [r for r, _, _ in loaded]
    curves, e0s = [], []
    for (row, _, c) in loaded:
        chk, res = dx.normalisation_check(c["energy_eV"], c["mu"], norm.overrides)
        e0s.append(chk.get("e0_eV"))
        curves.append({"label": row["output"], "E": c["energy_eV"], "mu": c["mu"],
                       "tfy": c.get("tfy"), "norm": res.norm, "check": chk})
    out = {"action": action, "outputs": [r["output"] for r in rows],
           "settings_that_differ": results.mismatches(rows),
           "normalisation_used_for_all": norm.overrides or "larch defaults",
           "e0_eV": e0s}
    if a.get("_missing"):
        out["repeats_without_processed_output"] = a["_missing"]
    if out["settings_that_differ"]:
        out["warning"] = ("these outputs were reduced with different settings; differences "
                          "between them are partly the settings, not the samples")
    if action == "compare":
        out["curves"] = [{"output": c["label"], "normalisation": c["check"]} for c in curves]
        return _ok(out, [plots.overlay([{"E": c["E"], "y": c["norm"], "label": c["label"]}
                                        for c in curves], "normalised HERFD")])
    if action != "average":
        raise ToolError("action is list, compare or average")
    shifts = [0.0] * len(curves)
    if a.get("align_e0", True) and all(e is not None for e in e0s):
        shifts = [e - e0s[0] for e in e0s]
    items = [(c["E"] - s, c["mu"]) + ((c["tfy"],) if c["tfy"] is not None else ())
             for c, s in zip(curves, shifts)]
    avg = average_series(items, labels=[c["label"] for c in curves])
    nres = normalize_average(avg, norm.overrides or None)
    ok = np.isfinite(avg.std) & (avg.n > 1)
    rel = avg.std[ok] / np.abs(avg.mean[ok]) if ok.any() else np.array([])
    out.update(e0_shift_applied_eV=shifts, energy_grid="first output",
               n_series=len(items),
               spread={"median_rel_std": float(np.median(rel)) if rel.size else None,
                       "points_covered_by_all": int(np.sum(avg.n == len(items)))},
               average_normalisation={"method": nres.method, "e0_eV": nres.e0,
                                      "edge_step": nres.edge_step})
    if any(abs(s) > 0.3 for s in shifts):
        out["alignment_note"] = (f"repeats offset by up to {max(abs(s) for s in shifts):.2f} eV "
                                 "in E0; averaged after shifting onto the first. Something "
                                 "moved between scans (mono or beam position).")
    band = {"E": avg.E, "lo": nres.norm - avg.std / (nres.edge_step or 1),
            "hi": nres.norm + avg.std / (nres.edge_step or 1)}
    images = [plots.overlay([{"E": avg.E, "y": nres.norm, "label": "average"}] +
                            [{"E": c["E"] - s, "y": c["norm"], "label": c["label"]}
                             for c, s in zip(curves, shifts)][:7],
                            "normalised HERFD", band=band)]
    return _ok(out, images)


TENDER_HANDLERS = {
    "tender_list_measurements": t_tender_list_measurements,
    "tender_inspect_measurement": t_tender_inspect_measurement,
    "tender_preview_image": t_tender_preview_image,
    "tender_herfd": t_tender_herfd,
    "tender_xes": t_tender_xes,
    "tender_results": t_tender_results,
}
