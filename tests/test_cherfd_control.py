"""Audited dispatch, safety switches, the mock server, and the live seam."""
from __future__ import annotations

from tests._cherfd_fixtures import *  # noqa: F401,F403 -- isolation fixtures
from tests._cherfd_fixtures import REAL_RUN, run_tool as run  # noqa: F401

import socket
import time

import pytest

from beamtimehero_cli.cherfd_control import settings as config
from beamtimehero_cli import audited_cherfd as audited
from beamtimehero_cli.cherfd_control import transport
from beamtimehero_cli.audited_cherfd import audited_cherfd as req



@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("mock mode opened a socket")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.mark.parametrize("value", [None, "1", "", "true", "True", "false", "False", "no", " 0"])
def test_mock_unless_exactly_zero(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CHERFD_MOCK", raising=False)
    else:
        monkeypatch.setenv("CHERFD_MOCK", value)
    assert config.mock_enabled()


def test_zero_is_live(monkeypatch):
    monkeypatch.setenv("CHERFD_MOCK", "0")
    assert not config.mock_enabled()


def test_read_needs_no_justification(no_network):
    r = req("status")
    assert r["ok"] and r["transport"] == "mock"
    assert r["result"]["status"]["state"] == "idle"


def test_action_requires_justification(no_network):
    r = req("move_energy", {"energy": 9000}, "")
    assert not r["ok"] and "justification" in r["error"]


def test_unknown_command_refused():
    assert req("caput", {"pv": "X"})["kind"] == "unknown"


def test_scan_lifecycle_in_mock(no_network):
    r = req("start_acquisition", {"command": "cherfd 8968 9100 .4 100", "sweeps": 1}, "test")
    assert r["ok"] and r["action_id"]
    assert req("status")["result"]["status"]["acquisition_active"]
    deadline = time.time() + 5
    while req("spec_status")["result"]["state"] != "1":
        assert time.time() < deadline
        time.sleep(0.02)
    assert req("results")["ok"]


def test_start_while_busy_is_silently_ignored_like_the_real_server(no_network, monkeypatch):
    monkeypatch.setenv("CHERFD_MOCK_TIME_SCALE", "1")
    req("start_acquisition", {"command": "cherfd 8968 9100 .4 100", "sweeps": 3}, "first")
    r = req("start_acquisition", {"command": "cherfd 7100 7200 1 20"}, "second")
    assert r["ok"], "the route answers success regardless"
    cur = req("status")["result"]["status"]["current_scan"]
    assert cur["command"] == "cherfd 8968 9100 .4 100" and cur["total_sweeps"] == 3


def test_stop_latches_until_cleared(no_network, monkeypatch):
    monkeypatch.setenv("CHERFD_MOCK_TIME_SCALE", "1")
    req("start_acquisition", {"command": "cherfd 8968 9100 .4 100", "sweeps": 5}, "go")
    assert req("stop", {}, "halt")["ok"]
    assert req("status")["result"]["status"]["state"] == "Stopped"
    assert req("clear_stop", {}, "clear")["ok"]
    assert req("status")["result"]["status"]["state"] == "idle"


def test_mock_validation_errors_match_routes(no_network):
    assert req("move_gap", {"gap": 30}, "x")["ok"] is False
    assert "anchor" in req("set_table_tracking", {"enable": True}, "x")["error"]
    assert req("daq_count", {"duration_s": 100}, "x")["http_status"] == 422


@pytest.mark.parametrize("key", ["spec_write_enabled", "cherfd_write_enabled"])
def test_write_switch_refuses_actions_but_not_stops(key, no_network, switches):
    switches({key: False})
    r = req("move_energy", {"energy": 9000}, "x")
    assert not r["ok"] and "SAFETY SWITCH" in r["error"]
    assert req("stop", {}, "emergency")["kind"] == "stop"
    assert "SAFETY SWITCH" not in (req("stop_gap", {}, "emergency").get("error") or "")
    assert req("status")["ok"], "reads unaffected by the write switch"


def test_unreadable_switch_fails_closed_for_writes_open_for_reads(no_network, switches):
    switches("{not json")
    assert "SAFETY SWITCH" in req("move_energy", {"energy": 9000}, "x")["error"]
    assert req("status")["ok"]


def test_read_switch(no_network, switches):
    switches({"cherfd_read_enabled": False})
    assert "SAFETY SWITCH" in req("status")["error"]


def test_actions_land_in_beamtimehero_action_log(no_network):
    from beamtimehero_cli.action_log.db import get_session
    from beamtimehero_cli.action_log.models import ActionLog
    from sqlmodel import select
    r = req("set_data_path", {"file_dir": "d", "file_root": "r"}, "audit trail test")
    with get_session() as s:
        row = s.exec(select(ActionLog).where(ActionLog.id == r["action_id"])).one()
    assert row.command == "cherfd:set_data_path"
    assert row.justification == "audit trail test"
    assert row.success and "mock://server/set_data_path" in row.spec_string_sent


def test_live_path_builds_the_right_request(monkeypatch):
    """CHERFD_MOCK=0 routes to requests; intercepted here so nothing is sent."""
    import requests
    monkeypatch.setenv("CHERFD_MOCK", "0")
    monkeypatch.setenv("CHERFD_SERVER_URL", "http://example.invalid:5002")
    calls = []

    class R:
        status_code = 200
        text = "{}"
        def json(self):
            return {"success": True, "message": "ok"}

    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: calls.append((url, json)) or R())
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("unexpected GET"))
    r = req("move_energy", {"energy": 9000}, "live seam test")
    assert r["ok"] and r["transport"] == "http"
    assert calls == [("http://example.invalid:5002/move_energy", {"energy": 9000.0})]


def test_service_ok_semantics():
    rep = transport.Reply(True, 200, {"success": False, "message": "No scan running"}, "mock", "", 0)
    assert not audited.service_ok(rep)
    assert audited.service_ok(transport.Reply(True, 202, {"accepted": True}, "mock", "", 0))
    assert not audited.service_ok(transport.Reply(True, 409, {"detail": "busy"}, "mock", "", 0))
