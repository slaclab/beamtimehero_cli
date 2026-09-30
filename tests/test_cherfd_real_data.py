"""Against the sibling cherfd_claude checkout; skipped when it is absent."""
from __future__ import annotations

from tests._cherfd_fixtures import *  # noqa: F401,F403 -- isolation fixtures
from tests._cherfd_fixtures import REAL_RUN, run_tool as run  # noqa: F401


import pytest

from beamtimehero_cli.cherfd_control import simulator as sim


def test_simulator_matches_our_step_count(sibling):
    r = sim.simulate("cherfd 8968 9100 .4 100")
    assert r["ok"] and r["checks"]["commanded_steps"] == 38652 and r["checks"]["steps_match"]


def test_simulator_refusal_is_reported(sibling):
    out, _ = run("cherfd_simulate_scan", command="cherfd 8968 8968.05 .2 9100 1 100", plot=False)
    assert out["ok"] is False or out.get("warnings") is not None


def test_simulator_unavailable_message():
    out, _ = run("cherfd_simulate_scan", command="cherfd 8968 9100 .4 100")
    assert not out["ok"] and "CHERFD_PROJECT_DIR" in out["error"]


def test_real_run_quality(real_data):
    q, _ = run("cherfd_assess_run_quality", run_id=REAL_RUN, signal="vortex")
    assert q["overall"] == "ok" and len(q["sweeps"]) == 10
    assert q["sweeps"][0]["recorded_pairing_max_offset_frames"] >= 1


def test_real_run_direction_offset_and_e0(real_data):
    c, imgs = run("cherfd_compare_directions", run_id=REAL_RUN, signal="vortex", normalize_by="adc_1")
    assert -0.12 < c["rev_minus_fwd_shift_ev"] < -0.04 and imgs
    assert -1.6 < c["shift_in_frames"] < -0.6
    cal, _ = run("cherfd_assess_energy_calibration", run_id=REAL_RUN, signal="vortex",
                 normalize_by="adc_1", element="Cu")
    assert cal["measured_e0_ev"] == pytest.approx(8978.5, abs=0.4)


def test_real_run_merge_and_export_reads_back_in_beamtimehero(real_data, tmp_path):
    m, imgs = run("cherfd_merge_sweeps", run_id=REAL_RUN, signal="vortex", normalize_by="adc_1",
                  direction_correction_ev=-0.076)
    assert m["empty_bins"] == 0 and imgs and max(v for v in m["merged"] if v) > 1e-5
    ex, _ = run("cherfd_export_merged", run_id=REAL_RUN, signal="vortex", normalize_by="adc_1")
    from beamtimehero_cli.spec_data.twocol_ascii import read_twocol
    e, y, meta = read_twocol(ex["path"])
    assert meta["n_points"] == ex["points"] and e[0] < 8970 < 9140 < e[-1]


def test_real_gap_tracking(real_data):
    g, _ = run("cherfd_check_gap_tracking", run_id=REAL_RUN, sweeps=[1])
    assert all(s["tracking_ok"] for s in g["sweeps"])
    assert 0.0 < g["sweeps"][0]["implied_calibration_offset_mm"] < 0.05


def test_real_logs(sibling):
    lst, _ = run("cherfd_list_logs")
    assert lst["files"]
    tail, _ = run("cherfd_read_log", name=lst["files"][0]["name"], lines=5)
    assert len(tail["lines"]) <= 5
