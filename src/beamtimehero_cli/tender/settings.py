"""Reduction settings, spelled exactly as chemcat's Processing tab spells them.

The tools and the tender-herfd / tender-xes jobs must mean the same thing by
every setting, or "try this in chat, then run it as a job" reproduces
nothing. So this mirrors chemcatal/skills/registry.py (_TenderBase,
TenderParams) field for field, and chemcatal/skills/tender.py::_tuning for
how each field reaches the library -- including its asymmetries:

  * dark="auto", RIXS: the paired dark is the background only when there is
    EXACTLY ONE (Measurement.pipeline). With two or more, the library falls
    back to the min-projection, silently. `background_rule()` says which.
  * dark="none", RIXS: use_dark_as_background=False -> min-projection.
  * dark="none", XES: bcg=None -> min-projection over the data files.

Normalisation ranges (e0, pre1, pre2, norm1, norm2, nnorm) are NOT job
fields yet -- chemcat's Tender jobs always normalise with larch's defaults.
They are carried here because the chat tools can apply them, and
`job_params()` reports them separately as `not_yet_job_fields` so nobody
mistakes a chat result for something a job will reproduce.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import ToolError

DEFAULT_THRESHOLDS = [60, 100, 170, 2000]
PRE_EDGE_KEYS = ("e0", "pre1", "pre2", "norm1", "norm2", "nnorm")


@dataclass(frozen=True)
class Reduction:
    """The settings that decide the per-image signal (so: what a cached RIXS
    map depends on). ROI, I0 and normalisation act on the map afterwards."""

    threshold: tuple[float, ...] | None = None
    dark: str = "auto"
    bcg_adjust: bool = True

    @classmethod
    def from_args(cls, a: dict) -> "Reduction":
        th = a.get("threshold")
        if isinstance(th, str):
            th = [x for x in th.replace(",", " ").split() if x]
        if th is not None and len(th) == 0:
            th = None
        if th is not None:
            try:
                th = tuple(float(x) for x in th)
            except (TypeError, ValueError):
                raise ToolError("threshold must be numbers") from None
            if len(th) not in (3, 4) or any(not 0 <= t <= 65535 for t in th):
                raise ToolError("threshold is 3 or 4 ADU values between 0 and 65535 "
                                "([bcg_cutoff, low, xray, hi] or [bcg_cutoff, low, hi])")
        dark = a.get("dark", "auto") or "auto"
        if dark not in ("auto", "none"):
            raise ToolError("dark is 'auto' (the paired _dark.sif) or 'none' (estimate the "
                            "background from the frames)")
        return cls(threshold=th, dark=dark, bcg_adjust=bool(a.get("bcg_adjust", True)))

    def thresholds(self):
        from tender_analysis import Thresholds
        return Thresholds.from_input(list(self.threshold) if self.threshold else None)

    def key(self) -> str:
        th = "default" if self.threshold is None else "-".join(f"{t:g}" for t in self.threshold)
        return f"th{th}_dark{self.dark}_adj{int(self.bcg_adjust)}"


@dataclass(frozen=True)
class Roi:
    central_pix: int | None = None
    n: int = 7
    i0_corr: bool = True

    @classmethod
    def from_args(cls, a: dict) -> "Roi":
        cp = a.get("central_pix")
        if cp in ("", "auto"):
            cp = None
        if cp is not None:
            cp = int(cp)
            if not 0 <= cp <= 2047:
                raise ToolError("central_pix is a detector column, 0-2047")
        n = int(a.get("n", 7) or 7)
        if not 1 <= n <= 64:
            raise ToolError("n (ROI width) is 1-64 pixels")
        return cls(central_pix=cp, n=n, i0_corr=bool(a.get("i0_corr", True)))


@dataclass(frozen=True)
class Normalisation:
    overrides: dict = field(default_factory=dict)

    @classmethod
    def from_args(cls, a: dict) -> "Normalisation":
        out = {}
        for k in PRE_EDGE_KEYS:
            v = a.get(k)
            if v is None or v == "":
                continue
            out[k] = int(v) if k == "nnorm" else float(v)
        if "nnorm" in out and not 0 <= out["nnorm"] <= 3:
            raise ToolError("nnorm is the post-edge polynomial degree, 0-3")
        return cls(overrides=out)


def background_rule(kind: str, dark: str, n_dark: int) -> str:
    """What the library will actually subtract, in words."""
    if kind == "RIXS":
        if dark == "auto" and n_dark == 1:
            return "paired dark (frame-averaged)"
        if dark == "auto":
            return (f"min-projection over the scan's frames ({n_dark} paired darks: the "
                    "library uses a dark only when there is exactly one)")
        return "min-projection over the scan's frames (dark = none)"
    if dark == "auto" and n_dark:
        return f"mean of the {n_dark} paired dark file(s)"
    return "min-projection over the measurement's frames"


def job_params(kind: str, directory: str, sample: str, measurement: str,
               red: Reduction, roi: Roi | None = None,
               norm: Normalisation | None = None) -> dict:
    """The POST /api/beamtimes/<id>/jobs body the Processing tab would send
    for these settings (tender.js::submit), so the agent can hand a user the
    exact job that reproduces what it showed them."""
    body: dict = {"skill": "tender-xes" if kind == "XES" else "tender-herfd",
                  "directory": directory, "sample": sample, "measurement": measurement,
                  "dark": red.dark, "bcg_adjust": red.bcg_adjust}
    if red.threshold is not None:
        body["threshold"] = [int(round(t)) for t in red.threshold]
    if kind != "XES" and roi is not None:
        body["n"] = roi.n
        body["i0_corr"] = roi.i0_corr
        if roi.central_pix is not None:
            body["central_pix"] = roi.central_pix
    out = {"job": body}
    if norm is not None and norm.overrides:
        out["not_yet_job_fields"] = {
            "normalisation": dict(norm.overrides),
            "note": ("Tender jobs normalise with larch's default ranges; these ranges "
                     "are applied by the chat tools only (see PLAN-chemcat-processing-tab)."),
        }
    return out
