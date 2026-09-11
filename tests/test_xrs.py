"""Tests for the XRS (X-ray Raman) pipeline + the Phase-0 counter/normalization
override that unblocked it.

Follows the repo convention: pure-math on synthetic arrays with known ground
truth, plus one end-to-end pass through ``execute_tool`` with the scan loaders
monkeypatched (no silx / data-file dependency). The synthetic model is the XRS
shape that broke the old pipeline: a flat dark ``vortDT`` channel at a huge DC
offset next to the real ``vortDT2`` signal — a bump on a sloping Compton
background, with an elastic line for the loss-axis zero.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from beamtimehero_cli.science.reduce.counters import counter_selection_warning, pick_active_counter
from beamtimehero_cli.science.reduce.normalize import NORMALIZATION_MODES, normalize_series
from beamtimehero_cli.science.xrs.calibrate import fit_elastic_line, q_from_two_theta
from beamtimehero_cli.science.xrs.reduce import align_and_average, area_normalize, subtract_compton_background, sum_crystals


# ---------------------------------------------------------------------------
# Synthetic model
# ---------------------------------------------------------------------------

ELASTIC_CENTER = 10250.3
O_K_LOSS = 540.0  # O K-edge feature center in energy loss


def _data_df(seed=0, n=300):
    """A data scan: flat dark vortDT + real vortDT2 (Compton slope + O-K bump)."""
    energy = np.linspace(10190.0, 10800.0, n)
    loss = energy - ELASTIC_CENTER
    ratio = 0.002 + 1.5e-6 * loss + 0.0010 * np.exp(-((loss - O_K_LOSS) / 6.0) ** 2)
    rng = np.random.RandomState(seed)
    i0 = np.full(n, 3.7e4)
    df = pd.DataFrame({
        "I0": i0,
        "vortDT": 62000.0 + 3000.0 * rng.rand(n),          # flat dark channel
        "vortDT2": ratio * i0 + rng.normal(0, 40, n),      # real signal (counts)
    }, index=energy)
    df.attrs["count_time"] = 1.0
    df.attrs["motor_positions"] = {}
    return df


def _elastic_df(n=61):
    energy = np.linspace(10248.0, 10252.5, n)
    i0 = np.full(n, 3.7e4)
    peak = 1.2e4 * np.exp(-0.5 * ((energy - ELASTIC_CENTER) / 0.40) ** 2)
    df = pd.DataFrame({
        "I0": i0,
        "vortDT": 62000.0 + np.zeros(n),
        "vortDT2": peak + 20.0,
    }, index=energy)
    df.attrs["count_time"] = 1.0
    df.attrs["motor_positions"] = {}
    return df


# ---------------------------------------------------------------------------
# Phase 0 — counter guardrail + normalization modes
# ---------------------------------------------------------------------------

def test_counter_guardrail_flags_flat_dark_channel():
    df = _data_df()
    picked, _reason = pick_active_counter(df)
    assert picked == "vortDT"  # the flat dark channel out-maxes the signal
    warn = counter_selection_warning(df, picked)
    assert warn is not None and "vortDT2" in warn and "flat" in warn.lower()
    # The real signal channel does not trip the guardrail.
    assert counter_selection_warning(df, "vortDT2") is None


def test_normalization_modes():
    df = _data_df()
    for mode in NORMALIZATION_MODES:
        e, v = normalize_series(df, "vortDT2", mode=mode)
        assert len(e) == len(v) == len(df)
    # raw is the unscaled counter; divide_by_i0 is signal/I0 (smaller).
    _, raw = normalize_series(df, "vortDT2", mode="raw")
    _, byi0 = normalize_series(df, "vortDT2", mode="divide_by_i0")
    assert raw.max() > byi0.max()
    with pytest.raises(ValueError):
        normalize_series(df, "vortDT2", mode="not_a_mode")


# ---------------------------------------------------------------------------
# analysis/xrs.py pure math
# ---------------------------------------------------------------------------

def test_q_from_two_theta():
    # q = 4π sin(θ)/λ, λ = hc/E. Backscatter (2θ=180) gives the max, ~2k.
    q90 = q_from_two_theta(10000.0, 90.0)
    q180 = q_from_two_theta(10000.0, 180.0)
    assert q180 > q90 > 0
    assert q90 == pytest.approx(4 * np.pi * np.sin(np.pi / 4) / (12398.42 / 10000.0), rel=1e-6)


def test_fit_elastic_line_recovers_center_and_resolution():
    df = _elastic_df()
    fit = fit_elastic_line(df.index.values, df["vortDT2"].values)
    assert fit["fit_ok"]
    assert fit["elastic_center_ev"] == pytest.approx(ELASTIC_CENTER, abs=0.05)
    # FWHM = 2.3548*sigma, sigma=0.40 -> ~0.94 eV
    assert fit["resolution_fwhm_ev"] == pytest.approx(0.94, abs=0.15)


def test_align_and_average_and_sem():
    loss = np.linspace(500, 580, 200)
    base = 0.002 + 1e-6 * loss
    reps_i = [base + np.random.RandomState(k).normal(0, 1e-4, len(loss)) for k in range(6)]
    out = align_and_average([loss] * 6, reps_i)
    assert out["n_reps"] == 6
    # Mean recovers the noiseless base to within the shot noise (mean abs error
    # below the per-rep sigma; per-point excursions up to a few SEM are expected).
    assert np.nanmean(np.abs(out["mean"] - base)) < 1e-4
    assert np.all(out["sem"][np.isfinite(out["sem"])] >= 0)


def test_compton_subtraction_isolates_bump():
    loss = np.linspace(500, 600, 400)
    background = 0.002 + 1.5e-6 * loss
    bump = 0.0010 * np.exp(-((loss - O_K_LOSS) / 6.0) ** 2)
    bg = subtract_compton_background(loss, background + bump, 528, 555, model="linear")
    i_peak = int(np.nanargmax(bg["subtracted"]))
    assert loss[i_peak] == pytest.approx(O_K_LOSS, abs=3.0)
    # Background subtracted → flanks near zero.
    flank = (loss < 520) | (loss > 575)
    assert np.nanmax(np.abs(bg["subtracted"][flank])) < 3e-4


def test_reject_outlier_channels():
    loss = np.linspace(500, 580, 200)
    good = 0.002 + 1e-6 * loss + 0.0010 * np.exp(-((loss - 540) / 6) ** 2)
    chans = [good * (1 + 0.03 * k) + np.random.RandomState(k).normal(0, 2e-5, len(loss))
             for k in range(4)]
    chans.append(np.full(len(loss), 0.002))        # dead flat -> low SNR
    # bump at a clearly different loss (515, not 540) -> shape deviates
    chans.append(0.002 + 1e-6 * loss + 0.0010 * np.exp(-((loss - 515) / 6) ** 2))
    res = sum_crystals([loss] * 6, chans, reject=True)
    assert res["n_channels_used"] == 4
    assert res["n_channels_total"] == 6


def test_area_normalize():
    loss = np.linspace(500, 560, 200)
    inten = np.exp(-((loss - 530) / 5) ** 2)
    out = area_normalize(loss, inten, 500, 560)
    trapz = getattr(np, "trapezoid", None) or np.trapz
    assert trapz(out["normalized"], loss) == pytest.approx(1.0, rel=1e-6)


# ---------------------------------------------------------------------------
# Interpretation
# ---------------------------------------------------------------------------

def test_xrs_edges_lookup_and_suggest():
    from beamtimehero_cli.science.tables.xrs_edges import classify_xrs_family, get_xrs_edge_info, suggest_xrs_edge
    info = get_xrs_edge_info("O", "K")
    assert info["family"] == "low_Z_K"
    assert info["tabulated_energy_ev"] == pytest.approx(543, abs=3)
    assert classify_xrs_family("Ni", "L3") == "3d_L"
    assert suggest_xrs_edge(515, 580)["best"]["element"] == "O"


def test_xrs_descriptors_on_clean_edge():
    from beamtimehero_cli.science.xrs.descriptors import extract_xrs_descriptors
    loss = np.linspace(515, 580, 300)
    # rising edge at 535 that peaks (white line ~538) then settles
    edge = (0.6 / (1 + np.exp(-(loss - 535) / 1.2))
            + 0.4 * np.exp(-((loss - 538) / 2.5) ** 2)
            - 0.15 * (loss > 545) * (1 - np.exp(-(np.clip(loss - 545, 0, None)) / 8)))
    desc, _arr = extract_xrs_descriptors(loss, edge, edge_window=(530, 560))
    assert desc["onset"]["onset_loss_ev"] == pytest.approx(535, abs=2.0)
    assert desc["white_line"]["peak_loss_ev"] == pytest.approx(538, abs=2.0)
    assert desc["feature_snr"]["snr"] is None or desc["feature_snr"]["snr"] >= 0


def test_q_dependence_classification():
    from beamtimehero_cli.science.xrs.interpret import interpret_q_dependence
    rising = interpret_q_dependence([{"q": 2, "value": 1.0}, {"q": 5, "value": 1.5},
                                        {"q": 8, "value": 2.1}])
    assert "non-dipole" in rising["estimate"]
    flat = interpret_q_dependence([{"q": 2, "value": 1.0}, {"q": 5, "value": 1.02},
                                      {"q": 8, "value": 0.99}])
    assert "dipole-like" in flat["estimate"]


def test_lcf_recovers_fractions():
    from beamtimehero_cli.science.xrs.interpret import compare_xrs_to_references
    loss = np.linspace(520, 560, 200)
    a = np.exp(-((loss - 535) / 3) ** 2)
    b = np.exp(-((loss - 545) / 3) ** 2)
    target = 0.7 * a + 0.3 * b
    res = compare_xrs_to_references(loss, target,
                                       [{"name": "A", "loss": loss, "intensity": a},
                                        {"name": "B", "loss": loss, "intensity": b}])
    fracs = {c["name"]: c["fraction"] for c in res["components"]}
    assert fracs["A"] == pytest.approx(0.7, abs=0.05)
    assert fracs["B"] == pytest.approx(0.3, abs=0.05)
    assert res["fit_r2"] > 0.99


def test_oxidation_refuses_absolute_without_calibration():
    from beamtimehero_cli.science.xrs.interpret import interpret_xrs_oxidation_state
    from beamtimehero_cli.science.tables.xrs_edges import get_xrs_edge_info
    desc = {"edge": get_xrs_edge_info("O", "K"), "onset": {"onset_loss_ev": 531.0},
            "pre_edge_fraction": 0.15, "white_line": {"peak_loss_ev": 540.0}, "flags": []}
    v = interpret_xrs_oxidation_state(desc, calibration=None)
    assert "refused_absolute_no_calibration" in v["flags"]
    assert "covalency" in v["narration"].lower() or "oxygen-redox" in v["narration"].lower()


# ---------------------------------------------------------------------------
# End-to-end through execute_tool (loaders monkeypatched)
# ---------------------------------------------------------------------------

@pytest.fixture
def xrs_scans(monkeypatch, tmp_path):
    """Patch the scan loaders so tools see scan 1 = elastic, 2..6 = data."""
    from beamtimehero_cli import config as bl_config
    from beamtimehero_cli.spec_data import scans, local_data

    monkeypatch.setattr(bl_config, "BL_SCAN_DIR", tmp_path)  # elastic store lands here

    dfs = {1: _elastic_df()}
    for sn in range(2, 7):
        dfs[sn] = _data_df(seed=sn)

    def fake_read(file_name, scan_number):
        return dfs.get(int(scan_number))

    monkeypatch.setattr(scans, "read_processed_scan", fake_read)
    monkeypatch.setattr(local_data, "get_scan_numbers_for_file", lambda fn: sorted(dfs))
    monkeypatch.setattr(scans, "get_most_recent_file", lambda: "xrs_test")
    monkeypatch.setattr(local_data, "get_most_recent_file", lambda: "xrs_test")
    return "xrs_test"


def _run(category, name, payload):
    import json
    from beamtimehero_cli.tool_catalog import execute_tool
    text, images = execute_tool(category, name, payload)
    return json.loads(text), images


def test_phase0_average_scans_counter_override(xrs_scans):
    data = [2, 3, 4, 5, 6]
    # Explicit correct counter, XRS normalization → no warning, right counter.
    out, _ = _run(("spec-file",), "average_scans",
                  {"file_name": xrs_scans, "counter": "vortDT2",
                   "normalization": "divide_by_i0", "scan_numbers": data})
    assert out["active_counter"] == "vortDT2"
    assert out["normalization"] == "divide_by_i0"
    assert "error" not in out
    # Omitting the counter auto-picks the flat vortDT AND warns.
    auto, _ = _run(("spec-file",), "average_scans",
                   {"file_name": xrs_scans, "scan_numbers": data})
    assert auto["active_counter"] == "vortDT"
    assert "counter_warning" in auto and "vortDT2" in auto["counter_warning"]


def test_xrs_pipeline_end_to_end(xrs_scans):
    data = [2, 3, 4, 5, 6]
    # 1. calibrate the loss axis from the elastic scan
    cal, imgs = _run(("xrs",), "calibrate_energy_loss",
                     {"file_name": xrs_scans, "scan_number": 1, "counter": "vortDT2"})
    assert cal["elastic_center_ev"] == pytest.approx(ELASTIC_CENTER, abs=0.1)
    assert imgs  # elastic-fit plot
    # 2. average on the loss axis using the stored calibration
    avg, imgs = _run(("xrs",), "average_xrs_scans",
                     {"file_name": xrs_scans, "counter": "vortDT2", "scan_numbers": data})
    assert avg["axis"] == "energy_loss_ev"
    assert avg["n_reps"] == 5 and imgs
    # 3. Compton-subtract and find the O-K bump near loss=540
    sub, imgs = _run(("xrs",), "subtract_compton_background",
                     {"file_name": xrs_scans, "counter": "vortDT2", "scan_numbers": data,
                      "edge_lo": 528, "edge_hi": 555, "model": "linear"})
    assert sub["edge_spectrum"]["peak_loss_ev"] == pytest.approx(O_K_LOSS, abs=4.0)
    # 4. capstone interpretation
    summ, imgs = _run(("xrs",), "summarize_xrs_chemistry",
                      {"file_name": xrs_scans, "counter": "vortDT2", "scan_numbers": data,
                       "edge_lo": 528, "edge_hi": 555, "element": "O", "edge": "K"})
    assert "narration" in summ and summ["quality"]["verdict"] in (
        "publication", "usable", "marginal", "noise_limited", "unknown")
    assert imgs


def test_tag_crystal_q_tool(xrs_scans):
    out, _ = _run(("xrs",), "tag_crystal_q",
                  {"incident_energy_ev": 10000.0, "two_thetas": [30, 90, 150],
                   "counters": ["c1", "c2", "c3"]})
    qs = [c["q_inv_angstrom"] for c in out["channels"]]
    assert qs == sorted(qs)  # q increases with 2θ
    assert out["channels"][0]["regime"].startswith("low-q")
