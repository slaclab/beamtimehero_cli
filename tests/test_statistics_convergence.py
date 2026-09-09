"""Convergence statistics — the claims the stopping decision rests on.

Every test here pins a property that was once violated, in ways that read as
plausible science on a plot. They are grouped by the claim they defend:

1. The merge SEM cannot beat counting statistics. A curve below its own
   reference line is arithmetically impossible, and a reference line anchored
   on a point of the data can make it happen anyway.
2. Precision is not convergence. A drifting sample gets more precise about a
   moving number, and a SEM threshold alone signs it off.
3. "Wasteful" has to be a statement about the data. Keyed to the shape of
   1/sqrt(n) instead, it fires on every long series regardless of quality.
"""
from __future__ import annotations

import numpy as np
import pytest

from beamtimehero_cli.science.statistics import policy as stats_policy
from beamtimehero_cli.science.statistics.efficiency import analyze_scan_efficiency
from beamtimehero_cli.science.statistics.features import analyze_scalar_convergence


# ---------------------------------------------------------------------------
# helpers — a windowed white line with genuine Poisson noise on the counts
# ---------------------------------------------------------------------------

def _stack(n_reps, counts_at_peak=4000.0, offsets=None, seed=11):
    """(normalised, raw_counts) for a white-line window.

    *offsets* is a per-rep multiplicative perturbation, which is how a
    systematic actually presents: a rep, or a run of reps, sitting off the
    others by a fixed fraction.
    """
    rng = np.random.default_rng(seed)
    energy = np.linspace(11919.0, 11935.0, 40)
    mu = 1.2 * np.exp(-((energy - 11924.0) / 6.0) ** 2) + 0.3
    off = np.zeros(n_reps) if offsets is None else np.asarray(offsets, dtype=float)
    lam = mu[None, :] * counts_at_peak * (1.0 + off[:, None])
    raw = rng.poisson(lam).astype(float)
    return (raw / counts_at_peak).tolist(), raw.tolist()


def _ratios(result):
    """SEM / counting-statistics floor at every rep count where both exist."""
    sem = result["cumulative_sem_pct"]
    floor = result["cumulative_floor_pct"]
    return [s / f for s, f in zip(sem, floor) if s is not None and f]


# ---------------------------------------------------------------------------
# 1. Nothing beats counting statistics
# ---------------------------------------------------------------------------

def test_first_rep_has_no_sem():
    """One scan carries no scatter estimate, so the curve must start empty.

    It used to be filled with the standard deviation of the *whole* stack.
    That made the first point incomparable with the rest of the curve and
    contaminated by reps not yet collected at that x position — and because
    the plotted reference line was anchored on it, the line inherited a
    baseline inflated by every later systematic. A series with a mid-run step
    then appeared to beat counting statistics for its first several reps.
    """
    norm, raw = _stack(8)
    out = analyze_scan_efficiency(norm, raw_counts_per_point=raw)
    assert out["cumulative_sem_pct"][0] is None
    assert all(v is not None for v in out["cumulative_sem_pct"][1:])


@pytest.mark.parametrize("offsets_name,offsets", [
    ("clean", None),
    ("step at rep 5", np.r_[np.zeros(4), np.full(10, 0.03)]),
    ("single outlier", np.r_[np.zeros(7), [0.05], np.zeros(6)]),
    ("progressive drift", -0.008 * np.arange(14)),
])
def test_sem_never_falls_below_the_counts_floor(offsets_name, offsets):
    """The floor is a lower bound, so the curve sits on or above it — always.

    This is the property that makes the plot mean anything: on the floor says
    "photon-limited, more reps help"; above it says "systematics". If the
    curve can dip below, neither reading is available.

    A little slack is allowed because the floor is estimated from the recorded
    counts of the active counter while the curve is measured on edge-step
    normalised data, so the two are not the same estimator to the last percent.
    """
    norm, raw = _stack(14, offsets=offsets)
    out = analyze_scan_efficiency(norm, raw_counts_per_point=raw)
    ratios = _ratios(out)
    assert ratios, "no comparable points"
    assert min(ratios) > 0.85, (
        f"{offsets_name}: merge SEM dipped to {min(ratios):.2f}x the counting-"
        f"statistics floor. Below 1.0 is not physically reachable; a margin "
        f"this large means the floor and the curve disagree about the data."
    )


def test_small_sample_bias_is_corrected():
    """ddof=1 is unbiased for the variance, not for sigma.

    Its square root is low by c4(n) — 20% at n=2, 8% at n=4 — so an
    uncorrected curve sits below the floor at exactly the rep counts a viewer
    looks at first, and invites the reading that the data beat Poisson.
    """
    norm, raw = _stack(10, offsets=None)
    out = analyze_scan_efficiency(norm, raw_counts_per_point=raw)
    early = _ratios(out)[:3]
    assert min(early) > 0.85, (
        f"early rep counts sit at {early} of the floor — the c4(n) correction "
        f"in the cumulative-SEM loop is missing or wrong"
    )


