"""Tests for the EXAFS k-space math (analysis/exafs.py) and its supporting
additions (filter_short_reps / deadtime_correct in analysis/xas.py,
pre_post_normalize in interpretation/normalize.py, generic_data/lcf.py).

Repo convention: pure math on synthetic arrays with known ground truth. The
synthetic model is a single-shell EXAFS signal — chi(k) = A·sin(2kR)/k² with
a known apparent distance R — which the FT must localize at R.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from beamtimehero_cli.science.exafs.background import autobk_lite
from beamtimehero_cli.science.exafs.fourier import first_shell_peak, ft_window, xftf
from beamtimehero_cli.science.exafs.kspace import etok, ktoe
from beamtimehero_cli.science.reduce.deadtime import deadtime_correct
from beamtimehero_cli.science.reduce.reps import filter_short_reps
from beamtimehero_cli.science.xas.compare import compare_to_references
from beamtimehero_cli.science.xas.normalize import pre_post_normalize

R_SHELL = 2.5  # apparent first-shell distance (Å) of the synthetic signal
E0 = 7112.0


def _synthetic_chi(kmax=12.0, kstep=0.05, amp=0.8):
    k = np.arange(0.0, kmax, kstep)
    chi = np.zeros_like(k)
    nz = k > 0
    chi[nz] = amp * np.sin(2 * k[nz] * R_SHELL) * np.exp(-0.01 * k[nz] ** 2) / k[nz] ** 2
    return k, chi


def _synthetic_mu(n=600, emax_above=500.0, noise=0.0, seed=0):
    """mu(E): sloping pre-edge + edge step + single-shell EXAFS oscillation."""
    rng = np.random.RandomState(seed)
    energy = np.concatenate([
        np.linspace(E0 - 150, E0 - 5, 60),
        np.linspace(E0 - 5, E0 + emax_above, n),
    ])
    k = etok(energy, E0)
    step = 1.0 / (1.0 + np.exp(-(energy - E0) / 1.5))
    osc = np.zeros_like(energy)
    nz = k > 0.5
    osc[nz] = 0.4 * np.sin(2 * k[nz] * R_SHELL) / k[nz] ** 2
    mu = 0.2 - 1e-4 * (energy - E0) + step * (1.0 + osc)
    return energy, mu + rng.normal(0, noise, len(energy))


# ---------------------------------------------------------------------------
# Axis conversion
# ---------------------------------------------------------------------------

def test_etok_ktoe_roundtrip():
    k = np.linspace(0.0, 12.0, 50)
    energy = ktoe(k, E0)
    np.testing.assert_allclose(etok(energy, E0), k, atol=1e-10)


def test_etok_clips_below_edge():
    assert etok(np.array([E0 - 50.0]), E0)[0] == 0.0


# ---------------------------------------------------------------------------
# FT localizes a known shell
# ---------------------------------------------------------------------------

def test_xftf_peak_at_known_distance():
    k, chi = _synthetic_chi()
    ft = xftf(k, chi, kmin=2.0, kmax=11.0, kweight=2)
    r, mag = ft["r"], ft["chir_mag"]
    sel = (r > 1.0) & (r < 4.0)
    r_peak = r[sel][np.argmax(mag[sel])]
    assert abs(r_peak - R_SHELL) < 0.1


def test_xftf_provenance_and_truncation():
    k, chi = _synthetic_chi()
    ft = xftf(k, chi, kmin=2.0, kmax=10.0, rmax_out=6.0)
    assert ft["r"].max() <= 6.0
    assert ft["provenance"]["kweight"] == 2
    assert "phase-uncorrected" in ft["provenance"]["r_axis"]


def test_first_shell_peak_parabola_refined():
    k, chi = _synthetic_chi()
    ft = xftf(k, chi, kmin=2.0, kmax=11.0)
    peak = first_shell_peak(ft["r"], ft["chir_mag"])
    assert peak["found"]
    assert abs(peak["r_peak_ang"] - R_SHELL) < 0.1
    assert "caveat" in peak


def test_ft_window_shape():
    k = np.arange(0, 12, 0.05)
    win = ft_window(k, 3.0, 9.0, dk=1.0)
    assert win.max() == pytest.approx(1.0)
    assert win[k < 2.0].max() == 0.0
    # sill midpoints at half height
    assert win[np.argmin(np.abs(k - 3.0))] == pytest.approx(0.5, abs=0.05)


# ---------------------------------------------------------------------------
# Background extraction recovers the oscillation
# ---------------------------------------------------------------------------

def test_autobk_lite_recovers_shell_from_mu():
    energy, mu = _synthetic_mu()
    flat, prov = pre_post_normalize(energy, mu, E0)
    assert prov["applied"]
    bk = autobk_lite(energy, mu, E0, edge_step=prov["edge_step"], rbkg=1.0)
    ft = xftf(bk["k"], bk["chi"], kmin=2.0, kmax=10.0, kweight=2)
    sel = (ft["r"] > 1.2) & (ft["r"] < 4.0)
    r_peak = ft["r"][sel][np.argmax(ft["chir_mag"][sel])]
    assert abs(r_peak - R_SHELL) < 0.15
    assert bk["provenance"]["method"] == "autobk_lite"


def test_autobk_lite_rejects_no_edge_data():
    energy = np.linspace(E0 - 100, E0 - 10, 50)
    with pytest.raises(ValueError):
        autobk_lite(energy, np.ones_like(energy), E0)


# ---------------------------------------------------------------------------
# pre_post_normalize contract
# ---------------------------------------------------------------------------

def test_pre_post_normalize_edge_step_near_one():
    energy, mu = _synthetic_mu()
    flat, prov = pre_post_normalize(energy, mu, E0)
    assert prov["applied"]
    # synthetic edge step is 1.0 by construction
    assert prov["edge_step"] == pytest.approx(1.0, rel=0.15)
    post = flat[energy > E0 + 100]
    assert np.abs(post.mean() - 1.0) < 0.1  # flattened post-edge sits at ~1


def test_pre_post_normalize_refuses_degenerate():
    energy = np.linspace(0, 10, 5)
    mu, prov = pre_post_normalize(energy, np.ones(5), 5.0)
    assert prov["applied"] is False
    assert "reason" in prov


# ---------------------------------------------------------------------------
# filter_short_reps
# ---------------------------------------------------------------------------

def test_filter_short_reps_drops_aborted_sweep():
    energy = np.linspace(100.0, 200.0, 101)
    full = pd.Series(np.ones(101), index=energy, name="S001")
    aborted = pd.Series([1.0] * 8 + [np.nan] * 93, index=energy, name="S002")
    combined = pd.concat([full, aborted], axis=1)
    filtered, dropped = filter_short_reps(combined)
    assert list(filtered.columns) == ["S001"]
    assert dropped == ["S002"]


def test_filter_short_reps_keeps_everything_when_uniform():
    energy = np.linspace(100.0, 200.0, 50)
    combined = pd.DataFrame({"S001": np.ones(50), "S002": np.ones(50)}, index=energy)
    filtered, dropped = filter_short_reps(combined)
    assert list(filtered.columns) == ["S001", "S002"]
    assert dropped == []


# ---------------------------------------------------------------------------
# deadtime_correct
# ---------------------------------------------------------------------------

def test_deadtime_correct_boosts_hot_channel():
    npts, nelem = 10, 2
    sca = np.full((npts, nelem), 1000.0)
    icr = np.zeros((npts, nelem))
    icr[:, 1] = 2e5  # hot element: 200 kcps at tau=1us -> 20% dead
    ct = np.ones(npts)
    out = deadtime_correct(sca, icr, ct, tau=1e-6)
    np.testing.assert_allclose(out[:, 0], 1000.0)         # cold channel untouched
    np.testing.assert_allclose(out[:, 1], 1000.0 / 0.8)   # hot channel boosted


def test_deadtime_correct_clips_pathological_icr():
    sca = np.full((5, 1), 100.0)
    icr = np.full((5, 1), 5e6)  # would give negative live time
    out = deadtime_correct(sca, icr, np.ones(5), tau=1e-6)
    np.testing.assert_allclose(out[:, 0], 100.0 / 0.05)  # floored at 5% live


# ---------------------------------------------------------------------------
# generic LCF (promoted from xrs_interpret)
# ---------------------------------------------------------------------------

def test_compare_to_references_recovers_mixture():
    x = np.linspace(0, 10, 200)
    a = np.exp(-((x - 3) / 1.0) ** 2)
    b = np.exp(-((x - 7) / 1.0) ** 2)
    target = 0.7 * a + 0.3 * b
    out = compare_to_references(x, target, [
        {"name": "A", "axis": x, "intensity": a},
        {"name": "B", "axis": x, "intensity": b},
    ])
    fracs = {c["name"]: c["fraction"] for c in out["components"]}
    assert fracs["A"] == pytest.approx(0.7, abs=0.02)
    assert fracs["B"] == pytest.approx(0.3, abs=0.02)
    assert out["fit_r2"] > 0.999


def test_compare_to_references_accepts_legacy_loss_key():
    x = np.linspace(0, 10, 100)
    a = np.exp(-((x - 5) / 1.0) ** 2)
    out = compare_to_references(x, a, [{"name": "A", "loss": x, "intensity": a}])
    assert out["components"][0]["fraction"] == pytest.approx(1.0)


def test_xrs_wrapper_keeps_caveat():
    from beamtimehero_cli.science.xrs.interpret import compare_xrs_to_references
    x = np.linspace(0, 10, 100)
    a = np.exp(-((x - 5) / 1.0) ** 2)
    out = compare_xrs_to_references(x, a, [{"name": "A", "loss": x, "intensity": a}])
    assert "regime_caveat" in out
