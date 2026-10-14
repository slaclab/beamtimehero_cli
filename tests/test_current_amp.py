"""zero_amp_offsets: the ssrl-current-amp Zero Offsets client (HTTP mocked)."""
from __future__ import annotations

import ast
import json

import pytest

from beamtimehero_cli import current_amp
from beamtimehero_cli.tool_catalog import tools_core
from beamtimehero_cli.tool_catalog.lineage import TOOL_LINEAGE

URL = "http://192.168.41.6:8570"


class _R:
    def __init__(self, body, code=200):
        self._b, self.status_code, self.text = body, code, json.dumps(body)

    def json(self):
        return self._b


def _status(state, **kw):
    return {"state": state, "step": None, "log": [{"t": 1.0, "level": "info", "msg": "i0: ok"}],
            "results": kw.get("results", {}), "started": 100.0,
            "finished": None if state == "running" else 122.5}


class _Server:
    """The three autozero routes; ``states`` is what successive GETs see after a start."""

    def __init__(self, states, enabled=True, start_code=200):
        self.states, self.enabled, self.start_code = list(states), enabled, start_code
        self.calls: list[tuple[str, str]] = []
        self.started = False

    def __call__(self, method, url, **kw):
        path = url[len(URL):]
        self.calls.append((method, path))
        if (method, path) == ("GET", "/api/autozero"):
            if not self.enabled:
                return _R({"enabled": False, "status": None})
            if not self.started:
                return _R({"enabled": True, "status": _status("idle")})
            st = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return _R({"enabled": True, "status": _status(st, results={
                "i1": {"adc": 2, "offset_before": "-2 nA", "offset_after": "-5 nA",
                       "v_before": -0.018, "v_after": 0.0022, "status": "zeroed"}})})
        if (method, path) == ("POST", "/api/autozero/start"):
            if self.start_code != 200:
                return _R({"detail": "offset zeroing is already running"}, self.start_code)
            self.started = True
            return _R({"status": "ok"})
        if (method, path) == ("POST", "/api/autozero/abort"):
            return _R({"status": "ok"})
        return _R({"detail": "Not Found"}, 404)


@pytest.fixture
def server(monkeypatch):
    import requests

    def make(*states, **kw):
        srv = _Server(states or ("done",), **kw)
        monkeypatch.setattr(requests, "request", srv)
        return srv

    monkeypatch.setenv("BTH_STATION", "bl41")
    monkeypatch.delenv(current_amp.URL_VAR, raising=False)
    return make


@pytest.fixture
def action_rows(monkeypatch, tmp_path):
    from beamtimehero_cli import audited_call as ac
    rows = []

    class _Row:
        def __init__(self, i):
            self.id = i

    monkeypatch.setattr(ac, "start_action", lambda **kw: rows.append({"start": kw}) or _Row(len(rows)))
    monkeypatch.setattr(ac, "mark_action_started", lambda _id: None)
    monkeypatch.setattr(ac, "finish_action", lambda action_id, **kw: rows[-1].update(finish=kw))
    monkeypatch.setenv("BEAMTIMEHERO_SAFETY_SWITCHES", str(tmp_path / "safety_switches.json"))
    return rows


def _no_sleep(monkeypatch):
    monkeypatch.setattr(current_amp.time, "sleep", lambda s: None)


def test_bl41_default_url(server):
    server()
    assert current_amp.server_url() == URL


def test_zero_offsets_runs_to_done(server):
    srv = server("running", "running", "done")
    out = current_amp.zero_offsets(sleep=lambda s: None)
    assert out["ok"] and out["state"] == "done"
    assert out["results"]["i1"]["offset_after"] == "-5 nA"
    assert out["log"] == ["i0: ok"] and out["duration_s"] == 22.5
    assert ("POST", "/api/autozero/start") in srv.calls
    assert ("POST", "/api/autozero/abort") not in srv.calls


@pytest.mark.parametrize("state", ["error", "aborted"])
def test_a_run_that_did_not_finish_is_not_ok(server, state):
    server("running", state)
    out = current_amp.zero_offsets(sleep=lambda s: None)
    assert not out["ok"] and out["state"] == state and "restored" in out["error"]


def test_deadline_aborts_the_run(server):
    srv = server("running")
    t = iter(range(0, 10_000, 5))
    out = current_amp.zero_offsets(wait_s=20, sleep=lambda s: None, clock=lambda: next(t))
    assert not out["ok"] and "aborted" in out["error"]
    assert srv.calls[-1] == ("POST", "/api/autozero/abort")


def test_refusals_before_starting(server):
    srv = server(enabled=False)
    out = current_amp.zero_offsets(sleep=lambda s: None)
    assert not out["ok"] and "not enabled" in out["error"]
    assert ("POST", "/api/autozero/start") not in srv.calls

    server(start_code=409)
    out = current_amp.zero_offsets(sleep=lambda s: None)
    assert not out["ok"] and out["status"] == 409 and "already running" in out["error"]


def test_tool_is_audited(server, action_rows, monkeypatch):
    _no_sleep(monkeypatch)
    server("running", "done")
    out = json.loads(tools_core.t_zero_amp_offsets({"justification": "beam off, re-zero"})[0])
    assert out["ok"] and out["kind"] == "action" and out["result"]["state"] == "done"
    assert action_rows[0]["start"]["command"] == "zero_amp_offsets"
    assert action_rows[0]["start"]["spec_string"] == f"POST {URL}/api/autozero/start"
    assert action_rows[0]["finish"]["success"] is True


def test_tool_refuses_without_justification(server, action_rows):
    srv = server()
    out = json.loads(tools_core.t_zero_amp_offsets({"justification": " "})[0])
    assert not out["ok"] and "justification" in out["error"]
    assert srv.calls == [] and action_rows == []


def test_tool_refused_without_a_server(monkeypatch, action_rows):
    monkeypatch.setenv("BTH_STATION", "bl15")
    monkeypatch.delenv(current_amp.URL_VAR, raising=False)
    out = json.loads(tools_core.t_zero_amp_offsets({"justification": "why"})[0])
    assert not out["ok"] and current_amp.URL_VAR in out["error"]
    assert action_rows == []


def test_handler_writes_through_audited_device_call():
    """test_mutates.py's AST scan only sees audited_call; this is its check here."""
    tree = ast.parse(open(tools_core.__file__).read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "t_zero_amp_offsets")
    called = {c.func.id for c in ast.walk(fn)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "audited_device_call" in called and "audited_call" not in called
    assert TOOL_LINEAGE["zero_amp_offsets"]["mutates"] is True
