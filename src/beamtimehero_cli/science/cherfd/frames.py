"""From raw FPGA/detector frames to a clean, trigger-ordered sweep.

A cherfd ``_dataframe.pkl`` is not analysis-ready. What it actually holds,
and what this module does about each:

1. **Rows are not in time order.** Each 0.2 s DMA chunk is parsed in a
   scrambled order (trig_1 reads 10000, 40000, 30000, 20000, ...). trig_1 is
   the trigger end time in microseconds, so ``round(trig_1 / period_us)`` is
   the 1-based trigger number: rows are re-ordered by it.
2. **The detector was merged by row index.** ``CherfdController
   ._merge_detector_data`` pastes Xspress3 frame *i* onto data row *i*, but
   the Xspress3 frames arrive in trigger order and the rows do not, so the
   recorded pairing is off by up to +/-4 triggers (measured on
   2025-10_Sokaras). ``align="trigger"`` (default) pairs detector frame *k*
   with trigger *k*; ``align="row"`` keeps the recorded pairing for
   comparison.
3. **Status-report frames** (trig_1 bit 31) are not filtered upstream. Dropped.
4. **Integer columns are object dtype**; the caller coerces to float before
   calling here (this module takes plain arrays).
5. The **last row** is routinely all-NaN (the detector has one frame more
   than the scan), and the first DMA chunk may be missing (the first
   ``.DATA`` callback is discarded as stale). Both show up as missing
   triggers, which is reported rather than hidden.
6. **Corrupt energies** far outside the commanded range are dropped.

Pure: arrays in, dict out.
"""
from __future__ import annotations

import numpy as np

from beamtimehero_cli.science.cherfd import policy

#: Columns that are FPGA ADC channels (offset-binary).
def is_adc(name: str) -> bool:
    return name.startswith("adc_")


def adc_signed(values: np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=float) - policy.ADC_OFFSET


def infer_period_us(trig_1: np.ndarray, trig_2: np.ndarray | None = None) -> float | None:
    """Trigger period in us: trig_2 (duration) is ~period-1ms; spacing of trig_1 is exact."""
    t = np.asarray(trig_1, dtype=float)
    t = np.sort(t[np.isfinite(t) & (t > 0)])
    if t.size >= 3:
        d = np.diff(t)
        d = d[d > 0]
        if d.size:
            return float(np.median(d))
    if trig_2 is not None:
        t2 = np.asarray(trig_2, dtype=float)
        t2 = t2[np.isfinite(t2) & (t2 > 0)]
        if t2.size:
            return float(np.median(t2))
    return None


