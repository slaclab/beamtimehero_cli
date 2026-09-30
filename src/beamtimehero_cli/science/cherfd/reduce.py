"""Rebinning, sweep merging, and the statistics built on them. Pure.

A continuous sweep is a cloud of (energy, counts) frames, not a grid. The
reductions here put sweeps on a common energy grid and combine them without
destroying absolute intensity -- unlike the cherfd_claude viewer's
``average_sweeps``, which min-max rescales every sweep to 0..1 first and so
erases beam decay, damage and any real intensity change between sweeps.

Combining rule. With a normalizer (I0), the merged value in a bin is the
**ratio of sums** sum(signal)/sum(I0) over every frame from every sweep in
that bin: the maximum-likelihood estimate for Poisson counts, and it weights
each sweep by how much it actually measured there. Without one it is the
frame-weighted mean. ``per_sweep_scaling="mean"`` optionally rescales each
sweep to a common mean first -- a stand-in for I0 when none was recorded,
and reported as such.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d

from beamtimehero_cli.science.cherfd import policy


def make_grid(e_min: float, e_max: float, bin_ev: float) -> np.ndarray:
    """Bin edges on a fixed grid anchored at a multiple of bin_ev."""
    if bin_ev <= 0:
        raise ValueError("bin_ev must be positive")
    lo = np.floor(e_min / bin_ev) * bin_ev
    hi = np.ceil(e_max / bin_ev) * bin_ev
    n = max(1, int(round((hi - lo) / bin_ev)))
    return lo + bin_ev * np.arange(n + 1)


def _bin_sums(energy, values, edges):
    e = np.asarray(energy, float)
    v = np.asarray(values, float)
    m = np.isfinite(e) & np.isfinite(v)
    idx = np.digitize(e[m], edges) - 1
    ok = (idx >= 0) & (idx < len(edges) - 1)
    nb = len(edges) - 1
    s = np.bincount(idx[ok], weights=v[m][ok], minlength=nb)
    s2 = np.bincount(idx[ok], weights=v[m][ok] ** 2, minlength=nb)
    n = np.bincount(idx[ok], minlength=nb).astype(float)
    return s, s2, n


def bin_sweeps(
    sweeps: list[dict],
    *,
    signal: str,
    normalize_by: str | None = None,
    bin_ev: float = policy.DEFAULT_BIN_EV,
    e_min: float | None = None,
    e_max: float | None = None,
    per_sweep_scaling: str = "none",
) -> dict:
    """Put each sweep on one grid and merge. ``sweeps`` = [{"label", "arrays"}].

    Returns the grid, the merged spectrum with a standard error, per-sweep
    binned spectra, and frame counts per bin.
    """
    if per_sweep_scaling not in ("none", "mean"):
        raise ValueError("per_sweep_scaling must be 'none' or 'mean'")
    if not sweeps:
        raise ValueError("no sweeps to merge")
    for s in sweeps:
        if signal not in s["arrays"]:
            raise ValueError(f"sweep {s.get('label')} has no column {signal!r}")
        if normalize_by and normalize_by not in s["arrays"]:
            raise ValueError(f"sweep {s.get('label')} has no normalizer column {normalize_by!r}")

    all_e = np.concatenate([np.asarray(s["arrays"]["absev"], float) for s in sweeps])
    all_e = all_e[np.isfinite(all_e)]
    lo = e_min if e_min is not None else float(all_e.min())
    hi = e_max if e_max is not None else float(all_e.max())
    edges = make_grid(lo, hi, bin_ev)
    centers = 0.5 * (edges[:-1] + edges[1:])
    nb = len(centers)

    tot_s = np.zeros(nb)
    tot_n = np.zeros(nb)
    tot_frames = np.zeros(nb)
    tot_e = np.zeros(nb)                   # sum of frame energies -> mean energy per bin
    per_sweep = []
    ratios_per_frame_s2 = np.zeros(nb)     # for scatter-based error, no normalizer
    scale_ref = None
    for s in sweeps:
        a = s["arrays"]
        e = np.asarray(a["absev"], float)
        y = np.asarray(a[signal], float)
        d = np.asarray(a[normalize_by], float) if normalize_by else None
        if d is not None:
            bad = ~np.isfinite(d) | (d <= 0)
            y = np.where(bad, np.nan, y)
        scale = 1.0
        if per_sweep_scaling == "mean":
            ref = np.nanmean(y / d) if d is not None else np.nanmean(y)
            if scale_ref is None:
                scale_ref = ref
            scale = (scale_ref / ref) if ref else 1.0
        sy, sy2, n = _bin_sums(e, y * scale, edges)
        se, _, _ = _bin_sums(e, np.where(np.isfinite(y), e, np.nan), edges)
        if d is not None:
            sd, _, _ = _bin_sums(e, np.where(np.isfinite(y), d, np.nan), edges)
            with np.errstate(invalid="ignore", divide="ignore"):
                spec = np.where(sd > 0, sy / sd, np.nan)
            tot_n += sd
        else:
            with np.errstate(invalid="ignore", divide="ignore"):
                spec = np.where(n > 0, sy / n, np.nan)
            tot_n += n
            ratios_per_frame_s2 += sy2
        tot_s += sy
        tot_frames += n
        tot_e += se
        with np.errstate(invalid="ignore", divide="ignore"):
            e_sweep = np.where(n > 0, se / np.where(n > 0, n, 1), centers)
        per_sweep.append({"label": s.get("label"), "energy": e_sweep, "values": spec,
                          "frames": n, "scale": scale})

    with np.errstate(invalid="ignore", divide="ignore"):
        merged = np.where(tot_n > 0, tot_s / tot_n, np.nan)
        if normalize_by:
            # Poisson on the numerator counts, I0 treated as exact.
            sem = np.where(tot_n > 0, np.sqrt(np.clip(tot_s, 0, None)) / tot_n, np.nan)
            error_model = "poisson(signal)/sum(I0); I0 treated as noiseless"
        else:
            var = np.where(tot_frames > 1,
                           (ratios_per_frame_s2 - tot_s ** 2 / np.where(tot_frames > 0, tot_frames, 1))
                           / np.maximum(tot_frames - 1, 1), np.nan)
            sem = np.sqrt(np.clip(var, 0, None)) / np.sqrt(np.maximum(tot_frames, 1))
            error_model = "frame scatter within bin / sqrt(n)"

    filled = tot_frames > 0
    # Label each bin by the mean energy of the frames that landed in it, not
    # the nominal centre: continuous sweeps fill bins unevenly (ramps, region
    # boundaries, frame spacing commensurate with the bin), and a centre label
    # then biases feature positions by up to half a bin.
    e_mean = np.where(filled, tot_e / np.where(filled, tot_frames, 1), centers)
    return {
        "energy": e_mean,
        "bin_center": centers,
        "merged": merged,
        "sem": sem,
        "frames_per_bin": tot_frames,
        "per_sweep": per_sweep,
        "bin_ev": bin_ev,
        "signal": signal,
        "normalize_by": normalize_by,
        "per_sweep_scaling": per_sweep_scaling,
        "error_model": error_model,
        "empty_bins": int((~filled).sum()),
        "n_sweeps": len(sweeps),
    }


def noise_estimate(values: np.ndarray) -> float | None:
    """White-noise sigma from the second difference: var(y[i-1]-2y[i]+y[i+1]) = 6 sigma^2.

    Insensitive to smooth spectral structure, so it measures noise on a real
    spectrum without a model of it.
    """
    y = np.asarray(values, float)
    y = y[np.isfinite(y)]
    if y.size < 5:
        return None
    d2 = y[:-2] - 2 * y[1:-1] + y[2:]
    return float(np.std(d2) / np.sqrt(6.0))


def convergence(sweep_spectra: list[np.ndarray], frames: list[np.ndarray],
                target_relative_noise: float | None = None) -> dict:
    """Noise of the running merge vs sweep count, and sweeps needed for a target.

    Input is per-sweep binned values + frame counts on one grid (bin_sweeps
    ``per_sweep``). The running merge is frame-weighted. Relative noise is
    sigma / mean level. Fitted as sigma_N = sigma_1 / sqrt(N)**alpha;
    alpha ~ 1 means the sweeps are statistically independent and averaging
    is still paying off; alpha well below 1 means a systematic (drift,
    damage, misalignment) dominates and more sweeps will not help.
    """
    if not sweep_spectra:
        raise ValueError("no sweeps")
    num = np.zeros_like(np.asarray(sweep_spectra[0], float))
    den = np.zeros_like(num)
    rows = []
    for i, (v, n) in enumerate(zip(sweep_spectra, frames), start=1):
        v = np.asarray(v, float)
        n = np.asarray(n, float)
        ok = np.isfinite(v) & (n > 0)
        num[ok] += v[ok] * n[ok]
        den[ok] += n[ok]
        with np.errstate(invalid="ignore", divide="ignore"):
            run = np.where(den > 0, num / den, np.nan)
        sigma = noise_estimate(run)
        level = float(np.nanmean(np.abs(run))) if np.isfinite(run).any() else None
        rows.append({"n_sweeps": i, "noise": sigma,
                     "relative_noise": (sigma / level) if (sigma is not None and level) else None})
    ns = np.array([r["n_sweeps"] for r in rows], float)
    rel = np.array([r["relative_noise"] if r["relative_noise"] else np.nan for r in rows])
    fit = None
    m = np.isfinite(rel) & (rel > 0)
    if m.sum() >= 3:
        slope, intercept = np.polyfit(np.log(ns[m]), np.log(rel[m]), 1)
        fit = {"alpha": round(float(-2 * slope), 3), "relative_noise_1": float(np.exp(intercept))}
    out = {"curve": rows, "fit": fit}
    if target_relative_noise and fit and fit["alpha"] > 0.2:
        need = (fit["relative_noise_1"] / target_relative_noise) ** (2.0 / fit["alpha"])
        out["target_relative_noise"] = target_relative_noise
        out["sweeps_needed"] = int(np.ceil(need))
        out["additional_sweeps"] = max(0, int(np.ceil(need)) - len(rows))
    elif target_relative_noise:
        out["target_relative_noise"] = target_relative_noise
        out["sweeps_needed"] = None
        out["note"] = "fewer than 3 sweeps or no 1/sqrt(N) trend; cannot project"
    if fit:
        a = fit["alpha"]
        out["interpretation"] = (
            "statistics-limited: more sweeps keep helping" if a >= 0.7 else
            "partly systematic-limited: returns diminishing" if a >= 0.3 else
            "systematic-limited: more sweeps will not reduce noise; check drift/damage"
        )
    return out


def _smooth_uniform(e: np.ndarray, y: np.ndarray, smooth_ev: float) -> tuple[np.ndarray, np.ndarray]:
    """Resample onto a uniform grid and Gaussian-smooth (sigma = smooth_ev)."""
    step = max(float(np.median(np.diff(e))), 1e-3)
    grid = np.arange(e[0], e[-1] + step / 2, step)
    yy = np.interp(grid, e, y)
    if smooth_ev > 0:
        yy = gaussian_filter1d(yy, sigma=smooth_ev / step, mode="nearest")
    return grid, yy


def best_shift(e_ref: np.ndarray, y_ref: np.ndarray, e: np.ndarray, y: np.ndarray,
               search_ev: float = policy.SHIFT_SEARCH_EV, step_ev: float = 0.01,
               smooth_ev: float = policy.SHIFT_SMOOTH_EV) -> dict:
    """Energy shift delta minimizing |y_ref(E) - (a*y(E+delta) + b)|^2.

    Scale and offset are solved in closed form at every delta, so an intensity
    difference between the two spectra does not bias the shift. Positive
    delta means ``y`` sits at higher energy than ``y_ref``.

    Both spectra are Gaussian-smoothed (``smooth_ev``) first. Without it the
    estimate "pixel-locks": linear interpolation of a noisy spectrum lowers its
    variance between grid nodes, so the cost has spurious minima at multiples
    of the point spacing (measured: shifts snapping to 0.05 eV steps on 0.05 eV
    frame spacing). Smoothing well beyond the spacing removes that structure;
    it broadens both spectra equally, so it does not move the minimum.
    """
    e_ref = np.asarray(e_ref, float)
    y_ref = np.asarray(y_ref, float)
    e = np.asarray(e, float)
    y = np.asarray(y, float)
    m1 = np.isfinite(e_ref) & np.isfinite(y_ref)
    m2 = np.isfinite(e) & np.isfinite(y)
    e_ref, y_ref, e, y = e_ref[m1], y_ref[m1], e[m2], y[m2]
    if e_ref.size < 10 or e.size < 10:
        raise ValueError("need at least 10 finite points in each spectrum")
    o = np.argsort(e)
    e, y = e[o], y[o]
    o = np.argsort(e_ref)
    e_ref, y_ref = e_ref[o], y_ref[o]
    e_ref, y_ref = _smooth_uniform(e_ref, y_ref, smooth_ev)
    e, y = _smooth_uniform(e, y, smooth_ev)
    lo = max(e_ref.min(), e.min()) + search_ev
    hi = min(e_ref.max(), e.max()) - search_ev
    w = (e_ref >= lo) & (e_ref <= hi)
    if w.sum() < 10:
        raise ValueError("spectra overlap too little for the shift search")
    er, yr = e_ref[w], y_ref[w]
    deltas = np.arange(-search_ev, search_ev + step_ev / 2, step_ev)
    cost = np.empty_like(deltas)
    for i, d in enumerate(deltas):
        yi = np.interp(er + d, e, y)
        A = np.vstack([yi, np.ones_like(yi)]).T
        coef, *_ = np.linalg.lstsq(A, yr, rcond=None)
        cost[i] = np.sum((yr - A @ coef) ** 2)
    k = int(np.argmin(cost))
    d = float(deltas[k])
    if 0 < k < len(deltas) - 1:
        c0, c1, c2 = cost[k - 1], cost[k], cost[k + 1]
        den = c0 - 2 * c1 + c2
        if den > 0:
            d += float(0.5 * (c0 - c2) / den * step_ev)
    yi = np.interp(er + d, e, y)
    A = np.vstack([yi, np.ones_like(yi)]).T
    coef, *_ = np.linalg.lstsq(A, yr, rcond=None)
    return {"shift_ev": round(d, 4), "scale": float(coef[0]), "offset": float(coef[1]),
            "smooth_ev": smooth_ev,
            "at_search_edge": bool(k in (0, len(deltas) - 1)),
            "window_ev": [float(lo), float(hi)]}


def linear_trend(x: np.ndarray, y: np.ndarray) -> dict | None:
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 2:
        return None
    if m.sum() == 2:
        slope = float((y[m][1] - y[m][0]) / (x[m][1] - x[m][0]))
        return {"slope": slope, "total_change": slope * float(x[m].max() - x[m].min())}
    slope, icpt = np.polyfit(x[m], y[m], 1)
    resid = y[m] - (slope * x[m] + icpt)
    return {"slope": float(slope), "intercept": float(icpt),
            "total_change": float(slope * (x[m].max() - x[m].min())),
            "residual_std": float(np.std(resid))}


def minmax(values: np.ndarray) -> np.ndarray:
    """0..1 rescale -- for locating features only, never for reporting intensity."""
    v = np.asarray(values, float)
    lo, hi = np.nanmin(v), np.nanmax(v)
    return (v - lo) / (hi - lo) if hi > lo else v * 0

# CITATIONS — method -> reference. ``None`` = implemented, not yet attributed.
CITATIONS = {
    "ratio-of-sums merge for Poisson counts": None,
    "second-difference white-noise estimate": None,
    "scale/offset-free least-squares energy shift": None,
}