# ---------------------------------------------------------------------------
# 2. Precision is not convergence
# ---------------------------------------------------------------------------

def test_late_drift_is_not_called_converged():
    """The false positive this whole gate exists for.

    A feature drifting 1.5%/rep over the last four reps — 6% of total travel,
    unambiguous beam damage — returns final_sem_frac 0.51% and
    final_drift_frac 0.37%, both inside their 1% thresholds, because the
    running-mean step absorbs each new rep with weight 1/n. The old logic
    called that "converged" while its own running SEM was climbing.
    """
    vals = (1
            + np.r_[np.zeros(10), np.linspace(0.015, 0.06, 4)]
            + np.random.default_rng(1).normal(0, 0.004, 14))
    out = analyze_scalar_convergence(vals.tolist())

    # The old gates still pass — that is the point of the test.
    assert out["final_sem_frac"] < stats_policy.DEFAULT_SEM_THRESHOLD_FRAC
    assert out["final_drift_frac"] < stats_policy.DEFAULT_DRIFT_THRESHOLD_FRAC
    # And the verdict is still not "converged".
    assert out["verdict"] == "drifting"
    assert out["is_drifting"] or out["sem_is_rising"]


def test_running_sem_cannot_rise_for_true_repeats():
    """A rising running SEM is self-contradictory, so it is a hard veto.

    sigma being re-estimated upward faster than sqrt(n) shrinks it only
    happens when the later reps disagree with the earlier ones.
    """
    clean = 1 + np.random.default_rng(3).normal(0, 0.004, 14)
    assert analyze_scalar_convergence(clean.tolist())["sem_is_rising"] is False

    drifting = 1 + np.r_[np.zeros(9), np.linspace(0.004, 0.02, 5)]
    out = analyze_scalar_convergence(drifting.tolist())
    assert out["sem_is_rising"] is True
    assert out["verdict"] == "drifting"


def test_steady_damage_is_caught_however_precise():
    vals = 1 - 0.005 * np.arange(14) + np.random.default_rng(2).normal(0, 0.004, 14)
    out = analyze_scalar_convergence(vals.tolist())
    assert out["verdict"] == "drifting"
    assert out["trend_tau"] < 0
    assert out["trend_p_value"] < stats_policy.DEFAULT_TREND_P_VALUE


def test_significant_but_immaterial_trend_still_converges():
    """The "so what" gate.

    With enough reps the rank test resolves arbitrarily small monotone
    trends. Without a size threshold beside it, a perfectly good merge gets
    blocked by a drift far below the noise floor it is being held to.
    """
    vals = 1 + 0.0002 * np.arange(40) + np.random.default_rng(4).normal(0, 0.0005, 40)
    out = analyze_scalar_convergence(vals.tolist())
    assert out["trend_p_value"] < stats_policy.DEFAULT_TREND_P_VALUE, "trend is real"
    assert out["trend_total_frac"] < stats_policy.DEFAULT_TREND_TOTAL_FRAC
    assert out["verdict"] == "converged"


def test_clean_repeats_converge():
    vals = 1 + np.random.default_rng(1).normal(0, 0.004, 14)
    out = analyze_scalar_convergence(vals.tolist())
    assert out["verdict"] == "converged"
    assert out["is_drifting"] is False


def test_too_few_reps_reports_unknown_not_no_trend():
    """Three reps cannot support a trend test, and saying "no trend" there is
    a false all-clear rather than a missing answer."""
    out = analyze_scalar_convergence([1.0, 1.01, 0.99])
    assert out["trend_p_value"] is None
    assert out["is_drifting"] is None


# ---------------------------------------------------------------------------
# 3. "Wasteful" has to be about the data
# ---------------------------------------------------------------------------

def test_long_clean_series_is_not_wasteful_by_arithmetic():
    """A perfect 1/sqrt(n) curve improves by ~1/(2n) per rep, crossing the 5%
    marginal-improvement threshold at n=11 whatever the data looks like. Keyed
    to that, optimal_scan_count could never exceed ~10 and any run past ~12
    reps was stamped "wasteful" on arithmetic alone.

    Here the target is genuinely out of reach at this count rate, so the
    honest answer is "keep going", at 20 reps as much as at 5.
    """
    norm, raw = _stack(20, counts_at_peak=60.0)
    out = analyze_scan_efficiency(norm, raw_counts_per_point=raw)
    assert out["target_reached_at_rep"] is None
    assert out["plateau_from_rep"] is None
    assert out["verdict"] == "needs_more"
    assert out["limited_by"] == "counting_statistics"
    assert out["reps_to_target"] > 20


