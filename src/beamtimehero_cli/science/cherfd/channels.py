"""Per-channel diagnostics for cleaned CHERFD sweeps: counts, rates, ratios.

The reduction tools answer "what is the spectrum"; these answer "what did
each channel do", which is what you check when a spectrum looks wrong or a
detector chain is being set up:

* **Per frame and per second.** ``counter_*`` and the detector sums are
  counts in one frame, so a rate is counts / gate time, and the gate is the
  frame's own ``trig_2`` (us; the full trigger period at BL4-1). ``adc_*``
  are the mean of the input over the frame (signed counts, not volts), so
  they get per-frame statistics only, never a rate.
* **Counting noise.** For a photon-counting channel (a detector sum),
  std / sqrt(mean) is ~1 when the frame-to-frame scatter is Poisson; well
  above 1 means beam, edge structure or gating noise in the window. Pick a
  flat window (pre-edge) to read it. A V-to-F channel (the BL4-1 box
  counters on ion chambers) counts a current, not photons, so it can read
  well below 1 (0.19 on counter_1, 2026-10-11_Fe): compare it between sweeps
  and windows, not against 1.
* **Log ratios.** ln(a/b) per frame, e.g. ln(I0/I1) for sample absorbance
  and ln(I1/I2) for a reference foil. Frames where either side is <= 0 are
  left out and counted. With ``pre`` and ``post`` windows, the post - pre
  difference of each ratio's mean is the edge jump.
* **Across sweeps.** The per-sweep means as a series, their spread and the
  linear trend per sweep: beam decay, I0 drops, a detector that comes and
  goes.

Pure: arrays in, dicts out.
"""
from __future__ import annotations

import re

import numpy as np

_COUNTING_RE = re.compile(r"^(counter_\d+|xs\d{2}|r[12]_\d{2})$")
_COUNTING_SUMS = frozenset({"vortex", "roi1", "roi_double", "vortex_intensity", "xsum", "xsA", "xsB"})


def is_counting(name: str) -> bool:
    """Counts per frame (a rate makes sense), as opposed to an ADC mean."""
    return name in _COUNTING_SUMS or bool(_COUNTING_RE.match(name))


def default_channels(arrays: dict, signal: str | None = None) -> list[str]:
    """Box counters and ADCs that are not all zero, plus ``signal``."""
    out = []
    for name in sorted(arrays):
        if name.startswith(("counter_", "adc_")):
            v = np.asarray(arrays[name], float)
            v = v[np.isfinite(v)]
            if v.size and np.any(v != 0):
                out.append(name)
    if signal and signal in arrays and signal not in out:
        out.append(signal)
    return out


def parse_ratio(spec: str) -> tuple[str, str]:
    parts = [p.strip() for p in str(spec).split("/")]
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"ratio {spec!r} must be 'numerator/denominator', e.g. 'counter_1/counter_2'")
    return parts[0], parts[1]


def _window_mask(energy: np.ndarray, window) -> np.ndarray:
    m = np.isfinite(energy)
    if window is None:
        return m
    lo, hi = window
    if lo is not None:
        m &= energy >= float(lo)
    if hi is not None:
        m &= energy <= float(hi)
    return m


def _r(x, sig=6):
    return None if x is None or not np.isfinite(x) else float(f"{float(x):.{sig}g}")


def _stats(v: np.ndarray) -> dict:
    v = v[np.isfinite(v)]
    if not v.size:
        return {"frames": 0}
    return {"frames": int(v.size), "mean": _r(v.mean()), "median": _r(np.median(v)),
            "std": _r(v.std(ddof=1)) if v.size > 1 else None,
            "min": _r(v.min()), "max": _r(v.max()),
            "zero_fraction": round(float((v == 0).mean()), 4)}


def channel_summary(values: np.ndarray, gate_s: np.ndarray | None, counting: bool) -> dict:
    """Per-frame statistics, plus rate and Poisson ratio for a counting channel."""
    v = np.asarray(values, float)
    out = {"kind": "counts" if counting else "adc_mean", "per_frame": _stats(v)}
    if counting:
        pf = out["per_frame"]
        if pf.get("mean") and pf.get("std") is not None and pf["mean"] > 0:
            out["poisson_ratio"] = _r(pf["std"] / np.sqrt(pf["mean"]), 4)
        if gate_s is not None:
            g = np.asarray(gate_s, float)
            ok = np.isfinite(v) & np.isfinite(g) & (g > 0)
            rate = v[ok] / g[ok]
            out["per_second"] = {k: s for k, s in _stats(rate).items() if k != "zero_fraction"}
    return out


def log_ratio(num: np.ndarray, den: np.ndarray) -> tuple[np.ndarray, int]:
    """ln(num/den) per frame over frames where both are > 0; and how many were not."""
    a, b = np.asarray(num, float), np.asarray(den, float)
    both = np.isfinite(a) & np.isfinite(b)
    ok = both & (a > 0) & (b > 0)
    return np.log(a[ok] / b[ok]), int((both & ~ok).sum())


