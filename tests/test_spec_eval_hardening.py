"""The spec-eval path: loopback pin, endpoint choice, and the error message.

Three properties, each of which failed quietly before there was a test for it.

1. `evaluate_spec_macro` posts SPEC macro source to a service that executes
   it. If `SPEC_EVAL_URL` names a remote host, that is a remote code execution
   service, so the URL is pinned to loopback and refused *before* a request is
   built. The test asserts no request is attempted, not merely that one fails.

2. The mock path must not inherit `SPEC_TRANSPORT`. `/evaluate` runs each
   macro in a `--network none` container; `/evaluate_tcp` talks to a
   long-lived spec server over a network. A deployment setting
   `SPEC_TRANSPORT=tcp` for its eventual live path was thereby routing
   *simulated* commands onto the networked endpoint.

3. The transport-error string is load-bearing in two directions at once:
   `spec_cmd.dispatch()` matches the substring `"transport error"` to decide
   that a mock-mode call should fall back to the in-memory simulator, and a
   human reading it on a fresh clone needs to be told that the missing piece
   is an out-of-tree service and that nothing else is broken. Both are
   asserted here so neither can be edited away for the other's sake.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from beamtimehero_cli import spec_eval
from beamtimehero_cli.spec_control import sandbox_client, spec_cmd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_response(payload: dict | None = None) -> Mock:
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"exit_code": 0, "output": "done", "log": "",
                              **(payload or {})}
    return resp


@pytest.fixture
def no_post(monkeypatch):
    """Patch the session so any HTTP attempt is recorded and never made."""
    post = Mock(side_effect=AssertionError("a request was built and sent"))
    monkeypatch.setattr(spec_eval._session, "post", post)
    return post


@pytest.fixture
def capture_post(monkeypatch):
    """Patch the session to record the call and return a healthy response."""
    post = Mock(return_value=_ok_response())
    monkeypatch.setattr(spec_eval._session, "post", post)
    return post


# ---------------------------------------------------------------------------
# 4.3(a) — loopback pin
# ---------------------------------------------------------------------------

NON_LOOPBACK = [
    "http://spec.example.com:5006",
    "http://10.0.0.7:5006",
    "https://198.51.100.9",
    "http://[2001:db8::1]:5006",
    # The near-misses are the ones that would slip a substring check.
    "http://127.0.0.1.example.com:5006",
    "http://localhost.attacker.test:5006",
    "http://notlocalhost:5006",
]


@pytest.mark.parametrize("url", NON_LOOPBACK)
def test_non_loopback_url_makes_no_request(url, no_post):
    result = spec_eval.evaluate_spec_macro("print 1", api_url=url)
    no_post.assert_not_called()
    assert result["ok"] is False
    assert "must be loopback" in result["error"]


def test_loopback_rejection_names_the_url_and_the_variable(no_post):
    result = spec_eval.evaluate_spec_macro("print 1",
                                           api_url="http://spec.example.com")
    no_post.assert_not_called()
    # The message has to be actionable without reading the source: which URL
    # was refused, what was acceptable, and where to change it.
    assert "spec.example.com" in result["error"]
    assert "127.0.0.1" in result["error"]
    assert "SPEC_EVAL_URL" in result["error"]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5006",
    "http://localhost:5006",
    "http://[::1]:5006",
    "http://127.0.0.1:5006/",
])
def test_loopback_urls_are_allowed_through(url, capture_post):
    result = spec_eval.evaluate_spec_macro("print 1", api_url=url)
    assert capture_post.called, url
    assert result["ok"] is True


def test_the_default_url_is_itself_loopback(capture_post):
    """A pin that the shipped default violates would be a broken tool."""
    result = spec_eval.evaluate_spec_macro("print 1")
    assert capture_post.called
    assert result["error"] is None


def test_loopback_rejection_is_reported_not_raised(no_post):
    """Every failure of this tool is an `error` field, never an exception —
    the handler JSON-dumps the result straight back to the agent."""
    result = spec_eval.evaluate_spec_macro("print 1", api_url="http://evil.test")
    assert set(result) == set(spec_eval.SpecEvalResult.__annotations__)
    assert result["exit_code"] is None
    assert result["timed_out"] is False


# ---------------------------------------------------------------------------
# 4.3(b) — mock deployments stay off the networked endpoint
# ---------------------------------------------------------------------------

def test_endpoint_follows_mode(capture_post):
    spec_eval.evaluate_spec_macro("print 1", mode="screen")
    assert capture_post.call_args[0][0].endswith("/evaluate")

    capture_post.reset_mock()
    spec_eval.evaluate_spec_macro("print 1", mode="tcp")
    assert capture_post.call_args[0][0].endswith("/evaluate_tcp")


def test_t_evaluate_spec_macro_posts_to_the_isolated_endpoint(capture_post):
    """The tool handler never asks for the networked endpoint."""
    from beamtimehero_cli.tool_catalog.tools_core import t_evaluate_spec_macro

    text, images = t_evaluate_spec_macro({"macro": "print 1"})
    assert capture_post.called
    assert capture_post.call_args[0][0].endswith("/evaluate")
    assert images == []
    assert '"ok": true' in text


def test_sandbox_dispatch_mode_none_keeps_the_transport_rule(monkeypatch):
    seen = {}

    def fake_eval(**kwargs):
        seen.update(kwargs)
        return spec_eval._error_result("stop here")

    monkeypatch.setattr(spec_eval, "evaluate_spec_macro", fake_eval)

    monkeypatch.setattr(sandbox_client, "SPEC_TRANSPORT", "tcp")
    sandbox_client.dispatch("print 1")
    assert seen["mode"] == "tcp"

    monkeypatch.setattr(sandbox_client, "SPEC_TRANSPORT", "screen")
    sandbox_client.dispatch("print 1")
    assert seen["mode"] == "screen"

    monkeypatch.setattr(sandbox_client, "SPEC_TRANSPORT", "sandbox")
    sandbox_client.dispatch("print 1")
    assert seen["mode"] == "screen"


def test_sandbox_dispatch_honours_an_explicit_mode(monkeypatch):
    seen = {}

    def fake_eval(**kwargs):
        seen.update(kwargs)
        return spec_eval._error_result("stop here")

    monkeypatch.setattr(spec_eval, "evaluate_spec_macro", fake_eval)
    monkeypatch.setattr(sandbox_client, "SPEC_TRANSPORT", "tcp")

    sandbox_client.dispatch("print 1", mode="screen")
    assert seen["mode"] == "screen"


def test_mock_path_pins_screen_mode_even_under_transport_tcp(monkeypatch):
    """The regression this exists for: SPEC_MOCK=1 with SPEC_TRANSPORT=tcp
    used to send simulated commands to /evaluate_tcp."""
    seen = {}

    def fake_eval(**kwargs):
        seen.update(kwargs)
        return spec_eval._error_result("stop here")

    monkeypatch.setattr(spec_eval, "evaluate_spec_macro", fake_eval)
    monkeypatch.setattr(sandbox_client, "SPEC_TRANSPORT", "tcp")
    monkeypatch.setattr(spec_cmd, "SPEC_TRANSPORT", "tcp")
    monkeypatch.setattr(spec_cmd, "SPEC_MOCK", True)
    monkeypatch.setattr(sandbox_client, "is_healthy", lambda **kw: True)

    spec_cmd.dispatch("wm mono")
    assert seen["mode"] == "screen"


# ---------------------------------------------------------------------------
# 4.3(c) — what a fresh clone is told
# ---------------------------------------------------------------------------

def test_transport_error_keeps_the_sentinel_and_explains_itself(monkeypatch):
    monkeypatch.setattr(
        spec_eval._session, "post",
        Mock(side_effect=requests.ConnectionError("connection refused")),
    )
    result = spec_eval.evaluate_spec_macro("print 1")
    msg = result["error"]
    # The sentinel spec_cmd.dispatch() matches on, still leading.
    assert msg.startswith("transport error")
    # The prose a human needs.
    assert "spec-eval service" in msg
    assert "127.0.0.1:5006" in msg
    assert "ref agent-integration" in msg


def test_transport_error_still_triggers_the_mock_fallback(monkeypatch):
    """The prose must not cost us the fallback: with the sandbox unreachable,
    a mock-mode dispatch has to land on _MockScreen rather than surface the
    error."""
    monkeypatch.setattr(
        spec_eval._session, "post",
        Mock(side_effect=requests.ConnectionError("connection refused")),
    )
    monkeypatch.setattr(spec_cmd, "SPEC_MOCK", True)
    monkeypatch.setattr(sandbox_client, "is_healthy", lambda **kw: True)

    result = spec_cmd.dispatch("wm mono")
    assert result.transport == "mock"
    assert result.ok is True


def test_the_tool_description_states_the_prerequisite():
    """An agent reads the description before its first call; the schema is
    the only place it learns the service is not bundled."""
    from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS

    desc = next(
        d["function"]["description"]
        for d in TOOL_DEFINITIONS
        if d["function"]["name"] == "evaluate_spec_macro"
    )
    first = desc.split(". ")[0]
    assert "spec-eval" in first, first
    for fragment in ("licensed SPEC", "mock", "transport error"):
        assert fragment in desc, fragment


def test_the_refdoc_has_the_subsection_the_error_points_at():
    """The transport-error message and two troubleshooting rows send the
    reader to `ref agent-integration`; the section has to be there."""
    from beamtimehero_cli import refdocs

    doc = refdocs.get_doc("agent-integration")
    assert "### The one tool the mock does not cover" in doc
    for fragment in ("SPEC_EVAL_URL", "licensed SPEC", "loopback", "/evaluate_tcp"):
        assert fragment in doc, fragment
