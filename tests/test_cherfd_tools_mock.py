"""Tool-level flows through execute_tool against the mock."""
from __future__ import annotations

from tests._cherfd_fixtures import *  # noqa: F401,F403 -- isolation fixtures
from tests._cherfd_fixtures import REAL_RUN, run_tool as run  # noqa: F401




def test_readiness_then_scan_then_wait():
    r, _ = run("cherfd_check_readiness", command="cherfd 8968 9100 .4 100", include_beam_status=False)
    assert r["ready"], r["blockers"]
    s, _ = run("cherfd_start_scan", command="cherfd 8968 9100 .4 100", sweeps=1,
               justification="flow test")
    assert s["ok"] and s["started"], s
    w, _ = run("cherfd_wait_for_scan", timeout_s=20, poll_s=0.2)
    assert w["done"] and w["final_state"] == "idle"
    res, _ = run("cherfd_get_scan_results")
    assert res["ok"] and res["n_rows"] > 0


def test_start_refuses_invalid_command_before_sending():
    r, _ = run("cherfd_start_scan", command="cherfd 4000 9100 .4 100", justification="x")
    assert r["refused"] == "validation"


def test_start_refuses_when_stopped():
    run("cherfd_start_scan", command="cherfd 8968 9100 .4 100", sweeps=100, justification="long")
    run("cherfd_stop_scan", justification="stop")
    r, _ = run("cherfd_start_scan", command="cherfd 8968 9100 .4 100", justification="again")
    assert r["refused"] == "controller_not_idle" and r["state"] == "Stopped"
    rd, _ = run("cherfd_check_readiness", include_beam_status=False)
    assert not rd["ready"] and any("clear_stop" in b for b in rd["blockers"])


def test_move_energy_reports_expected_gap_with_server_offset():
    r, _ = run("cherfd_move_energy", energy_ev=9000, justification="park")
    assert r["ok"]
    assert abs(r["expected_gap"]["commanded_gap_mm"] - (8.103432445 + 0.03)) < 1e-9


def test_move_gap_limit_checked_locally():
    r, _ = run("cherfd_move_gap", gap_mm=5.0, justification="x")
    assert not r["ok"] and "outside" in r["error"]


def test_daq_count_waits_for_result():
    r, _ = run("cherfd_daq_count", duration_s=0.5, justification="count")
    assert r["ok"] and r["count_result"]["counters"]["ctr1"] == 50


def test_unknown_tool_envelope_matches_beamtimehero():
    from beamtimehero_cli.tool_catalog import execute_tool
    text, _ = execute_tool(("cherfd",), "nope", {})
    assert text == "Unknown tool: cherfd/nope"


def test_data_tools_say_what_to_configure():
    r, _ = run("cherfd_list_runs")
    assert not r["ok"] and "CHERFD_DATA_DIR" in r["error"]