def sweep_channel_stats(arrays: dict, *, channels: list[str], ratios: list[str] | None = None,
                        window=None, pre=None, post=None, period_us: float | None = None) -> dict:
    """Statistics of each channel and log ratio over ``window`` (eV) of one sweep.

    ``pre`` / ``post`` are optional (lo, hi) eV windows; each ratio then
    also gets its mean in both and ``jump`` = post - pre. The gate per frame
    is ``trig_2`` when the sweep has it, else ``period_us``.
    """
    e = np.asarray(arrays["absev"], float)
    m = _window_mask(e, window)
    if not m.any():
        raise ValueError(f"no frames in the energy window {window}")
    gate = None
    if "trig_2" in arrays:
        gate = np.asarray(arrays["trig_2"], float) * 1e-6
    elif period_us:
        gate = np.full(e.shape, period_us * 1e-6)
    missing = [c for c in channels if c not in arrays]
    if missing:
        raise ValueError(f"columns not in this sweep: {missing}; available: {sorted(arrays)}")
    out = {
        "frames": int(m.sum()),
        "energy_ev": [_r(e[m].min(), 7), _r(e[m].max(), 7)],
        "gate_s": _r(np.nanmedian(gate[m]), 6) if gate is not None else None,
        "channels": {c: channel_summary(np.asarray(arrays[c], float)[m],
                                        gate[m] if gate is not None else None, is_counting(c))
                     for c in channels},
    }
    if ratios:
        out["ratios"] = {}
        for spec in ratios:
            num, den = parse_ratio(spec)
            for c in (num, den):
                if c not in arrays:
                    raise ValueError(f"ratio {spec!r}: column {c!r} not in this sweep")
            a, b = np.asarray(arrays[num], float), np.asarray(arrays[den], float)
            lr, bad = log_ratio(a[m], b[m])
            r = {"ln": _stats(lr), "frames_excluded_nonpositive": bad}
            r["ln"].pop("zero_fraction", None)
            if pre is not None and post is not None:
                lp, _ = log_ratio(a[_window_mask(e, pre)], b[_window_mask(e, pre)])
                lq, _ = log_ratio(a[_window_mask(e, post)], b[_window_mask(e, post)])
                r["pre_mean"] = _r(lp.mean()) if lp.size else None
                r["post_mean"] = _r(lq.mean()) if lq.size else None
                r["jump"] = (_r(lq.mean() - lp.mean()) if lp.size and lq.size else None)
                r["pre_frames"], r["post_frames"] = int(lp.size), int(lq.size)
            out["ratios"][f"ln({num}/{den})"] = r
    return out


def _trend(y: list[float | None]) -> dict:
    """Spread of a per-sweep series and its linear trend, relative to its mean."""
    v = np.array([np.nan if x is None else x for x in y], float)
    ok = np.isfinite(v)
    if ok.sum() == 0:
        return {"n": 0}
    vv = v[ok]
    mean = float(vv.mean())
    if vv.size == 1:
        return {"n": 1, "mean": _r(mean)}
    out = {"n": int(ok.sum()), "mean": _r(mean), "min": _r(vv.min()), "max": _r(vv.max()),
           "first": _r(vv[0]), "last": _r(vv[-1])}
    if mean:
        out["spread_fraction"] = _r((vv.max() - vv.min()) / abs(mean), 4)
        out["last_vs_first_fraction"] = _r((vv[-1] - vv[0]) / abs(mean), 4)
    if ok.sum() >= 3:
        slope = float(np.polyfit(np.flatnonzero(ok), vv, 1)[0])
        out["slope_per_sweep"] = _r(slope)
        if mean:
            out["slope_fraction_per_sweep"] = _r(slope / abs(mean), 4)
    return out


def run_channel_summary(per_sweep: list[dict]) -> dict:
    """Across-sweep series of each channel's mean (per frame, per second) and each ratio.

    ``per_sweep`` items are ``sweep_channel_stats`` results in acquisition order.
    """
    if not per_sweep:
        return {}
    out: dict = {"channels": {}, "ratios": {}}
    for c in per_sweep[0]["channels"]:
        series = [s["channels"][c] for s in per_sweep]
        entry = {"per_frame_mean": _trend([x["per_frame"].get("mean") for x in series])}
        if any("per_second" in x for x in series):
            entry["per_second_mean"] = _trend([x.get("per_second", {}).get("mean") for x in series])
        out["channels"][c] = entry
    for name in (per_sweep[0].get("ratios") or {}):
        series = [s["ratios"][name] for s in per_sweep]
        entry = {"ln_mean": _trend([x["ln"].get("mean") for x in series])}
        if "jump" in series[0]:
            entry["jump"] = _trend([x.get("jump") for x in series])
        out["ratios"][name] = entry
    return out


# CITATIONS — method -> reference. ``None`` = implemented, not yet attributed.
CITATIONS = {
    "frame gate time from trig_2 (us)": "cherfd_claude cscan_box/data_frame.py parse_frame",
}
