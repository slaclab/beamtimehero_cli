"""Unit tests for ``beamtimehero_cli.analysis.xas``.

Pure-math; uses crafted DataFrames so backend wiring isn't on the test path.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from beamtimehero_cli.science.reduce.counters import pick_active_counter
from beamtimehero_cli.science.reduce.normalize import edge_step_normalize
from beamtimehero_cli.science.reduce.reps import average_reps, estimate_per_rep_noise


def _make_scan(counter_values, *, counter_name="vortDT", i0=None, n=100):
    """Energy index 0..n-1, single counter column with optional I0."""
    if isinstance(counter_values, (int, float)):
        signal = np.full(n, float(counter_values))
    else:
        signal = np.asarray(counter_values, dtype=float)
    energy = np.arange(len(signal), dtype=float)
    data = {counter_name: signal}
    if i0 is not None:
        data["I0"] = np.full(len(signal), float(i0))
    df = pd.DataFrame(data, index=pd.Index(energy, name="energy"))
    return df


# ---------------------------------------------------------------------------
# pick_active_counter
# ---------------------------------------------------------------------------

def test_pick_active_counter_prefers_ppboff():
    df = pd.DataFrame({"ppboff": [1, 2], "vortDT": [10, 10], "I0": [1, 1]})
    counter, reason = pick_active_counter(df)
    assert counter == "ppboff"
    assert "ppboff" in reason


def test_pick_active_counter_picks_highest_vort():
    df = pd.DataFrame({
        "vortDT": [1, 2, 3],
        "vortDT2": [10, 20, 30],
        "vortDT3": [5, 5, 5],
        "I0": [1, 1, 1],
    })
    counter, _ = pick_active_counter(df)
    assert counter == "vortDT2"


def test_pick_active_counter_defaults_to_I1():
    df = pd.DataFrame({"I0": [1, 1], "I1": [2, 2], "other": [3, 3]})
    counter, reason = pick_active_counter(df)
    assert counter == "I1"
    assert "defaulting to I1" in reason


# ---------------------------------------------------------------------------
# edge_step_normalize
# ---------------------------------------------------------------------------

def test_edge_step_normalize_unit_step():
    # Signal jumps from 0 (pre-edge plateau) to 1 (post-edge plateau).
    n = 100
    signal = np.concatenate([np.zeros(40), np.linspace(0, 1, 20), np.ones(40)])
    df = _make_scan(signal, i0=1.0)

    energy, norm = edge_step_normalize(df, "vortDT", normalize_by="I0")

    assert len(energy) == n
    assert norm[:5].mean() == pytest.approx(0.0, abs=1e-6)
    assert norm[-5:].mean() == pytest.approx(1.0, abs=1e-6)


def test_edge_step_normalize_zero_step_centers_pre_edge():
    # Constant signal — denom is zero, function should subtract pre-edge only.
    df = _make_scan(5.0, i0=1.0)
    _, norm = edge_step_normalize(df, "vortDT", normalize_by="I0")
    assert np.allclose(norm, 0.0)


def test_edge_step_normalize_divides_by_i0_with_zero_guard():
    # I0 hits zero on one row; normalization should not raise or yield inf.
    counter = np.linspace(1.0, 2.0, 50)
    df = _make_scan(counter, i0=2.0)
    df.loc[df.index[25], "I0"] = 0.0
    _, norm = edge_step_normalize(df, "vortDT", normalize_by="I0")
    assert np.all(np.isfinite(norm))


def test_edge_step_normalize_raises_on_missing_counter():
    df = pd.DataFrame({"I0": [1, 1, 1]}, index=[10.0, 11.0, 12.0])
    with pytest.raises(KeyError, match="vortDT"):
        edge_step_normalize(df, "vortDT", normalize_by="I0")


def test_edge_step_normalize_raises_on_missing_normalizer():
    df = pd.DataFrame({"vortDT": [1, 2, 3]}, index=[10.0, 11.0, 12.0])
    with pytest.raises(KeyError, match="I0"):
        edge_step_normalize(df, "vortDT", normalize_by="I0")


# ---------------------------------------------------------------------------
# estimate_per_rep_noise
# ---------------------------------------------------------------------------

def test_estimate_per_rep_noise_recovers_known_sigma():
    rng = np.random.default_rng(0)
    n_points = 200
    sigmas = np.array([0.01, 0.02, 0.05, 0.1, 0.2])
    # Build reps with the post-edge plateau as the last 30% of points so the
    # 10% baseline window is well inside it.
    cols = {}
    for i, s in enumerate(sigmas):
        signal = np.concatenate([
            np.zeros(int(n_points * 0.5)),
            np.linspace(0, 1, int(n_points * 0.2)),
            np.ones(n_points - int(n_points * 0.5) - int(n_points * 0.2)),
        ])
        cols[f"S{i:03d}"] = signal + rng.normal(0, s, n_points)
    combined = pd.DataFrame(cols, index=np.arange(n_points, dtype=float))

    est = estimate_per_rep_noise(combined, baseline_frac=0.10)
    # Each estimate should be within a generous factor of the true sigma.
    for true, got in zip(sigmas, est):
        assert 0.4 * true < got < 2.5 * true, f"sigma={true} → est={got}"


def test_estimate_per_rep_noise_falls_back_when_no_variance():
    combined = pd.DataFrame({
        "A": np.ones(50), "B": np.ones(50),
    }, index=np.arange(50, dtype=float))
    est = estimate_per_rep_noise(combined)
    # All zero std → fallback to equal weights (1.0).
    assert np.all(est == 1.0)


# ---------------------------------------------------------------------------
# average_reps
# ---------------------------------------------------------------------------

def test_average_reps_equal_is_arithmetic_mean():
    combined = pd.DataFrame({
        "a": [1.0, 2.0, 3.0],
        "b": [3.0, 4.0, 5.0],
    }, index=[10.0, 11.0, 12.0])
    mean, std, weights = average_reps(combined, weighting="equal")
    assert weights is None
    assert mean.tolist() == [2.0, 3.0, 4.0]


def test_average_reps_inverse_variance_returns_weights():
    rng = np.random.default_rng(1)
    # Build reps where rep A has much lower noise than rep B.
    n = 200
    base = np.concatenate([np.zeros(100), np.ones(100)])
    a = base + rng.normal(0, 0.01, n)
    b = base + rng.normal(0, 0.20, n)
    combined = pd.DataFrame({"a": a, "b": b}, index=np.arange(n, dtype=float))

    mean, std, weights = average_reps(combined, weighting="inverse_variance")
    assert weights is not None
    assert len(weights) == 2
    # Lower-noise rep should dominate.
    assert weights[0] > weights[1]
    assert mean.index.equals(combined.index)


def test_average_reps_rejects_unknown_weighting():
    combined = pd.DataFrame({"a": [1.0]}, index=[0.0])
    with pytest.raises(ValueError, match="Unknown weighting"):
        average_reps(combined, weighting="bogus")
