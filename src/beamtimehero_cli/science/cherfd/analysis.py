"""Run-level CHERFD analysis: direction offsets, sweep drift, energy calibration.

Continuous scanning adds failure modes that step scans do not have, and
these functions exist to catch them:

* **fwd/rev offset** -- the encoder is latched at the trigger *end*, so any
  lag between the encoder latch and the detector gate shifts forward and
  reverse sweeps in opposite directions. Half the fwd-rev shift is the
  per-direction energy error; merging both directions without correcting it
  broadens every feature by the full shift.
* **drift across sweeps** -- edge position creep (mono heat load, encoder
  calibration drift) and intensity decay (beam, sample damage) over a run.
* **energy calibration** -- measured E0 against a reference, converted into
  the Bragg-angle correction the encoder calibration would need.

E0 uses beamtimehero_cli's ``science.xas.e0.find_e0`` (derivative maximum),
so the number means the same thing it means in every other bth XAS tool.
"""
from __future__ import annotations

import math

import numpy as np
from beamtimehero_cli.science.xas.e0 import find_e0

from beamtimehero_cli.science.cherfd import beamline, policy, reduce


def _e0(energy, values) -> dict | None:
    e = np.asarray(energy, float)
    y = np.asarray(values, float)
    m = np.isfinite(e) & np.isfinite(y)
    if m.sum() < 15:
        return None
    try:
        r = find_e0(e[m], reduce.minmax(y[m]), smooth_span_ev=policy.E0_SMOOTH_SPAN_EV)
    except Exception:  # noqa: BLE001 -- a spectrum without an edge has no E0
        return None
    return {"e0_ev": round(r["e0_ev"], 4), "e0_unc_ev": round(r["e0_unc_ev"], 4)}


def compare_directions(fwd: list[dict], rev: list[dict], *, signal: str, normalize_by: str | None,
                       bin_ev: float, e_min=None, e_max=None) -> dict:
    if not fwd or not rev:
        raise ValueError("need at least one fwd and one rev sweep")
    lo = e_min if e_min is not None else max(
        min(float(np.nanmin(s["arrays"]["absev"])) for s in fwd),
        min(float(np.nanmin(s["arrays"]["absev"])) for s in rev))
    hi = e_max if e_max is not None else min(
        max(float(np.nanmax(s["arrays"]["absev"])) for s in fwd),
        max(float(np.nanmax(s["arrays"]["absev"])) for s in rev))
    bf = reduce.bin_sweeps(fwd, signal=signal, normalize_by=normalize_by, bin_ev=bin_ev, e_min=lo, e_max=hi)
    br = reduce.bin_sweeps(rev, signal=signal, normalize_by=normalize_by, bin_ev=bin_ev, e_min=lo, e_max=hi)
    shift = reduce.best_shift(bf["energy"], bf["merged"], br["energy"], br["merged"])
    e0f, e0r = _e0(bf["energy"], bf["merged"]), _e0(br["energy"], br["merged"])
    d = shift["shift_ev"]
    steps = [np.median(np.abs(np.diff(np.asarray(x["arrays"]["absev"], float)))) for x in fwd + rev]
    ev_per_frame = float(np.median(steps)) if steps else None
    verdict = ("consistent" if abs(d) < policy.DIRECTION_OFFSET_WARN_EV else
               "offset: correct before merging directions")
    level_ratio = float(np.nanmean(br["merged"]) / np.nanmean(bf["merged"])) if np.nanmean(bf["merged"]) else None
    return {
        "n_fwd": len(fwd), "n_rev": len(rev), "window_ev": [lo, hi], "bin_ev": bin_ev,
        "rev_minus_fwd_shift_ev": d,
        "per_direction_error_ev": round(d / 2.0, 4),
        "shift_in_frames": round(d / ev_per_frame, 2) if ev_per_frame else None,
        "ev_per_frame": round(ev_per_frame, 5) if ev_per_frame else None,
        "suggested_direction_correction_ev": round(d, 4),
        "shift_fit": shift,
        "e0_fwd": e0f, "e0_rev": e0r,
        "e0_rev_minus_fwd_ev": round(e0r["e0_ev"] - e0f["e0_ev"], 4) if (e0f and e0r) else None,
        "rev_over_fwd_intensity": round(level_ratio, 4) if level_ratio else None,
        "verdict": verdict,
        "interpretation": (
            "A positive shift means reverse sweeps read features at higher energy than "
            "forward ones. Both directions are symmetric about the true energy if the "
            "cause is a fixed latch/gate lag; the true edge is then near the fwd/rev mean. "
            "Pass suggested_direction_correction_ev as direction_correction_ev to "
            "cherfd_merge_sweeps / cherfd_export_merged to merge both directions on that mean. "
            "A shift that is a stable number of frames across runs points at detector-gate vs "
            "encoder-latch timing; one that varies with speed or energy points elsewhere."
        ),
        "_fwd": bf, "_rev": br,
    }