def clean_sweep(
    columns: dict[str, np.ndarray],
    *,
    detector: dict[str, np.ndarray] | None = None,
    period_us: float | None = None,
    energy_range: tuple[float, float] | None = None,
    align: str = "trigger",
    detector_frame_offset: int = policy.DETECTOR_FRAME_OFFSET,
) -> dict:
    """Return trigger-ordered arrays plus a QC ledger.

    ``columns`` holds the FPGA columns (must include ``trig_1`` and
    ``absev``) as float arrays in recorded row order. ``detector`` holds
    Xspress3 columns (e.g. ``vortex``) in *detector frame order* -- which is
    what the recorded ``vortex`` column is, since it was pasted by index.
    """
    if align not in ("trigger", "row"):
        raise ValueError("align must be 'trigger' or 'row'")
    if "trig_1" not in columns or "absev" not in columns:
        raise ValueError("sweep needs trig_1 and absev columns")
    n_rows = len(columns["trig_1"])
    trig_1 = np.asarray(columns["trig_1"], dtype=float)
    finite = np.isfinite(trig_1)
    t_int = np.zeros(n_rows, dtype=np.int64)
    t_int[finite] = trig_1[finite].astype(np.int64)
    status = finite & (((t_int >> policy.STATUS_FRAME_BIT) & 1) == 1)
    valid = finite & ~status & (t_int > 0)

    energy = np.asarray(columns["absev"], dtype=float)
    if energy_range is not None:
        lo, hi = sorted(energy_range)
        m = policy.ENERGY_OUTLIER_MARGIN_EV
        out_of_range = valid & ((energy < lo - m) | (energy > hi + m) | ~np.isfinite(energy))
    else:
        out_of_range = valid & ~np.isfinite(energy)
    keep = valid & ~out_of_range

    if period_us is None:
        period_us = infer_period_us(trig_1[keep], columns.get("trig_2"))
    if not period_us:
        raise ValueError("cannot infer trigger period (no valid trig_1 values)")

    trig_no = np.rint(t_int / period_us).astype(np.int64)      # 1-based trigger number
    rows = np.flatnonzero(keep)
    order = rows[np.argsort(trig_no[rows], kind="stable")]
    tn = trig_no[order]
    dup_mask = np.r_[False, np.diff(tn) == 0]
    order, tn = order[~dup_mask], tn[~dup_mask]

    out = {name: np.asarray(v, dtype=float)[order] for name, v in columns.items()}
    out["trigger"] = tn.astype(float)

    displacement = None
    det_stats = {}
    if detector:
        n_det = min(len(v) for v in detector.values())
        if align == "trigger":
            idx = tn - 1 + int(detector_frame_offset)
        else:
            idx = order.copy()
        ok = (idx >= 0) & (idx < n_det)
        for name, v in detector.items():
            arr = np.full(len(tn), np.nan)
            arr[ok] = np.asarray(v, dtype=float)[idx[ok]]
            out[name] = arr
        displacement = (order - (tn - 1)).astype(int)
        det_stats = {
            "detector_frames": int(n_det),
            "detector_frames_unmatched": int((~ok).sum()),
            "recorded_pairing_max_offset_frames": int(np.abs(displacement).max()) if len(displacement) else 0,
            "recorded_pairing_mean_abs_offset_frames": round(float(np.abs(displacement).mean()), 3) if len(displacement) else 0.0,
            "recorded_pairing_fraction_misaligned": round(float((displacement != 0).mean()), 4) if len(displacement) else 0.0,
        }

    expected = int(tn.max()) if tn.size else 0
    missing = expected - int(tn.size)
    gaps = np.diff(tn) if tn.size > 1 else np.array([])
    first_missing = int(tn.min() - 1) if tn.size else 0
    e_sorted = out["absev"]
    backsteps = int(np.sum(np.diff(e_sorted) * np.sign(e_sorted[-1] - e_sorted[0]) < 0)) if e_sorted.size > 2 else 0

    qc = {
        "rows": int(n_rows),
        "frames_kept": int(tn.size),
        "status_frames_dropped": int(status.sum()),
        "nan_rows_dropped": int((~finite).sum()),
        "energy_outliers_dropped": int(out_of_range.sum()),
        "duplicate_triggers_dropped": int(dup_mask.sum()),
        "period_us": float(period_us),
        "frame_rate_hz": round(1e6 / period_us, 4),
        "last_trigger": expected,
        "missing_triggers": int(missing),
        "missing_leading_triggers": first_missing,
        "largest_trigger_gap": int(gaps.max()) if gaps.size else 0,
        "missing_fraction": round(missing / expected, 5) if expected else None,
        "energy_backsteps": backsteps,
        "alignment": align,
        **det_stats,
    }
    return {"arrays": out, "qc": qc}


