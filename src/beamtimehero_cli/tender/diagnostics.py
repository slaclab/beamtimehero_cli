"""The checks the Tender story says nothing in the pipeline makes.

Each returns plain numbers plus a short verdict string, so the agent can
quote evidence rather than an opinion. The thresholds are stated where they
are used and come from the story's own measurements (chemcatal
static/docs/tender-story.html, chapters 2-4, 8-11):

  * a real dark is flat to ~1 ADU per pixel down a column; the bundled
    Na2SO4 series' "dark" carries the emission band at up to 84 ADU;
  * the pedestal's read-out noise is ~3 ADU; an S Ka photon is ~230 ADU,
    an Ag L photon ~333 ADU, against the default xray gate of 170;
  * the automatic ROI centre is one gaussian over the last 10 points, which
    lands in the valley of a Ka1/Ka2 doublet;
  * larch's default post-edge range can double the edge step of a scan that
    ends ~50 eV above the edge.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import find_peaks

# -------------------------------------------------------------- one frame


def pedestal(frame: np.ndarray) -> dict:
    """Common mode (refined, as the pipeline uses it) and read-out noise."""
    from tender_analysis.common import adu_histogram, common_mode

    cm = float(common_mode(frame, refine=True))
    h = adu_histogram(frame).astype(float)
    peak = int(round(cm))
    lo, hi = max(0, peak - 20), min(h.size, peak + 21)
    x, y = np.arange(lo, hi), h[lo:hi]
    # Noise from the left half-width of the pedestal peak: the right side is
    # where the photons are, so it overstates it.
    half = y.max() / 2
    left = x[(y >= half) & (x <= peak)]
    sigma = float((peak - left.min()) / 1.1774) if left.size else float("nan")
    within = float(np.mean(np.abs(frame - cm) < 5 * sigma)) if sigma == sigma else float("nan")
    return {"common_mode_adu": round(cm, 2), "noise_sigma_adu": round(sigma, 2),
            "fraction_within_5sigma": round(within, 4)}


def column_profile(frame: np.ndarray, cm: float, smooth: int = 9) -> np.ndarray:
    """Mean ADU above the pedestal per column (averaged down the 512 rows)."""
    prof = (frame - cm).mean(axis=0)
    k = np.ones(smooth) / smooth
    return np.convolve(prof, k, mode="same")


def grain_peak(hist_xray: np.ndarray, thresholds) -> dict:
    """Where single photons sit in the per-grain histogram vs the xray gate."""
    h = np.asarray(hist_xray, dtype=float)
    xray, hi = float(thresholds.xray), float(thresholds.hi)
    lo_b, hi_b = int(max(20, 0.5 * xray)), int(min(h.size - 1, hi))
    seg = np.convolve(h, np.ones(15) / 15, mode="same")[lo_b:hi_b]
    if seg.size == 0 or seg.max() <= 0:
        return {"photon_peak_adu": None, "verdict": "no grains above the noise: no photons "
                "(shutter closed, beam lost, or thresholds far too high)"}
    peak = lo_b + int(np.argmax(seg))
    total = h.sum()
    rejected = float(h[: int(xray)].sum() / total) if total else float("nan")
    out = {"photon_peak_adu": peak, "xray_gate_adu": xray,
           "gate_over_peak": round(xray / peak, 2),
           "fraction_of_grains_below_gate": round(rejected, 3)}
    if peak < xray:
        out["verdict"] = ("the xray gate is ABOVE the single-photon peak: most real photons "
                          "are being thrown away; lower xray")
    elif xray > 0.8 * peak:
        out["verdict"] = ("the xray gate is close to the photon peak: it is cutting into real "
                          "photons; consider lowering it")
    else:
        out["verdict"] = "the photon peak sits clearly above the xray gate"
    return out


# ------------------------------------------------------------ measurement


def i0_check(i0: list[float]) -> dict:
    a = np.asarray([np.nan if v is None else v for v in i0], dtype=float)
    bad = [int(i) for i in np.flatnonzero(~np.isfinite(a) | (a == 0))]
    good = a[np.isfinite(a) & (a > 0)]
    out = {"n": int(a.size), "missing_or_zero_idx": bad}
    if good.size:
        out.update(min=float(good.min()), max=float(good.max()),
                   change_pct=round(100 * (good.max() - good.min()) / good.min(), 1))
    # a point >20 % below its neighbours' median: beam dump / refill / shutter
    drops = []
    for i in range(a.size):
        nb = a[max(0, i - 3): i + 4]
        nb = nb[np.isfinite(nb) & (nb > 0)]
        if np.isfinite(a[i]) and a[i] > 0 and nb.size >= 3 and a[i] < 0.8 * np.median(nb):
            drops.append(i)
    out["sudden_drop_idx"] = drops
    notes = []
    if bad:
        notes.append(f"{len(bad)} point(s) with I0 = 0 or missing: the library silently "
                     "treats them as I0 = 1, so each becomes a spike or dip when I0 "
                     "correction is on")
    if drops:
        notes.append(f"{len(drops)} point(s) with a sudden I0 drop (beam dump / refill?): "
                     "corrected, but noisier")
    out["verdict"] = "; ".join(notes) or "I0 is present and smooth"
    return out


def energy_check(rows: list[dict]) -> dict:
    """mono (header) vs the filename energy, per file."""
    mism, nomono = [], []
    for r in rows:
        if r.get("mono_eV") is None:
            nomono.append(r["file"])
        elif r.get("name_eV") is not None and abs(r["mono_eV"] - r["name_eV"]) > 0.5:
            mism.append({"file": r["file"], "mono_eV": r["mono_eV"], "name_eV": r["name_eV"]})
    e = [r["mono_eV"] if r.get("mono_eV") is not None else r.get("name_eV") for r in rows]
    e = [x for x in e if x is not None]
    dup = sorted({x for x in e if e.count(x) > 1})
    out = {"no_mono_in_header": nomono, "mono_vs_name_mismatch": mism,
           "duplicate_energies": dup}
    notes = []
    if nomono:
        notes.append(f"{len(nomono)} file(s) without a mono value: their energy falls back "
                     "to the filename")
    if mism:
        notes.append(f"{len(mism)} file(s) whose header mono disagrees with the filename by "
                     ">0.5 eV")
    if dup:
        notes.append(f"repeated incident energies {dup[:5]}")
    out["verdict"] = "; ".join(notes) or "header and filename energies agree"
    return out


def dark_check(dark_frame: np.ndarray, data_frame: np.ndarray, dark_hdr: dict,
               data_hdr: dict) -> dict:
    """Is the paired 'dark' actually dark?

    A real dark is the pedestal plus the detector's fixed pattern: its column
    profile above the pedestal is flat to ~1 ADU. A dark taken with the beam
    on the sample carries the SAME emission band as the data, and
    subtracting it removes real photons from every image, non-uniformly (the
    story's chapter 3: the white line lost 62 % of its counts and came out at
    7.81 edge steps instead of 3.50).
    """
    from tender_analysis.common import common_mode

    cm_d = common_mode(dark_frame, refine=True)
    cm_x = common_mode(data_frame, refine=True)
    pd_ = column_profile(dark_frame, cm_d)
    px = column_profile(data_frame, cm_x)
    base_d = float(np.median(pd_))
    exc_d = pd_ - base_d
    exc_x = px - float(np.median(px))
    peak_d = float(exc_d.max())
    band = exc_x > 0.3 * exc_x.max() if exc_x.max() > 0 else np.zeros_like(exc_x, bool)
    r = (float(np.corrcoef(exc_d, exc_x)[0, 1])
         if exc_d.std() > 0 and exc_x.std() > 0 else 0.0)
    ratio = (float(exc_d[band].mean() / exc_x[band].mean())
             if band.any() and exc_x[band].mean() > 0 else float("nan"))
    out = {"dark_peak_excess_adu_per_px": round(peak_d, 2),
           "dark_vs_data_profile_correlation": round(r, 3),
           "dark_band_level_vs_data_band_level": (None if math.isnan(ratio)
                                                  else round(ratio, 3)),
           "dark_header": {k: dark_hdr.get(k) for k in ("mono_eV", "I0", "exptime_s")},
           "data_header": {k: data_hdr.get(k) for k in ("file", "mono_eV", "I0", "exptime_s")}}
    if peak_d <= 2.0:
        out["verdict"] = "dark"
        out["meaning"] = "flat to within ~2 ADU per pixel: a real dark"
    elif peak_d > 5.0 and r > 0.5:
        out["verdict"] = "lit"
        out["meaning"] = (f"the 'dark' carries the data's emission band (up to {peak_d:.0f} "
                          f"ADU/px, correlation {r:.2f} with the data's profile): it was "
                          "taken with beam on the sample. Subtracting it removes real "
                          "photons and reshapes every spectrum. Reduce with dark='none' "
                          "(min-projection background) instead.")
    elif peak_d > 5.0:
        out["verdict"] = "structured"
        out["meaning"] = (f"not flat (up to {peak_d:.0f} ADU/px) but not the data's emission "
                          "band: hot columns or stray light; compare a reduction with "
                          "dark='none'")
    else:
        out["verdict"] = "marginal"
        out["meaning"] = (f"slightly above flat ({peak_d:.1f} ADU/px); probably harmless, "
                          "compare dark='auto' against dark='none' if heights matter")
    ex_d, ex_x = dark_hdr.get("exptime_s"), data_hdr.get("exptime_s")
    if ex_d and ex_x and abs(ex_d - ex_x) > 1e-6:
        out["exposure_mismatch"] = (f"dark exposure {ex_d:g} s vs data {ex_x:g} s: the "
                                    "fixed pattern does not scale with the pedestal")
    return out


# ------------------------------------------------------------ RIXS / HERFD


def emission_peaks(profile: np.ndarray, centre: int) -> dict:
    """Peaks of the emission profile the auto-centre was fitted to, and
    whether the fitted centre sits in a valley between two of them."""
    p = np.convolve(np.asarray(profile, float), np.ones(9) / 9, mode="same")
    base = float(np.median(p))
    q = p - base
    if q.max() <= 0:
        return {"peaks": [], "verdict": "no emission line in the profile"}
    # 5 %: the Na2SO4 Ka2 peak rises only ~7 % of the Ka1 height out of the
    # valley between them, but is a real line (ratio 0.67, 58 px apart).
    idx, props = find_peaks(q, prominence=0.05 * q.max(), distance=15)
    order = np.argsort(q[idx])[::-1][:4]
    idx = idx[order]
    top = float(q[idx[0]]) if idx.size else float(q.max())
    peaks = [{"pixel": int(i), "height_rel": round(float(q[i] / top), 3)} for i in idx]
    out = {"peaks": peaks, "auto_centre": int(centre),
           "profile_at_centre_rel": round(float(q[int(centre)] / top), 3)}
    if len(peaks) >= 2 and abs(peaks[0]["pixel"] - peaks[1]["pixel"]) < 200 \
            and peaks[1]["height_rel"] > 0.3:
        a, b = sorted((peaks[0]["pixel"], peaks[1]["pixel"]))
        out["doublet"] = {"pixels": [a, b], "separation_px": b - a,
                          "ratio_lower_to_higher": round(
                              min(peaks[0]["height_rel"], peaks[1]["height_rel"]), 3)}
        if a + 8 < centre < b - 8:
            out["verdict"] = (f"the automatic centre ({centre}) sits BETWEEN two emission "
                              f"peaks at {a} and {b} (a doublet, e.g. Ka2/Ka1), on neither: "
                              "one gaussian over a doublet lands in the valley. Choose "
                              "central_pix on purpose (compare_centres='peaks' shows what "
                              "each choice does) and use the same for every sample compared.")
        else:
            out["verdict"] = (f"emission doublet at {a} and {b}; the automatic centre "
                              f"({centre}) is on/near a peak")
    else:
        out["verdict"] = "single emission peak; the automatic centre is on it" if \
            peaks and abs(peaks[0]["pixel"] - centre) < 10 else \
            f"automatic centre {centre}; main peak at {peaks[0]['pixel'] if peaks else '?'}"
    return out


def normalisation_check(E, mu, overrides: dict | None = None) -> dict:
    """Normalise, then say whether the edge step can be trusted."""
    from tender_analysis import normalize_mu

    E = np.asarray(E, float)
    mu = np.asarray(mu, float)
    res = normalize_mu(E, mu, overrides or None)
    out = {"method": res.method, "overrides": dict(overrides or {})}
    if overrides and "fallback" in res.method:
        out["overrides_ignored"] = ("neither chemcatal.xas_core nor xraylarch is importable, "
                                    "and the vendored fallback takes no ranges: these numbers "
                                    "are NOT the portal's")
    if not np.isfinite(res.e0):
        out["verdict"] = "not normalised: " + res.method
        return out, res
    tail = np.asarray(res.norm)[-3:]
    end = float(np.nanmean(tail))
    span = float(E.max() - res.e0)
    out.update(e0_eV=round(float(res.e0), 2), edge_step=float(res.edge_step),
               post_edge_span_eV=round(span, 1), norm_at_scan_end=round(end, 3),
               white_line_height_edge_steps=round(float(np.nanmax(res.norm)), 3))
    notes = []
    if abs(end - 1) > 0.15:
        notes.append(f"'norm' ends at {end:.2f}, not ~1: the edge step is wrong, and so is "
                     "every normalised height")
    if span < 60:
        notes.append(f"the scan ends {span:.0f} eV above E0: larch's default post-edge "
                     "range (~E0+15 to E0+50 eV) starts on the near-edge resonances")
    out["verdict"] = "; ".join(notes) or "edge step looks sound (norm ends near 1)"
    return out, res


def suggest_post_edge(E, mu, e0: float) -> list[dict]:
    """Try a few post-edge starts (linear post-edge) and report where 'norm'
    ends: evidence for choosing a range, not a choice."""
    from tender_analysis import normalize_mu

    E = np.asarray(E, float)
    span = float(E.max() - e0)
    if "fallback" in normalize_mu(E, np.asarray(mu, float)).method:
        return []  # the fallback ignores ranges: every row would be identical
    rows = []
    for n1 in (10, 15, 20, 25, 30, 35, 40):
        if n1 > span - 8:
            break
        r = normalize_mu(E, np.asarray(mu, float), {"norm1": float(n1), "nnorm": 1})
        if not np.isfinite(r.e0):
            continue
        rows.append({"norm1": n1, "nnorm": 1, "edge_step": float(r.edge_step),
                     "norm_at_scan_end": round(float(np.nanmean(np.asarray(r.norm)[-3:])), 3),
                     "white_line_height_edge_steps": round(float(np.nanmax(r.norm)), 3)})
    return rows
