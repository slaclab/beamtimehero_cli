"""Pinned values for every scientific default in ``science/*/policy.py``.

These are not correctness tests — nothing here asserts the physics is right.
They assert the numbers are what someone *chose*, so that changing one is a
deliberate act rather than a silent one.

The gap this closes: the policy modules centralize the scientific defaults, so
a one-token edit in one file changes what every tool computes. Before this
file, all of those edits left the suite green — a contributor could widen the
pre-edge window by 50%, empty the multi-component family list, or triple the
k-weight and get no signal at all.

**If a test here fails and you meant it:** update the expected value in the
same commit as the change. The diff on this file is then the record of which
physics defaults moved and when, which is exactly what a reviewer wants to see.

The last two tests are the important ones — they check that the constants are
actually *wired*, in both directions:

  * the science functions take their defaults from policy, so a scientist
    calling them directly gets the same behaviour as the CLI, and
  * the agent-facing JSON schema takes its defaults from policy, so the tool
    description cannot drift from what the tool does.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from beamtimehero_cli.science.exafs import policy as exafs_policy
from beamtimehero_cli.science.reduce import policy as reduce_policy
from beamtimehero_cli.science.statistics import policy as stats_policy
from beamtimehero_cli.science.xas import policy as xas_policy
from beamtimehero_cli.science.xrs import policy as xrs_policy
from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS


# --------------------------------------------------------------------------
# XAS
# --------------------------------------------------------------------------

def test_xas_fit_windows():
    """Wilke-style pre-edge window and the white-line window, relative to E0."""
    assert xas_policy.PRE_EDGE_WINDOW_REL == (-20.0, -5.0)
    assert xas_policy.WHITE_LINE_WINDOW_REL == (-10.0, 40.0)


def test_xas_multi_component_families():
    """Which edge families get a multi-peak white line, and how many peaks."""
    assert xas_policy.MULTI_COMPONENT_FAMILIES == ("ln_L3", "an_L3", "an_M")
    assert xas_policy.MULTI_COMPONENT_COUNT == 3
    assert xas_policy.SINGLE_COMPONENT_COUNT == 1
    # the decision function, not just the constants
    assert xas_policy.white_line_components_for("ln_L3") == 3
    assert xas_policy.white_line_components_for("an_M") == 3
    assert xas_policy.white_line_components_for("3d_K") == 1
    assert xas_policy.white_line_components_for(None) == 1


def test_xas_data_adequacy_minimums():
    """Descriptor fits need more points than a reference spectrum does."""
    assert xas_policy.MIN_OVERLAPPING_POINTS == 20
    assert xas_policy.MIN_REFERENCE_POINTS == 10
    assert xas_policy.MIN_REFERENCE_POINTS < xas_policy.MIN_OVERLAPPING_POINTS

    xas_policy.check_overlap(20)                      # at the limit: fine
    with pytest.raises(ValueError, match="Too few overlapping"):
        xas_policy.check_overlap(19)

    xas_policy.check_reference_points(10, "ref.dat")  # at the limit: fine
    with pytest.raises(ValueError, match="too few overlapping"):
        xas_policy.check_reference_points(9, "ref.dat")


# --------------------------------------------------------------------------
# EXAFS
# --------------------------------------------------------------------------

def test_exafs_kspace_defaults():
    assert exafs_policy.DEFAULT_KWEIGHT == 2
    assert exafs_policy.DEFAULT_KMIN == 2.0
    assert exafs_policy.DEFAULT_KMAX is None      # None = full measured range
    assert exafs_policy.DEFAULT_DK == 1.0
    assert exafs_policy.DEFAULT_RBKG == 1.0


def test_exafs_point_minimum():
    assert exafs_policy.MIN_EXAFS_POINTS == 20
    exafs_policy.check_exafs_points(20)
    with pytest.raises(ValueError, match="for EXAFS extraction"):
        exafs_policy.check_exafs_points(19)


# --------------------------------------------------------------------------
# XRS
# --------------------------------------------------------------------------

def test_xrs_background_and_regime():
    assert xrs_policy.BACKGROUND_MODELS == ("constant", "linear", "pearson7")
    assert xrs_policy.DEFAULT_BACKGROUND_MODEL == "linear"
    assert xrs_policy.DEFAULT_BACKGROUND_MODEL in xrs_policy.BACKGROUND_MODELS
    assert xrs_policy.DIPOLE_REGIME_Q_MAX_INV_ANG == 3.0
    # the boundary is exclusive on the low side
    assert xrs_policy.q_regime(2.99) == xrs_policy.LOW_Q_LABEL
    assert xrs_policy.q_regime(3.0) == xrs_policy.HIGH_Q_LABEL


# --------------------------------------------------------------------------
# Reduction — artifact gating and repetition averaging
# --------------------------------------------------------------------------

def test_reduce_artifact_and_rep_defaults():
    assert reduce_policy.DEFAULT_GLITCH_Z_THRESHOLD == 8.0
    assert reduce_policy.DEFAULT_GLITCH_WINDOW == 7
    # medfilt needs an odd kernel; an even default would be silently bumped
    assert reduce_policy.DEFAULT_GLITCH_WINDOW % 2 == 1
    assert reduce_policy.DEFAULT_SATURATION_REL_TOL == 1e-4
    assert reduce_policy.DEFAULT_NOISE_BASELINE_FRAC == 0.1
    assert reduce_policy.DEFAULT_MIN_REP_SPAN_FRAC == 0.8
    assert 0.0 < reduce_policy.DEFAULT_MIN_REP_SPAN_FRAC <= 1.0


# --------------------------------------------------------------------------
# Statistics — convergence and repetition efficiency
# --------------------------------------------------------------------------

def test_statistics_convergence_defaults():
    assert stats_policy.DEFAULT_SEM_THRESHOLD_FRAC == 0.01
    assert stats_policy.DEFAULT_DRIFT_THRESHOLD_FRAC == 0.01
    assert stats_policy.DEFAULT_EFFICIENCY_THRESHOLD == 0.05
    assert stats_policy.DEFAULT_MIN_RECOMMENDED_SCANS == 2
    # Per-rep screening. Empirical, not a chi-square quantile: clean and
    # monotonically-damaged reference series top out near 1.6, so 3 clears the
    # dispersion of the estimate without giving up a genuine step.
    assert stats_policy.DEFAULT_REP_CHI2_THRESHOLD == 3.0
    # Must sit above 1, the photon-limited expectation, or every rep of a
    # perfectly good stack is flagged.
    assert stats_policy.DEFAULT_REP_CHI2_THRESHOLD > 1.0
    # Two reps is the fewest that permits any scatter estimate at all.
    assert stats_policy.DEFAULT_MIN_RECOMMENDED_SCANS >= 2


def test_statistics_trend_defaults():
    """The stationarity half of the convergence decision.

    Precision and stationarity are separate conditions and a precision test
    alone passes a sample that is being destroyed — it just gets more
    confident about a moving number. These two pin the second condition.
    """
    assert stats_policy.DEFAULT_TREND_P_VALUE == 0.05
    assert stats_policy.DEFAULT_TREND_TOTAL_FRAC == 0.01
    # Significance alone is not enough to block a merge: with enough reps the
    # rank test resolves arbitrarily small trends, so a "so what" gate on the
    # size of the excursion has to sit beside it.
    assert 0.0 < stats_policy.DEFAULT_TREND_P_VALUE < 1.0
    assert stats_policy.DEFAULT_TREND_TOTAL_FRAC > 0.0
    # Same scale as the SEM target on purpose — a trend smaller than the noise
    # floor the merge is being held to is not a trend worth stopping for.
    assert (stats_policy.DEFAULT_TREND_TOTAL_FRAC
            == stats_policy.DEFAULT_SEM_THRESHOLD_FRAC)


# --------------------------------------------------------------------------
# Wiring — the part that actually broke twice during review
# --------------------------------------------------------------------------

def _default_of(fn, param):
    return inspect.signature(fn).parameters[param].default


def test_science_signatures_read_from_policy():
    """A scientist calling the science function directly must get the policy
    default — not a literal that happens to agree with it today."""
    from beamtimehero_cli.science.exafs import background, fourier
    from beamtimehero_cli.science.xrs import reduce as xrs_reduce

    assert _default_of(fourier.xftf, "kweight") is exafs_policy.DEFAULT_KWEIGHT
    assert _default_of(fourier.xftf, "kmin") is exafs_policy.DEFAULT_KMIN
    assert _default_of(fourier.xftf, "kmax") is exafs_policy.DEFAULT_KMAX
    assert _default_of(fourier.xftf, "dk") is exafs_policy.DEFAULT_DK
    assert _default_of(fourier.ft_window, "dk") is exafs_policy.DEFAULT_DK
    assert _default_of(background.autobk_lite, "kweight") is exafs_policy.DEFAULT_KWEIGHT
    assert _default_of(background.autobk_lite, "rbkg") is exafs_policy.DEFAULT_RBKG
    assert (_default_of(xrs_reduce.subtract_compton_background, "model")
            is xrs_policy.DEFAULT_BACKGROUND_MODEL)

    from beamtimehero_cli.science.reduce import artifacts, reps
    from beamtimehero_cli.science.statistics import efficiency, features

    assert (_default_of(artifacts.detect_glitches, "z_threshold")
            is reduce_policy.DEFAULT_GLITCH_Z_THRESHOLD)
    assert (_default_of(artifacts.detect_glitches, "window")
            is reduce_policy.DEFAULT_GLITCH_WINDOW)
    assert (_default_of(artifacts.detect_saturation, "rel_tol")
            is reduce_policy.DEFAULT_SATURATION_REL_TOL)
    assert (_default_of(reps.estimate_per_rep_noise, "baseline_frac")
            is reduce_policy.DEFAULT_NOISE_BASELINE_FRAC)
    assert (_default_of(reps.filter_short_reps, "min_span_frac")
            is reduce_policy.DEFAULT_MIN_REP_SPAN_FRAC)
    assert (_default_of(efficiency.analyze_scan_efficiency, "efficiency_threshold")
            is stats_policy.DEFAULT_EFFICIENCY_THRESHOLD)
    assert (_default_of(efficiency.analyze_scan_efficiency, "min_recommended_scans")
            is stats_policy.DEFAULT_MIN_RECOMMENDED_SCANS)
    assert (_default_of(efficiency.analyze_scan_efficiency, "sem_threshold_frac")
            is stats_policy.DEFAULT_SEM_THRESHOLD_FRAC)
    for fn in (features.analyze_scalar_convergence, features.analyze_feature_evolution):
        assert (_default_of(fn, "sem_threshold_frac")
                is stats_policy.DEFAULT_SEM_THRESHOLD_FRAC)
        assert (_default_of(fn, "drift_threshold_frac")
                is stats_policy.DEFAULT_DRIFT_THRESHOLD_FRAC)
    assert (_default_of(features.analyze_scalar_convergence, "trend_p_value")
            is stats_policy.DEFAULT_TREND_P_VALUE)
    assert (_default_of(features.analyze_scalar_convergence, "trend_total_frac")
            is stats_policy.DEFAULT_TREND_TOTAL_FRAC)


def _schema(tool_name):
    for d in TOOL_DEFINITIONS:
        if d["function"]["name"] == tool_name:
            return d["function"]["parameters"]["properties"]
    raise AssertionError(f"tool {tool_name!r} not in the catalog")


def test_agent_schema_reads_from_policy():
    """The JSON schema's defaults are what the CLI actually sends, so if they
    diverge from policy the tool lies to the agent about its own behaviour —
    and a policy edit silently does nothing on the CLI path."""
    ft = _schema("fourier_transform_chi")
    assert ft["kweight"]["default"] == exafs_policy.DEFAULT_KWEIGHT
    assert ft["kmin"]["default"] == exafs_policy.DEFAULT_KMIN
    assert ft["dk"]["default"] == exafs_policy.DEFAULT_DK

    chi = _schema("extract_chi")
    assert chi["rbkg"]["default"] == exafs_policy.DEFAULT_RBKG
    assert chi["kweight"]["default"] == exafs_policy.DEFAULT_KWEIGHT

    bg = _schema("subtract_compton_background")
    assert bg["model"]["default"] == xrs_policy.DEFAULT_BACKGROUND_MODEL
    assert bg["model"]["enum"] == list(xrs_policy.BACKGROUND_MODELS)


def _policy_modules():
    """Every ``science/*/policy.py``, discovered rather than listed.

    This used to name three modules. That made the guard itself the blind spot
    it was written to remove: adding a fourth policy module left its constants
    unpinned and the suite green, which is the exact failure mode this file
    exists to prevent. Now a new policy module is covered by existing.
    """
    import importlib

    import beamtimehero_cli.science as science

    root = Path(science.__file__).parent
    mods = [
        importlib.import_module(f"beamtimehero_cli.science.{path.parent.name}.policy")
        for path in sorted(root.glob("*/policy.py"))
    ]
    assert mods, "no science/*/policy.py found — has the layout moved?"
    return mods


def test_policy_module_discovery_covers_every_technique():
    """A policy module that discovery misses is a policy module nobody pins."""
    found = {m.__name__.split(".")[-2] for m in _policy_modules()}
    assert found == {"exafs", "reduce", "statistics", "xas", "xrs"}, (
        f"policy modules changed: {sorted(found)}. If a package gained or lost "
        "one, update this set and add or remove its pinned values above."
    )


def test_every_policy_constant_is_pinned_here():
    """Catch a *new* policy constant that nobody pinned.

    Without this, adding a default to a policy module reintroduces exactly the
    silent-change problem this file exists to prevent.
    """
    source = Path(__file__).read_text()
    missing = []
    for mod in _policy_modules():
        for name in dir(mod):
            if name.startswith("_") or name == "CITATIONS":
                continue
            if not name.isupper():
                continue
            if name not in source:
                missing.append(f"{mod.__name__.split('.')[-2]}.{name}")
    assert not missing, (
        "new policy constants are not pinned in tests/test_science_policy.py: "
        f"{missing}. Add an assertion so a future change to them is deliberate."
    )