def sweep_quality(clean: dict, *, signal: str, expected_range: tuple[float, float] | None = None,
                  expected_freq_hz: float | None = None) -> dict:
    """Verdict + issues for one cleaned sweep."""
    a, qc = clean["arrays"], clean["qc"]
    issues: list[dict] = []

    def add(level: str, code: str, msg: str) -> None:
        issues.append({"level": level, "code": code, "message": msg})

    if qc["frames_kept"] == 0:
        add("fail", "no_frames", "no usable frames")
    if qc.get("missing_fraction") and qc["missing_fraction"] > policy.DROPPED_FRAME_WARN_FRACTION:
        add("warn", "dropped_frames",
            f"{qc['missing_triggers']} of {qc['last_trigger']} triggers missing "
            f"({qc['missing_fraction']:.2%}); leading gap {qc['missing_leading_triggers']}")
    if qc["status_frames_dropped"]:
        add("info", "status_frames", f"{qc['status_frames_dropped']} FPGA status frames dropped")
    if qc["energy_outliers_dropped"]:
        add("warn", "energy_outliers", f"{qc['energy_outliers_dropped']} frames with corrupt energy dropped")
    if expected_freq_hz and abs(qc["frame_rate_hz"] - expected_freq_hz) / expected_freq_hz > 0.02:
        add("warn", "frame_rate", f"frame rate {qc['frame_rate_hz']} Hz vs commanded {expected_freq_hz} Hz")
    if qc["energy_backsteps"] > max(3, 0.01 * qc["frames_kept"]):
        add("warn", "energy_backsteps",
            f"energy steps backwards {qc['energy_backsteps']} times in trigger order (jitter or backlash)")

    sig = a.get(signal)
    sig_stats = None
    if sig is None:
        add("fail", "missing_signal", f"signal column {signal!r} not present")
    else:
        s = sig[np.isfinite(sig)]
        sig_stats = {
            "column": signal, "finite": int(s.size),
            "mean": float(s.mean()) if s.size else None,
            "max": float(s.max()) if s.size else None,
            "zero_fraction": round(float((s == 0).mean()), 4) if s.size else None,
        }
        if s.size == 0 or np.all(s == 0):
            hint = (" -- the Xspress3 was probably still owned by SPEC (run `xtc 1` in SPEC "
                    "before cherfd scans)") if signal == "vortex" else ""
            add("fail", "signal_all_zero", f"signal {signal!r} is all zero{hint}")
        elif sig_stats["zero_fraction"] > 0.2:
            add("warn", "signal_zeros", f"{sig_stats['zero_fraction']:.0%} of {signal!r} frames are zero")
    if qc.get("detector_frames_unmatched"):
        add("warn", "detector_unmatched",
            f"{qc['detector_frames_unmatched']} triggers had no detector frame")

    e = a["absev"]
    coverage = None
    if e.size:
        coverage = {"e_min": float(np.nanmin(e)), "e_max": float(np.nanmax(e))}
        if expected_range:
            lo, hi = sorted(expected_range)
            short = max(coverage["e_min"] - lo, hi - coverage["e_max"], 0.0)
            coverage["shortfall_ev"] = round(short, 3)
            if short > 2.0:
                add("warn", "incomplete_range",
                    f"sweep covers {coverage['e_min']:.1f}-{coverage['e_max']:.1f} eV of "
                    f"{lo:g}-{hi:g} eV (partial or stopped sweep?)")

    level = "ok"
    if any(i["level"] == "fail" for i in issues):
        level = "fail"
    elif any(i["level"] == "warn" for i in issues):
        level = "warn"
    return {"verdict": level, "issues": issues, "signal": sig_stats, "coverage": coverage}


def gap_tracking(energy: np.ndarray, gap_mm: np.ndarray, ideal_gap_fn) -> dict:
    """Measured gap vs the ideal harmonic gap at each frame's energy.

    ``ideal_gap_fn(E) -> mm`` (beamline.harmonic_gap based). The constant part
    of the residual is the gap calibration offset in effect; the scatter
    around it is the tracking error the predictive loop left behind.
    """
    e = np.asarray(energy, float)
    g = np.asarray(gap_mm, float)
    m = np.isfinite(e) & np.isfinite(g)
    if m.sum() < 3:
        return {"ok": False, "error": "fewer than 3 frames carry a gap reading",
                "frames_with_gap": int(m.sum())}
    ideal = np.array([ideal_gap_fn(x) for x in e[m]], float)
    r = g[m] - ideal
    offset = float(np.median(r))
    scatter = r - offset
    return {
        "ok": True,
        "frames_with_gap": int(m.sum()),
        "implied_calibration_offset_mm": round(offset, 5),
        "residual_rms_mm": round(float(np.sqrt(np.mean(scatter ** 2))), 5),
        "residual_max_mm": round(float(np.max(np.abs(scatter))), 5),
        "tracking_ok": bool(np.sqrt(np.mean(scatter ** 2)) <= policy.GAP_TRACKING_WARN_MM),
        "gap_range_mm": [float(g[m].min()), float(g[m].max())],
    }

# CITATIONS — method -> reference. ``None`` = implemented, not yet attributed.
CITATIONS = {
    "DMA frame layout (trig_1 end time, status bit 31)": "cherfd_claude cscan_box/references/DMA_structure_definition.txt",
    "trigger-number re-pairing of Xspress3 frames": None,
}