def test_reps_to_target_projects_from_the_floor():
    """SEM ~ k/sqrt(n), so the reps needed scale as (SEM/target)^2. This is the
    number the planner reasons about against remaining beamtime."""
    norm, raw = _stack(8, counts_at_peak=400.0)
    out = analyze_scan_efficiency(norm, raw_counts_per_point=raw)
    if out["target_reached_at_rep"] is not None:
        pytest.skip("target already met at this count rate")
    sem_final = [v for v in out["cumulative_sem_pct"] if v is not None][-1]
    expected = int(np.ceil(8 * (sem_final / out["sem_threshold_pct"]) ** 2))
    assert out["reps_to_target"] == expected


def test_plateau_finds_the_onset_not_the_whole_series():
    """Locating the onset is what makes the verdict actionable: the reps
    before it are the clean data. A detector that merely notices the series
    as a whole underperforms reports rep 2 and throws all of it away."""
    norm, raw = _stack(16, counts_at_peak=8000.0,
                       offsets=np.r_[np.zeros(5), np.full(11, 0.05)])
    out = analyze_scan_efficiency(norm, raw_counts_per_point=raw)
    assert out["plateau_from_rep"] is not None
    assert 4 <= out["plateau_from_rep"] <= 8, (
        f"systematic entered at rep 6, detector said {out['plateau_from_rep']}"
    )
    assert out["limited_by"] == "systematics"


def test_clean_series_has_no_plateau():
    norm, raw = _stack(16, counts_at_peak=8000.0)
    out = analyze_scan_efficiency(norm, raw_counts_per_point=raw)
    assert out["plateau_from_rep"] is None
    assert out["limited_by"] == "counting_statistics"


def test_cumulative_cv_pct_alias_is_preserved():
    """The plan JSON schema and the planner prompt both name the old key; it
    stays as an alias so stored plans keep rendering."""
    norm, raw = _stack(6)
    out = analyze_scan_efficiency(norm, raw_counts_per_point=raw)
    assert out["cumulative_cv_pct"] == out["cumulative_sem_pct"]


def test_floor_curve_absent_without_counts():
    """No counts, no absolute reference — and the plot must be told so rather
    than shown a guide it will read as a floor."""
    norm, _ = _stack(6)
    out = analyze_scan_efficiency(norm)
    assert "cumulative_floor_pct" not in out


# ---------------------------------------------------------------------------
# 4. The panel that carries the decision
# ---------------------------------------------------------------------------

def _panel(stats):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from beamtimehero_cli.science.plots.scan import plot_statistics_trend
    fig, summary = plot_statistics_trend(stats, sample_name="test")
    try:
        labels = [t.get_text() for t in fig.axes[0].get_legend().get_texts()]
    finally:
        if fig is not None:
            plt.close(fig)
    return labels, summary


def test_panel_uses_the_absolute_floor_when_counts_are_available():
    norm, raw = _stack(10)
    out = analyze_scan_efficiency(norm, raw_counts_per_point=raw)
    labels, summary = _panel({
        "cumulative_sem_pct": out["cumulative_sem_pct"],
        "cumulative_floor_pct": out["cumulative_floor_pct"],
        "sem_threshold_pct": out["sem_threshold_pct"],
        "target_reached_at_rep": out["target_reached_at_rep"],
        "plateau_from_rep": out["plateau_from_rep"],
    })
    assert any("Poisson floor" in x for x in labels)
    assert not any("not a floor" in x for x in labels), (
        "counts were supplied, so the reference is the absolute floor, not a fit"
    )
    # The summary is what the agent reads, so it has to name the reference and
    # quantify the gap to it rather than just plotting one.
    assert "counting-statistics floor" in summary


def test_panel_labels_the_fallback_as_a_fit_not_a_floor():
    """Without counts there is no absolute reference. The fallback is fitted
    to every point, so no single rep sets its height, and it says on the
    legend that it is not a floor — because a dashed 1/sqrt(n) guide gets
    read as a limit whatever the caller intended."""
    norm, _ = _stack(10)
    out = analyze_scan_efficiency(norm)
    labels, _ = _panel({
        "cumulative_sem_pct": out["cumulative_sem_pct"],
        "sem_threshold_pct": out["sem_threshold_pct"],
    })
    assert any("not a floor" in x for x in labels)
    assert not any("Poisson floor" in x for x in labels)


def test_panel_refuses_to_draw_what_it_cannot_support():
    """One usable point is not a trend. Returning a figure anyway would put a
    reference line and a threshold around a single marker, which reads as a
    result."""
    import matplotlib
    matplotlib.use("Agg")
    from beamtimehero_cli.science.plots.scan import plot_statistics_trend

    fig, msg = plot_statistics_trend({"cumulative_sem_pct": [None, 1.2]})
    assert fig is None and "need 2" in msg

    fig, msg = plot_statistics_trend({})
    assert fig is None and "missing" in msg
