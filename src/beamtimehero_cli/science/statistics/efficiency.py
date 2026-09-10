"""
Comprehensive scan repetition efficiency analysis.

Parent tool that calls cosine similarity convergence as one component,
then adds CV analysis, Poisson limit comparison, optimal scan count
recommendation, and a synthesized verdict.

Pure math module — no DB, no data loading, no pandas dependency.
"""
from __future__ import annotations

import numpy as np
import warnings
from typing import Any

from beamtimehero_cli.science.fitting.similarity import analyze_scan_quality
from beamtimehero_cli.science.statistics.policy import (
    DEFAULT_EFFICIENCY_THRESHOLD,
    DEFAULT_SEM_THRESHOLD_FRAC,
    DEFAULT_MIN_RECOMMENDED_SCANS,
    DEFAULT_REP_CHI2_THRESHOLD,
)


def analyze_scan_efficiency(
    scan_data: list[list[float]],
    efficiency_threshold: float = DEFAULT_EFFICIENCY_THRESHOLD,
    sem_threshold_frac: float = DEFAULT_SEM_THRESHOLD_FRAC,
    min_recommended_scans: int = DEFAULT_MIN_RECOMMENDED_SCANS,
    raw_counts_per_point: list[list[float]] | None = None,
) -> dict[str, Any]:
    """
    Comprehensive efficiency analysis for repeated scan data.

    Combines cosine similarity convergence with CV analysis, Poisson limit
    comparison, and optimal scan count recommendation.

    Parameters
    ----------
    scan_data : list[list[float]]
        2D array where each row is one scan's normalized intensity values.
        Shape: (n_scans, n_points). Minimum 2 scans required.
    efficiency_threshold : float, default=0.05
        Fractional CV improvement below which adding scans is not worthwhile.
    min_recommended_scans : int, default=2
        Floor for the optimal scan count recommendation.
    raw_counts_per_point : list[list[float]], optional
        Raw (un-normalized) total counts per energy point per scan, same shape
        as scan_data. If provided, an absolute counts-based Poisson floor is
        computed: at each energy point the achievable per-rep CV is
        1/sqrt(N_total) where N_total is summed across all reps. The floor is
        averaged over the analysis window and reported alongside the existing
        rate-based metric. If actual cumulative CV plateaus above this floor,
        more reps cannot help (limit is systematic, not statistical).

    Returns
    -------
    dict with keys:
        - convergence: full result from analyze_scan_quality
        - cv_mean_pct: average coefficient of variation (%)
        - cumulative_sem_pct: fractional SEM of the merge of the first n reps,
          averaged over the window, per rep count (%). None at n=1 — a single
          scan has no scatter estimate. Canonical name for what used to be
          called cumulative_cv_pct, which is retained as an alias.
        - cumulative_floor_pct: the counting-statistics floor at each rep
          count (%), = counts_poisson_floor_pct / sqrt(n). The reference the
          SEM curve should be plotted against; absolute, so the data cannot
          fall below it. Only present with raw_counts_per_point.
        - limited_by: "counting_statistics" | "systematics" — what is capping
          the merge now. Systematics means more reps will not help.
        - target_reached_at_rep: first rep count whose merge SEM met the
          target, or None
        - reps_to_target: total reps needed to reach the target if the series
          keeps tracking the floor, or None if already reached
        - plateau_from_rep: rep count from which the SEM curve stopped falling
          like 1/sqrt(n), i.e. where systematics took over and further reps
          stopped paying. None if it never flattened.
        - poisson_limit_pct: how close to theoretical sqrt(n) improvement (%),
          anchored at n=2 (the first defined point)
        - counts_poisson_floor_pct: absolute counts-based achievable CV floor (%)
          (only present when raw_counts_per_point is provided)
        - cv_vs_floor_ratio: cv_mean_pct / counts_poisson_floor_pct
          (>1 = systematics-limited, more reps won't help; ~1 = at the floor;
          <1 means floor estimate is wrong, usually wrong counter passed)
        - optimal_scan_count: recommended number of scans
        - marginal_improvement: per-scan fractional CV improvement
        - current_vs_optimal: human-readable comparison string
        - verdict: "needs_more" | "reasonable" | "marginal" | "wasteful"
        - verdict_explanation: human-readable reasoning
    """
    data = np.array(scan_data, dtype=float)
    if data.ndim != 2 or data.shape[0] < 2:
        return {"error": "scan_data must be a 2D array with at least 2 scans (rows)."}

    n_scans, n_points = data.shape

    # --- Component 1: Cosine similarity convergence ---
    convergence_result = analyze_scan_quality(scan_data)
    if "error" in convergence_result:
        return convergence_result

    # --- Component 2: CV analysis ---
    mean_spectrum = np.mean(data, axis=0)
    std_spectrum = np.std(data, axis=0, ddof=1)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cv_by_point = std_spectrum / mean_spectrum
        cv_by_point = np.where(np.isfinite(cv_by_point), cv_by_point, 0.0)

    # Trim 5% from each edge to avoid low-signal regions
    trim = max(1, n_points // 20)
    cv_mean = float(np.mean(cv_by_point[trim:-trim])) if n_points > 2 * trim else float(np.mean(cv_by_point))

    # --- Component 3: Cumulative SEM improvement curve ---
    # The fractional standard error of the merge of the first n reps, averaged
    # over the window. This is the quantity that answers "how well do I know
    # the spectrum I am going to publish" — and it is directly comparable to
    # the counting-statistics floor computed in Component 4b, because both are
    # per-point *relative* errors averaged over the same window.
    #
    # n=1 is deliberately NaN. A single scan carries no scatter estimate, so
    # there is no honest value here. It used to be filled with the std of the
    # *entire* stack, which made the first point (a) incomparable with every
    # other point on the curve and (b) contaminated by reps not yet collected
    # at that x position. Anything anchored on it — the plotted 1/sqrt(n)
    # guide especially — inherited a baseline inflated by every later
    # systematic, so a series with a mid-run step change appeared to beat
    # counting statistics for its first few reps. It cannot.
    cumulative_cv = np.full(n_scans, np.nan)
    for n in range(2, n_scans + 1):
        subset = data[:n]
        cum_mean = np.mean(subset, axis=0)
        # ddof=1 gives an unbiased *variance*; its square root is a biased
        # estimate of sigma, low by c4(n) — 20% at n=2, 8% at n=4. Left
        # uncorrected, the first few points of the curve sit below the
        # counting-statistics floor, which is not a thing that can happen and
        # invites exactly the wrong reading of the plot. Divide it out.
        cum_sem = np.std(subset, axis=0, ddof=1) / (_c4(n) * np.sqrt(n))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cum_cv = cum_sem / cum_mean
            cum_cv = np.where(np.isfinite(cum_cv), cum_cv, 0.0)
        if n_points > 2 * trim:
            cumulative_cv[n - 1] = np.mean(cum_cv[trim:-trim])
        else:
            cumulative_cv[n - 1] = np.mean(cum_cv)

    # --- Component 4: Poisson limit comparison ---
    # Anchored at n=2, the first rep count at which the metric is defined.
    baseline_cv = cumulative_cv[1] if n_scans >= 2 else np.nan
    if np.isfinite(baseline_cv) and baseline_cv > 0 and cumulative_cv[-1] > 0:
        actual_improvement = baseline_cv / cumulative_cv[-1]
        theoretical_improvement = np.sqrt(n_scans / 2.0)
        poisson_limit_pct = float((actual_improvement / theoretical_improvement) * 100)
    else:
        poisson_limit_pct = 100.0

    # --- Component 4b: Counts-based Poisson floor (absolute) ---
    counts_poisson_floor_pct = None
    cv_vs_floor_ratio = None
    cumulative_floor_pct = None
    if raw_counts_per_point is not None:
        counts_arr = np.array(raw_counts_per_point, dtype=float)
        if counts_arr.shape != data.shape:
            return {
                "error": (
                    f"raw_counts_per_point shape {counts_arr.shape} does not match "
                    f"scan_data shape {data.shape}."
                )
            }
        # Total counts at each energy point, summed across all reps:
        total_counts_per_point = np.sum(np.maximum(counts_arr, 0.0), axis=0)
        # Achievable per-point CV at the Poisson floor (single-rep equivalent):
        # CV_floor(E) = 1/sqrt(N_per_rep_avg) where N_per_rep_avg = total/n_scans.
        # We want to compare to cv_mean which is std/mean ACROSS reps, so the
        # apples-to-apples floor for the rep-to-rep CV is 1/sqrt(N_per_rep).
        n_per_rep = total_counts_per_point / max(n_scans, 1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cv_floor_per_point = np.where(
                n_per_rep > 0, 1.0 / np.sqrt(n_per_rep), np.nan
            )
        if n_points > 2 * trim:
            cv_floor_window = cv_floor_per_point[trim:-trim]
        else:
            cv_floor_window = cv_floor_per_point
        cv_floor_window = cv_floor_window[np.isfinite(cv_floor_window)]
        if cv_floor_window.size > 0:
            counts_poisson_floor_pct = float(np.mean(cv_floor_window) * 100)
            if counts_poisson_floor_pct > 0:
                cv_vs_floor_ratio = round(cv_mean * 100 / counts_poisson_floor_pct, 3)
                # The same floor as a curve against rep count: merging n reps
                # multiplies the counts by n, so the floor falls as 1/sqrt(n).
                # This is the *absolute* reference the cumulative-SEM curve
                # should be judged against — it comes from the recorded counts,
                # not from any point on the curve itself, so the data cannot
                # sit below it and the comparison means something.
                cumulative_floor_pct = [
                    round(counts_poisson_floor_pct / np.sqrt(n), 6)
                    for n in range(1, n_scans + 1)
                ]

    # --- Component 5: Marginal improvement & optimal scan count ---
    marginal_improvement = np.zeros(n_scans)
    # No metric at n=1, so no improvement is measurable into n=1 or n=2.
    # 1.0 reads as "still improving", which keeps the knee search below from
    # stopping on a number it never had.
    marginal_improvement[: min(2, n_scans)] = 1.0
    for i in range(2, n_scans):
        if np.isfinite(cumulative_cv[i - 1]) and cumulative_cv[i - 1] > 0:
            marginal_improvement[i] = (cumulative_cv[i - 1] - cumulative_cv[i]) / cumulative_cv[i - 1]
        else:
            marginal_improvement[i] = 0.0

    # --- Component 5b: where should this series have stopped? ---
    #
    # The old rule was "the first rep whose marginal CV improvement falls
    # below 5%", which sounds data-driven and is not: a *perfect* 1/sqrt(n)
    # curve improves by 1 - sqrt(n/(n+1)) ~ 1/(2n) per rep, so it crosses 5%
    # at n=11 no matter how good the data is. That capped optimal_scan_count
    # near 10 and stamped "wasteful" on every run past ~12 reps on arithmetic
    # alone. Two things actually end a series, so ask about those instead:
    #
    #   1. the merge SEM reached the science target  -> you have the data
    #   2. the SEM curve left the 1/sqrt(n) track    -> systematics dominate,
    #      more reps buy nothing and something needs fixing
    #
    # Whichever happens first is where the series should have stopped.
    sem_pct_curve = cumulative_cv * 100
    sem_threshold_pct = sem_threshold_frac * 100
    target_rep = None
    for i in range(n_scans):
        if np.isfinite(sem_pct_curve[i]) and sem_pct_curve[i] <= sem_threshold_pct:
            target_rep = i + 1
            break

    plateau_rep = _plateau_onset(cumulative_cv, cumulative_floor_pct)

    # How many reps would it take to reach the target, if the series carries
    # on tracking counting statistics? SEM ~ k/sqrt(n), so n_req = n*(SEM/T)^2.
    # This is the number the planner actually needs: it converts "not yet
    # converged" into "you need 40 reps and have time for 12", which is a
    # decision (accept a looser target, add count time, add filters, or drop
    # the sample) rather than an open-ended instruction to keep collecting.
    reps_to_target = None
    last_valid = np.where(np.isfinite(sem_pct_curve) & (sem_pct_curve > 0))[0]
    if target_rep is None and last_valid.size:
        i = int(last_valid[-1])
        reps_to_target = int(np.ceil((i + 1) * (sem_pct_curve[i] / sem_threshold_pct) ** 2))

    if target_rep is not None and plateau_rep is not None:
        optimal_scan_count = min(target_rep, plateau_rep)
    elif target_rep is not None:
        optimal_scan_count = target_rep
    elif plateau_rep is not None:
        # Flattened before reaching the target: the target is not reachable by
        # collecting more of the same, so the optimum is where it flattened.
        optimal_scan_count = plateau_rep
    else:
        # Still on the floor and still short of the target — nothing in this
        # series says stop. The old code fell back to the marginal-improvement
        # knee here, which reports ~10 for any series (see Component 5b) and so
        # manufactured a "wasteful" verdict out of a run that had simply not
        # finished. Say "not done" instead, and say how much is left.
        optimal_scan_count = reps_to_target or n_scans
    optimal_scan_count = max(int(optimal_scan_count), min_recommended_scans)

    # What is holding the merge back *now* — photons, or the beamline?
    # A detected plateau answers this on its own: the curve has left the floor,
    # so the merge is no longer photon-limited whether or not the target was
    # already met on the way there. Whether the target was met is the separate
    # question the verdict answers.
    limited_by = "systematics" if plateau_rep is not None else "counting_statistics"

    # --- Component 6: Verdict ---
    final_convergence = convergence_result["cumulative_convergence"][-1]
    last_marginal = marginal_improvement[-1] if n_scans > 1 else 1.0
    final_sem_pct = sem_pct_curve[-1] if np.isfinite(sem_pct_curve[-1]) else None

    if limited_by == "systematics" and target_rep is None:
        # The distinguishing case, and the one the old rule read as ordinary
        # diminishing returns: the curve flattened *before* reaching the
        # target, so the target is not reachable by collecting more.
        verdict = "wasteful"
        explanation = (
            f"Merge SEM left the 1/sqrt(n) track at rep {plateau_rep} and has stalled at "
            f"{final_sem_pct:.3f}% without reaching the {sem_threshold_pct:.2f}% target. "
            f"This is a systematics limit, not a statistics limit — the remaining "
            f"{max(n_scans - plateau_rep, 0)} reps bought nothing and further reps will not "
            f"either. Look for a step in the optics, a spot change, sample damage or drifting "
            f"energy calibration before spending more beam time."
        )
    elif target_rep is None and plateau_rep is None:
        verdict = "needs_more"
        still_improving = (final_convergence < 0.99 or last_marginal > efficiency_threshold)
        explanation = (
            f"Merge SEM is {final_sem_pct:.3f}% after {n_scans} reps, still above the "
            f"{sem_threshold_pct:.2f}% target, and still tracking the counting-statistics "
            f"floor — so more reps do still help. Reaching the target on this count rate "
            f"needs about {reps_to_target} reps in total "
            f"({max(reps_to_target - n_scans, 0)} more). If that does not fit the schedule, "
            f"the levers are count time, filters, or accepting a looser target — not more "
            f"reps of the same length."
            + ("" if still_improving else
               " Note the cosine-similarity component has already saturated, so the "
               "remaining gain is in noise level rather than in spectral shape.")
        )
    elif n_scans <= optimal_scan_count * 1.2:
        verdict = "reasonable"
        if target_rep is not None and optimal_scan_count == target_rep:
            why = f"the {sem_threshold_pct:.2f}% SEM target was met at rep {target_rep}"
        elif plateau_rep is not None and optimal_scan_count == plateau_rep:
            why = f"the SEM curve left the counting-statistics floor at rep {plateau_rep}"
        else:
            why = (f"neither the {sem_threshold_pct:.2f}% target nor a plateau falls inside "
                   f"this series, so this is the marginal-improvement estimate")
        explanation = (
            f"Collecting {n_scans} scans is close to the estimated optimal of "
            f"{optimal_scan_count} ({why}). Good balance of statistics and beam time."
        )
    elif n_scans <= optimal_scan_count * 1.5:
        verdict = "marginal"
        explanation = (
            f"Collecting {n_scans} scans when ~{optimal_scan_count} would suffice. "
            f"The extra scans provide diminishing returns."
        )
    else:
        verdict = "wasteful"
        if target_rep is not None:
            explanation = (
                f"The {sem_threshold_pct:.2f}% SEM target was already met at rep {target_rep}; "
                f"{n_scans} were collected. The extra {n_scans - target_rep} reps kept improving "
                f"precision nothing downstream can use — that is {n_scans - target_rep} reps of "
                f"beam time another sample could have had."
            )
        else:
            explanation = (
                f"Collecting {n_scans} scans when ~{optimal_scan_count} would give nearly identical "
                f"statistics. The additional {n_scans - optimal_scan_count} scans provide minimal "
                f"improvement."
            )

    out = {
        "convergence": convergence_result,
        "n_scans": n_scans,
        "n_points": n_points,
        "cv_mean_pct": round(cv_mean * 100, 4),
        "poisson_limit_pct": round(poisson_limit_pct, 1),
        "optimal_scan_count": optimal_scan_count,
        "marginal_improvement": [round(v, 6) for v in marginal_improvement.tolist()],
        # Canonical name. This is a standard error of the merged spectrum, not
        # a coefficient of variation: it carries the 1/sqrt(n) from averaging.
        # ``cumulative_cv_pct`` is kept as a deprecated alias because the
        # planner prompt and the plan JSON schema both name it.
        "cumulative_sem_pct": _pct_list(cumulative_cv),
        "cumulative_cv_pct": _pct_list(cumulative_cv),
        "current_vs_optimal": f"{n_scans} scans collected, {optimal_scan_count} recommended",
        "limited_by": limited_by,
        "sem_threshold_pct": round(sem_threshold_pct, 4),
        "target_reached_at_rep": target_rep,
        "reps_to_target": reps_to_target,
        "verdict": verdict,
        "verdict_explanation": explanation,
    }
    if counts_poisson_floor_pct is not None:
        out["counts_poisson_floor_pct"] = round(counts_poisson_floor_pct, 4)
        out["cv_vs_floor_ratio"] = cv_vs_floor_ratio
        if cumulative_floor_pct is not None:
            out["cumulative_floor_pct"] = cumulative_floor_pct
    out["plateau_from_rep"] = plateau_rep
    if plateau_rep is not None and verdict != "wasteful":
        out["verdict_explanation"] += (
            f" Note: the merge SEM left the counting-statistics floor at rep {plateau_rep} "
            f"and sits at {sem_pct_curve[-1] / (cumulative_floor_pct[-1] or np.nan):.1f}x the "
            f"floor — reps from {plateau_rep} on carry a systematic that averaging will not "
            f"remove. The reps before it are the clean data."
        )
    return out


def _c4(n: int) -> float:
    """E[s]/sigma for a sample of size n drawn from a normal distribution.

    The correction factor that makes the sample standard deviation an unbiased
    estimator of sigma rather than of sigma-squared. Tabulated as ``c4`` in the
    control-chart literature; computed here from its closed form,
    ``sqrt(2/(n-1)) * Gamma(n/2)/Gamma((n-1)/2)``.
    """
    if n < 2:
        return float("nan")
    from scipy.special import gammaln
    return float(np.sqrt(2.0 / (n - 1)) * np.exp(gammaln(n / 2) - gammaln((n - 1) / 2)))


def _pct_list(arr):
    """Percent-scaled list with NaN rendered as None, so a rep count at which
    the metric is undefined serialises as a gap rather than a fake zero."""
    return [None if not np.isfinite(v) else round(float(v) * 100, 4) for v in arr]


def _plateau_onset(cumulative_cv, floor_pct=None, margin=0.25, tol=0.5):
    """First rep count at which the merge SEM stops tracking counting statistics.

    This is the stopping signal that matters. While the reps are genuine
    repeats the merge SEM follows the 1/sqrt(n) counting-statistics floor;
    once a systematic enters — a step in the optics, a spot change, sample
    damage — it adds a variance that averaging cannot remove, the curve peels
    away from the floor, and every further rep is beam time spent for nothing.

    With *floor_pct* (the absolute per-rep-count floor from the recorded
    counts) the test is direct: find where SEM/floor rises meaningfully above
    the ratio the series started with, and stays there. That locates the
    *onset* of the systematic rather than merely noting that the series as a
    whole underperforms — a distinction that matters, because the reps before
    the onset are the good data.

    Without a floor, fall back to comparing the realised improvement over the
    tail against the 1/sqrt(n) expectation.

    Returns the 1-indexed rep count, or None if the curve never departs.
    """
    cv = np.asarray(cumulative_cv, dtype=float)
    ok = np.where(np.isfinite(cv) & (cv > 0))[0]
    if ok.size < 4:
        return None

    if floor_pct is not None:
        fl = np.asarray(floor_pct, dtype=float)
        if fl.shape == cv.shape:
            ratio = np.full_like(cv, np.nan)
            good = np.isfinite(cv) & np.isfinite(fl) & (fl > 0)
            ratio[good] = cv[good] * 100.0 / fl[good]
            idx = np.where(np.isfinite(ratio))[0]
            if idx.size >= 4:
                # Baseline from the opening reps, before anything can have
                # gone wrong. Median of three so one noisy rep cannot set it.
                base = float(np.median(ratio[idx[:3]]))
                if base > 0:
                    limit = base * (1.0 + margin)
                    for j, i in enumerate(idx):
                        if j < 2:
                            continue
                        rest = ratio[idx[j:]]
                        # Require the departure to persist: a single high rep
                        # is an outlier, a sustained offset is a systematic.
                        if ratio[i] > limit and float(np.median(rest)) > limit:
                            return int(i + 1)
            return None

    for i in ok[:-2]:
        n_i, n_f = i + 1, ok[-1] + 1
        expected = np.sqrt(n_f / n_i)          # ideal improvement factor
        realised = cv[i] / cv[ok[-1]]
        if (realised - 1.0) < tol * (expected - 1.0):
            return int(n_i)
    return None

# ---------------------------------------------------------------------------
# CITATIONS — method -> reference. ``None`` means the method is implemented
# but not yet attributed; those surface as gaps on the generated science
# index, and filling one in is a welcome contribution. See science/README.md.
# ---------------------------------------------------------------------------

CITATIONS = {
    "Coefficient-of-variation convergence": None,
    "Poisson-limit comparison (counting-statistics floor)": (
        "Compares the observed scan-to-scan scatter with the sqrt(N) "
        "counting-statistics floor; scatter at the floor means further reps "
        "buy only sqrt(N) improvement."
    ),
    "Optimal scan-count recommendation": None,
    "Cosine-similarity convergence component": (
        "Delegates to science.fitting.similarity.analyze_scan_quality."
    ),
}


def screen_reps(
    scan_data: list[list[float]],
    raw_counts_per_point: list[list[float]],
    chi2_threshold: float = DEFAULT_REP_CHI2_THRESHOLD,
    window_mask: list[bool] | None = None,
) -> dict[str, Any]:
    """
    Flag single repetitions that disagree with the others by more than counting
    statistics allow.

    Every other check in this module reads the series as a whole. The rank
    trend test uses only the direction of pairwise comparisons, so one wild rep
    cannot move it; the floor comparison averages that rep into a dispersion
    taken over the whole stack. A single bad repetition therefore enters the
    merge unremarked. This screens for one.

    Each rep is compared against the merge of its *siblings*, in units of the
    counting error expected at each energy point::

        chi2_i = (1/m) * sum_E [ y_i(E) - mu_-i(E) ]^2 / sigma_i(E)^2

    Leaving rep *i* out of the mean it is measured against removes the
    correlation between a rep and its own contribution to that mean; the
    remaining variance of the leave-one-out mean is folded into sigma.

    The scale is absolute. ``sigma`` comes from the recorded counts and not
    from the spread of the data, so the expectation is 1 for a rep limited by
    photon statistics, and a value of 4 means four times the variance photon
    counting can account for. That is what makes a fixed threshold meaningful
    here where a shape-similarity score is not: a normalised inner product
    between two spectra that agree on a tall edge is dominated by the edge, and
    tracks the overall noise level rather than the defect.

    Parameters
    ----------
    scan_data : list[list[float]]
        2D array, one row per rep, of edge-step normalised intensities.
        Shape (n_scans, n_points). At least 3 reps are required, since the
        comparison is against a merge of the others.
    raw_counts_per_point : list[list[float]]
        Raw, un-normalised counts per energy point per rep, same shape as
        *scan_data*. Required: the whole point of the statistic is that its
        denominator is independent of the dispersion it is testing. These must
        be the counts as recorded, before any dead-time correction, since a
        corrected count is a rescaled quantity whose variance exceeds its
        value and would push every chi2 down.
    chi2_threshold : float, default=3.0
        Value above which a rep is flagged for inspection.
    window_mask : list[bool], optional
        Per-point mask selecting the feature window. If omitted, the same 5%
        end trim used elsewhere in this module is applied.

    Returns
    -------
    dict with keys:
        - rep_chi2: reduced chi-square per rep, in collection order
        - flagged_reps: 1-based rep numbers above the threshold
        - median_chi2, max_chi2, max_over_median
        - edge_step_counts, background_counts: the affine calibration used
        - n_window_points: points entering each average
        - chi2_threshold: the threshold applied
        - verdict, verdict_explanation

    Notes
    -----
    The conversion from counts to normalised units is recovered from the data
    rather than taken as an argument. Edge-step normalisation is affine, so the
    mean recorded counts and the mean normalised spectrum are related by
    ``N(E) ~ b + edge_step * y(E)``; a least-squares fit over all points
    returns both, and ``sigma_i(E) = sqrt(N_i(E)) / edge_step``.
    """
    data = np.array(scan_data, dtype=float)
    if data.ndim != 2:
        return {"error": "scan_data must be a 2D array (n_scans, n_points)."}
    n_scans, n_points = data.shape
    if n_scans < 3:
        return {
            "error": (
                f"Screening compares each rep against the merge of the others, "
                f"which needs at least 3 reps; got {n_scans}."
            )
        }

    counts = np.array(raw_counts_per_point, dtype=float)
    if counts.shape != data.shape:
        return {
            "error": (
                f"raw_counts_per_point shape {counts.shape} does not match "
                f"scan_data shape {data.shape}."
            )
        }

    if window_mask is None:
        trim = max(1, n_points // 20)
        mask = np.zeros(n_points, dtype=bool)
        if n_points > 2 * trim:
            mask[trim:-trim] = True
        else:
            mask[:] = True
    else:
        mask = np.asarray(window_mask, dtype=bool)
        if mask.shape != (n_points,):
            return {
                "error": (
                    f"window_mask length {mask.shape} does not match "
                    f"{n_points} energy points."
                )
            }
    m = int(mask.sum())
    if m < 2:
        return {"error": "Fewer than 2 points in the analysis window."}

    # Affine calibration: recover the edge step in counts so the counting error
    # can be expressed in the units of the normalised spectrum.
    mean_norm = data.mean(axis=0)
    mean_counts = counts.mean(axis=0)
    design = np.vstack([np.ones_like(mean_norm), mean_norm]).T
    background, edge_step = np.linalg.lstsq(design, mean_counts, rcond=None)[0]
    if not np.isfinite(edge_step) or edge_step <= 0:
        return {
            "error": (
                "Could not recover a positive edge step relating the raw counts "
                "to the normalised spectrum; are these the matching arrays?"
            )
        }

    # Leaving one rep out inflates the variance of the comparison mean by
    # 1/(n-1) of a single rep's variance.
    loo_inflation = np.sqrt(1.0 + 1.0 / (n_scans - 1))
    total = data.sum(axis=0)
    rep_chi2: list[float] = []
    for i in range(n_scans):
        others_mean = (total - data[i]) / (n_scans - 1)
        sigma = np.sqrt(np.maximum(counts[i], 1.0)) / edge_step * loo_inflation
        resid = ((data[i] - others_mean) / sigma)[mask]
        rep_chi2.append(float(np.mean(resid ** 2)))

    chi2_arr = np.array(rep_chi2)
    median_chi2 = float(np.median(chi2_arr))
    max_chi2 = float(chi2_arr.max())
    flagged = [int(i) + 1 for i in np.flatnonzero(chi2_arr > chi2_threshold)]

    if flagged:
        verdict = "outliers_present"
        explanation = (
            f"Rep(s) {', '.join(str(r) for r in flagged)} disagree with the "
            f"others by more than counting statistics allow (reduced chi-square "
            f"up to {max_chi2:.1f} against a threshold of {chi2_threshold:g}, "
            f"where 1 is the photon-limited expectation). Inspect them before "
            f"merging."
        )
    elif median_chi2 > chi2_threshold:
        verdict = "stack_above_counting_limit"
        explanation = (
            f"No single rep stands out, but the whole stack sits at a median "
            f"reduced chi-square of {median_chi2:.1f}, so the dispersion "
            f"exceeds counting statistics throughout. This is a property of the "
            f"series, not of one rep."
        )
    else:
        verdict = "clean"
        explanation = (
            f"No rep exceeds the threshold; median reduced chi-square "
            f"{median_chi2:.2f} against a photon-limited expectation of 1."
        )

    return {
        "rep_chi2": [round(v, 4) for v in rep_chi2],
        "flagged_reps": flagged,
        "median_chi2": round(median_chi2, 4),
        "max_chi2": round(max_chi2, 4),
        "max_over_median": (
            round(max_chi2 / median_chi2, 3) if median_chi2 > 0 else None
        ),
        "edge_step_counts": round(float(edge_step), 3),
        "background_counts": round(float(background), 3),
        "n_window_points": m,
        "n_scans": n_scans,
        "chi2_threshold": chi2_threshold,
        "verdict": verdict,
        "verdict_explanation": explanation,
    }
