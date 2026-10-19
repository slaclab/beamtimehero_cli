"""Edge scans (``cscan Fe xanes``): the scan server's default per edge,
expanded through /check_command before a tool validates or sends it."""
from __future__ import annotations

import pytest

from tests._cherfd_fixtures import *  # noqa: F401,F403 -- isolation fixtures
from tests._cherfd_fixtures import run_tool as run

from beamtimehero_cli.cherfd_control import mock_server
from beamtimehero_cli.science.cherfd import command as cmd_sci

FE_XANES = "cscan 6962 7092 1 7142 0.31 7262 1 125"


@pytest.fixture
def bl41(monkeypatch):
    monkeypatch.setenv("BTH_STATION", "bl41")


def test_shorthand_is_recognised():
    for c in ("cscan Fe", "cscan Fe xanes 80", "cherfd PtL3 exafs", "Fe"):
        assert cmd_sci.is_edge_shorthand(c), c
    for c in ("cscan 7100 7150 .2 20", "cscan 6309 6519 1 k10 1 100 e0=6539", "cscan", ""):
        assert not cmd_sci.is_edge_shorthand(c), c
    with pytest.raises(ValueError, match="names an edge scan"):
        cmd_sci.parse_command("cscan Fe xanes")


def test_check_command_expands(bl41):
    r, _ = run("cherfd_check_command", command="cscan Fe xanes 80")
    assert r["ok"] and r["edge_scan"] == "FeK_xanes"
    assert r["command"] == FE_XANES[:-3] + "80"
    plain, _ = run("cherfd_check_command", command=FE_XANES)
    assert plain["ok"] and plain["edge_scan"] is None


def test_start_scan_sends_the_expanded_command(bl41):
    rd, _ = run("cherfd_check_readiness", command="cscan Fe xanes", include_beam_status=False)
    assert rd["ready"], rd["blockers"]
    assert rd["info"]["edge_scan"] == {"key": "FeK_xanes", "command": FE_XANES}
    s, _ = run("cherfd_start_scan", command="cscan Fe xanes", justification="edge scan test")
    assert s["ok"] and s["started"], s
    assert s["edge_scan"] == "FeK_xanes" and s["expanded_command"] == FE_XANES
    # the server got the full command, so it reports no edge scan of its own
    assert s["result"]["command"] == FE_XANES and s["result"]["edge_scan"] is None
    assert s["estimate"]["total_s"] > 0


def test_disabled_or_unknown_edge_is_refused_before_sending(bl41):
    s, _ = run("cherfd_start_scan", command="cscan Mn", justification="x")
    assert s["refused"] == "edge_scan" and "not enabled" in s["error"]
    s, _ = run("cherfd_start_scan", command="cscan Zr xanes", justification="x")
    assert s["refused"] == "edge_scan" and "no XANES scan" in s["error"]
    rd, _ = run("cherfd_check_readiness", command="cscan Mn", include_beam_status=False)
    assert not rd["ready"] and any("not enabled" in b for b in rd["blockers"])


def test_server_without_edge_scans(bl41, monkeypatch):
    def old_check(st, body):   # a server before edge scans: no edge_scan key, a parse error
        return 200, {"success": True, "ok": False, "command": body["command"],
                     "error": f"Failed to parse command '{body['command']}': could not convert"}
    monkeypatch.setitem(mock_server.ROUTES, "check_command", old_check)
    s, _ = run("cherfd_start_scan", command="cscan Fe", justification="x")
    assert s["refused"] == "edge_scan" and "predates edge scans" in s["error"]


def test_readiness_reports_an_unparsable_command():
    rd, _ = run("cherfd_check_readiness", command="cherfd 8968 abc .4 100", include_beam_status=False)
    assert not rd["ready"] and any("non-numeric" in b for b in rd["blockers"])
    assert rd["info"]["command_validation"]["effective_command"] is None
