"""Route a registered command to the mock or to the live HTTP service.

Selection happens here and nowhere else. ``requests`` is imported only on
the live path, so a mock-mode process never even loads the HTTP client --
``tests/test_control.py`` asserts no socket is opened in mock mode.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from beamtimehero_cli.cherfd_control import settings as config
from beamtimehero_cli.cherfd_control import mock_server
from beamtimehero_cli.cherfd_control.commands import DAQ, RestCommand


@dataclass
class Reply:
    ok: bool                # transport reached the service and got JSON back
    status_code: int | None
    body: dict | None
    transport: str          # "mock" | "http"
    url: str
    elapsed_s: float
    error: str | None = None


def base_url(cmd: RestCommand) -> str:
    return config.daq_url() if cmd.service == DAQ else config.server_url()


def describe(cmd: RestCommand, body: dict | None) -> str:
    """Human-readable wire form, stored as the action log's command string."""
    target = "mock://" + cmd.service if config.mock_enabled() else base_url(cmd)
    payload = f" {body}" if body else ""
    return f"{cmd.method} {target}{cmd.path}{payload}"


def send(cmd: RestCommand, body: dict | None) -> Reply:
    t0 = time.time()
    if config.mock_enabled():
        code, js = mock_server.handle(cmd.name, body)
        return Reply(True, code, js, "mock", f"mock://{cmd.service}{cmd.path}", time.time() - t0)

    import requests  # live path only

    url = base_url(cmd) + cmd.path
    timeout = cmd.timeout_s or config.http_timeout_s()
    try:
        if cmd.method == "GET":
            r = requests.get(url, timeout=timeout)
        else:
            r = requests.post(url, json=body or {}, timeout=timeout)
    except requests.RequestException as e:
        return Reply(False, None, None, "http", url, time.time() - t0,
                     error=f"transport error: {type(e).__name__}: {e}")
    try:
        js = r.json()
    except ValueError:
        return Reply(False, r.status_code, None, "http", url, time.time() - t0,
                     error=f"non-JSON reply (HTTP {r.status_code}): {r.text[:200]}")
    return Reply(True, r.status_code, js if isinstance(js, dict) else {"data": js},
                 "http", url, time.time() - t0)