def apply_direction_correction(sweeps: list[dict], correction_ev: float) -> list[dict]:
    """Shift fwd energies by +c/2 and rev by -c/2 (c = rev-minus-fwd feature shift).

    Assumes the two directions err symmetrically about the true energy, which
    is what a fixed timing lag produces. Returns new sweep dicts; inputs are
    not modified.
    """
    out = []
    for s in sweeps:
        sign = -1.0 if s.get("direction") == "rev" else 1.0
        a = dict(s["arrays"])
        a["absev"] = np.asarray(a["absev"], float) + sign * correction_ev / 2.0
        out.append({**s, "arrays": a})
    return out


def sweep_drift(sweeps: list[dict], *, signal: str, normalize_by: str | None,
                bin_ev: float, e_min=None, e_max=None) -> dict:
    """Per-sweep E0, shift against the first sweep, and mean level, with trends.

    Directions are analysed separately (a fwd/rev offset is not drift).
    """
    if not sweeps:
        raise ValueError("no sweeps")
    b = reduce.bin_sweeps(sweeps, signal=signal, normalize_by=normalize_by, bin_ev=bin_ev,
                          e_min=e_min, e_max=e_max)
    rows = []
    refs: dict[str, tuple] = {}
    for s, ps in zip(sweeps, b["per_sweep"]):
        d = s.get("direction", "fwd")
        v, ev = ps["values"], ps["energy"]
        if d not in refs:
            refs[d] = (ev, v)
        try:
            sh = 0.0 if refs[d][1] is v else reduce.best_shift(refs[d][0], refs[d][1], ev, v)["shift_ev"]
        except ValueError:
            sh = None
        e0 = _e0(ev, v)
        rows.append({"sweep": s.get("label"), "direction": d, "index": s.get("sweep"),
                     "e0_ev": e0["e0_ev"] if e0 else None,
                     "shift_vs_first_ev": sh,
                     "mean_level": float(np.nanmean(v)) if np.isfinite(v).any() else None,
                     "noise": reduce.noise_estimate(v)})
    trends = {}
    flags = []
    for d in sorted({r["direction"] for r in rows}):
        rs = [r for r in rows if r["direction"] == d]
        x = np.array([r["index"] for r in rs], float)
        shift_t = reduce.linear_trend(x, np.array([r["shift_vs_first_ev"] if r["shift_vs_first_ev"] is not None else np.nan for r in rs]))
        level_t = reduce.linear_trend(x, np.array([r["mean_level"] if r["mean_level"] is not None else np.nan for r in rs]))
        trends[d] = {"energy_shift": shift_t, "mean_level": level_t}
        if shift_t and abs(shift_t["total_change"]) > policy.EDGE_DRIFT_WARN_EV:
            flags.append(f"{d}: features drift {shift_t['total_change']:+.3f} eV across the run")
        if level_t and rs[0]["mean_level"]:
            frac = level_t["total_change"] / rs[0]["mean_level"]
            level_t["relative_change"] = round(frac, 4)
            if abs(frac) > policy.INTENSITY_DRIFT_WARN_FRACTION:
                cause = ("normalized signal changes: sample change or damage" if normalize_by else
                         "unnormalized: could be ring current/I0 -- set normalize_by to separate it")
                flags.append(f"{d}: mean level changes {frac:+.1%} across the run ({cause})")
    return {"sweeps": rows, "trends": trends, "flags": flags, "bin_ev": bin_ev,
            "verdict": "drift detected" if flags else "stable"}


def energy_calibration(energy, values, reference_e0_ev: float | None, crystal: str | None = None) -> dict:
    e0 = _e0(energy, values)
    if e0 is None:
        raise ValueError("could not locate an edge (E0) in the merged spectrum")
    out = {"measured_e0_ev": e0["e0_ev"], "e0_unc_ev": e0["e0_unc_ev"],
           "e0_definition": "max of first derivative (beamtimehero find_e0)"}
    if reference_e0_ev is None:
        out["note"] = "give reference_e0_ev (or element/edge) to get a correction"
        return out
    delta = e0["e0_ev"] - float(reference_e0_ev)
    th_meas = beamline.bragg_angle_deg(e0["e0_ev"], crystal)
    th_ref = beamline.bragg_angle_deg(float(reference_e0_ev), crystal)
    dtheta = th_ref - th_meas
    out.update({
        "reference_e0_ev": float(reference_e0_ev),
        "measured_minus_reference_ev": round(delta, 4),
        "bragg_angle_correction_deg": dtheta,
        "encoder_ticks_equivalent": round(dtheta / beamline.MONO_ENCODER_RESOLUTION, 1),
        "interpretation": (
            "Add bragg_angle_correction_deg to the mono calibration offset (mono_xtal) to map "
            "the measured edge onto the reference. A constant angle offset is not a constant eV "
            "offset: the eV error grows as E/tan(theta) away from this edge."
        ),
        "caveat": ("Tabulated edge energies are for elemental foils; a compound's edge "
                   "is chemically shifted. Use a measured reference foil value where possible."),
        "significant": abs(delta) > max(3 * e0["e0_unc_ev"], 0.1) and math.isfinite(delta),
    })
    return out

# CITATIONS — method -> reference. ``None`` = implemented, not yet attributed.
CITATIONS = {
    "E0 as derivative maximum": "science.xas.e0.find_e0",
    "Bragg-angle calibration correction": "Bragg's law; cherfd_claude utils/mono.py",
}
