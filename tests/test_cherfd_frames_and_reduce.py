"""Synthetic sweeps with known truth: ordering, alignment, merging, shifts."""
from __future__ import annotations

from tests._cherfd_fixtures import *  # noqa: F401,F403 -- isolation fixtures
from tests._cherfd_fixtures import REAL_RUN, run_tool as run  # noqa: F401

import numpy as np
import pytest

from beamtimehero_cli.science.cherfd import analysis, frames, policy, reduce

PERIOD = 10000.0


def edge(e, e0=8979.0):
    return 100 + 400 / (1 + np.exp(-(e - e0) / 1.5))


def synthetic_sweep(rng, n=2000, e_start=8960.0, e_end=9060.0, scramble=True, lag_ev=0.0):
    k = np.arange(1, n + 1)
    energy = np.linspace(e_start, e_end, n) + lag_ev
    detector = rng.poisson(edge(energy - lag_ev)).astype(float)    # frame order == trigger order
    trig = k * PERIOD
    order = np.arange(n)
    if scramble:                                                  # scramble within 20-frame chunks
        for s in range(0, n, 20):
            rng.shuffle(order[s:s + 20])
    cols = {"trig_1": trig[order].astype(float), "absev": energy[order],
            "adc_1": np.full(n, policy.ADC_OFFSET + 1e7)}
    # status frame and trailing NaN row, as in real files
    cols = {c: np.r_[v, np.nan] for c, v in cols.items()}
    cols["trig_1"][-1] = np.nan
    cols["trig_1"] = np.r_[cols["trig_1"], float(1 << 31) + 5]
    cols["absev"] = np.r_[cols["absev"], 205917.0]
    cols["adc_1"] = np.r_[cols["adc_1"], np.nan]
    det = {"vortex": np.r_[detector, np.nan, np.nan]}
    return cols, det, detector, energy


def test_clean_orders_and_realigns():
    rng = np.random.default_rng(0)
    cols, det, truth, energy = synthetic_sweep(rng)
    c = frames.clean_sweep(cols, detector=det, energy_range=(8960, 9060))
    a, qc = c["arrays"], c["qc"]
    assert np.all(np.diff(a["trigger"]) == 1)
    assert np.allclose(a["absev"], energy)
    assert np.array_equal(a["vortex"], truth), "trigger alignment recovers the true pairing"
    assert qc["status_frames_dropped"] == 1 and qc["nan_rows_dropped"] == 1
    assert qc["missing_triggers"] == 0
    assert qc["recorded_pairing_max_offset_frames"] > 0


def test_row_alignment_reproduces_recorded_pairing():
    rng = np.random.default_rng(1)
    cols, det, truth, _ = synthetic_sweep(rng)
    c = frames.clean_sweep(cols, detector=det, align="row")
    assert not np.array_equal(c["arrays"]["vortex"], truth)


def test_missing_triggers_counted():
    rng = np.random.default_rng(2)
    cols, det, _, _ = synthetic_sweep(rng, scramble=False)
    cols = {k: v[20:] for k, v in cols.items()}             # first DMA chunk discarded
    c = frames.clean_sweep(cols, detector=det)
    assert c["qc"]["missing_leading_triggers"] == 20
    assert c["qc"]["missing_triggers"] == 20


def test_quality_flags_all_zero_detector():
    rng = np.random.default_rng(3)
    cols, det, _, _ = synthetic_sweep(rng)
    det = {"vortex": np.zeros_like(det["vortex"])}
    c = frames.clean_sweep(cols, detector=det)
    q = frames.sweep_quality(c, signal="vortex")
    assert q["verdict"] == "fail"
    assert any(i["code"] == "signal_all_zero" and "xtc 1" in i["message"] for i in q["issues"])


def _clean(rng, **kw):
    cols, det, _, _ = synthetic_sweep(rng, **kw)
    c = frames.clean_sweep(cols, detector=det)
    c["arrays"]["adc_1"] = frames.adc_signed(c["arrays"]["adc_1"])
    return c["arrays"]


def test_merge_ratio_of_sums_preserves_intensity():
    rng = np.random.default_rng(4)
    sw = [{"label": i, "arrays": _clean(rng)} for i in range(4)]
    b = reduce.bin_sweeps(sw, signal="vortex", bin_ev=0.5)
    hi = b["energy"] > 9040
    assert np.nanmean(b["merged"][hi]) == pytest.approx(500, rel=0.02), "absolute level kept"
    bn = reduce.bin_sweeps(sw, signal="vortex", normalize_by="adc_1", bin_ev=0.5)
    assert np.nanmean(bn["merged"][hi]) == pytest.approx(500 / 1e7, rel=0.02)
    assert np.all(np.isfinite(bn["sem"][bn["frames_per_bin"] > 0]))


def test_convergence_is_statistics_limited_for_poisson():
    rng = np.random.default_rng(5)
    sw = [{"label": i, "arrays": _clean(rng)} for i in range(12)]
    b = reduce.bin_sweeps(sw, signal="vortex", bin_ev=0.5)
    conv = reduce.convergence([s["values"] for s in b["per_sweep"]],
                              [s["frames"] for s in b["per_sweep"]], target_relative_noise=0.005)
    assert conv["fit"]["alpha"] == pytest.approx(1.0, abs=0.25)
    rel1 = conv["fit"]["relative_noise_1"]
    assert conv["sweeps_needed"] == pytest.approx((rel1 / 0.005) ** (2 / conv["fit"]["alpha"]), abs=1)


@pytest.mark.parametrize("true_shift", [-0.3, 0.0, 0.17])
def test_best_shift_recovers_known_shift(true_shift):
    e = np.arange(8950, 9050, 0.1)
    ref = edge(e)
    moved = 1.3 * edge(e - true_shift) + 7        # scale/offset must not bias it
    r = reduce.best_shift(e, ref, e, moved)
    assert r["shift_ev"] == pytest.approx(true_shift, abs=0.01)


def test_compare_directions_and_symmetric_correction():
    rng = np.random.default_rng(6)
    fwd = [{"label": f"{i}f", "direction": "fwd", "arrays": _clean(rng, lag_ev=+0.06)} for i in range(3)]
    rev = [{"label": f"{i}r", "direction": "rev", "arrays": _clean(rng, lag_ev=-0.06)} for i in range(3)]
    r = analysis.compare_directions(fwd, rev, signal="vortex", normalize_by=None, bin_ev=0.1)
    assert r["rev_minus_fwd_shift_ev"] == pytest.approx(-0.12, abs=0.03)
    fixed = analysis.apply_direction_correction(fwd + rev, r["suggested_direction_correction_ev"])
    r2 = analysis.compare_directions(fixed[:3], fixed[3:], signal="vortex", normalize_by=None, bin_ev=0.1)
    assert abs(r2["rev_minus_fwd_shift_ev"]) < 0.03


def test_energy_calibration_angle_correction():
    e = np.arange(8950, 9050, 0.1)
    r = analysis.energy_calibration(e, edge(e, e0=8978.5), 8979.0)
    assert r["measured_minus_reference_ev"] == pytest.approx(-0.5, abs=0.1)
    # edge reads low -> needs a higher energy -> a smaller Bragg angle
    assert r["bragg_angle_correction_deg"] < 0 and r["significant"]


def test_noise_estimate_ignores_smooth_structure():
    rng = np.random.default_rng(7)
    e = np.linspace(0, 10, 4000)
    y = 50 * np.sin(e) + rng.normal(0, 2.0, e.size)
    assert reduce.noise_estimate(y) == pytest.approx(2.0, rel=0.1)
