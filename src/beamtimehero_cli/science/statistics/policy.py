"""Statistics policy — the convergence and repetition-efficiency thresholds.

Sibling to ``reduce/policy.py`` and the three technique policy modules. These
decide when a scan series is called converged and when more repetitions stop
paying for themselves — which is to say, when a user is told to stop
collecting. That is a scientific judgement with beamtime attached, so it is
pinned rather than left inline.
"""
from __future__ import annotations

# Standard error of the mean, as a fraction of the feature value, below which a
# scalar is called converged. 1% is the point where the SEM is comfortably
# under the systematic error of the descriptors themselves, so further
# repetitions buy precision that nothing downstream can use.
DEFAULT_SEM_THRESHOLD_FRAC = 0.01

# Drift in the running mean, as a fraction of the feature value, above which a
# series is called drifting rather than converged. Same scale as the SEM
# threshold on purpose: a trend smaller than the noise floor is not a trend.
#
# .. warning::
#
#    This gates ``final_drift_frac``, the step in the *running mean*, which
#    absorbs each new rep with weight 1/n. Its sensitivity therefore dies as
#    1/n: at n=30 a rep 20% off the mean moves the running mean only 0.66% and
#    slips under this threshold. It is retained because it is cheap and catches
#    gross early outliers, but it is no longer the drift criterion — see
#    ``DEFAULT_TREND_P_VALUE``, which tests the whole trace instead of its last
#    step.
DEFAULT_DRIFT_THRESHOLD_FRAC = 0.01

# Two-sided p-value from the Mann-Kendall (Kendall tau) rank-correlation test
# on the per-rep trace, below which the trace is called monotonically drifting
# rather than stationary. Convergence has two conditions, not one: the mean
# must be precise *and* the reps must be samples of the same thing. A series
# drifting under beam damage gets more precise about a moving target, so a
# precision test alone reports it as converged. Rank-based on purpose — it
# makes no assumption about the shape of the drift and is unmoved by the one
# wild rep that a least-squares slope would chase.
DEFAULT_TREND_P_VALUE = 0.05

# Total drift across the whole series, as a fraction of the mean, below which
# a *statistically* significant trend is still called scientifically
# irrelevant. With enough reps the rank test will resolve arbitrarily small
# monotone trends; this is the "so what" gate that keeps that from blocking a
# perfectly good merge. Same scale as the SEM threshold, same reasoning.
DEFAULT_TREND_TOTAL_FRAC = 0.01

# Marginal improvement in the convergence metric, per additional repetition,
# below which further repetitions are called wasteful.
DEFAULT_EFFICIENCY_THRESHOLD = 0.05

# Floor on the recommended repetition count. Two reps is the minimum that
# permits any scatter estimate at all, so it is never sensible to recommend
# fewer regardless of what the efficiency curve says.
DEFAULT_MIN_RECOMMENDED_SCANS = 2

# ---------------------------------------------------------------------------
# CITATIONS — method -> reference. ``None`` means the method is implemented
# but not yet attributed; those surface as gaps on the generated science
# index, and filling one in is a welcome contribution. See science/README.md.
# ---------------------------------------------------------------------------

CITATIONS = {
    "SEM-fraction convergence criterion": None,
    "Running-mean drift criterion": None,
    "Mann-Kendall trend test for rep-series stationarity": (
        "Mann, H.B. (1945) Econometrica 13, 245; Kendall, M.G. (1948) "
        "Rank Correlation Methods. Rank-based monotone-trend test, used here "
        "to separate 'precise' from 'stationary' when deciding convergence."
    ),
    "Theil-Sen slope for per-rep drift rate": (
        "Theil, H. (1950) Proc. K. Ned. Akad. Wet. 53, 386; Sen, P.K. (1968) "
        "J. Am. Stat. Assoc. 63, 1379. Median-of-pairwise-slopes estimator, "
        "chosen over least squares for resistance to single outlying reps."
    ),
    "Marginal-efficiency stopping rule": None,
    "Poisson-limit comparison for repetition efficiency": None,
}
