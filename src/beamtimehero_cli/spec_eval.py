"""Sandbox evaluation of SPEC macros via the spec-eval Docker API.

Wraps the spec-eval HTTP service to let the LLM validate SPEC macro code
in a disposable, network-isolated container before recommending it.
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict
from urllib.parse import urlsplit

import requests

from beamtimehero_cli.config import SPEC_EVAL_URL

logger = logging.getLogger(__name__)

DEFAULT_API_URL = SPEC_EVAL_URL
_HTTP_TIMEOUT = 600  # must comfortably exceed SPEC's own timeout

# The spec-eval service takes arbitrary SPEC macro source and executes it.
# That is only defensible because the service is on this machine, behind no
# network, started by whoever runs the CLI. A remote host with that endpoint
# open is a remote code execution service, so the URL is pinned to loopback
# here rather than trusted from SPEC_EVAL_URL: a typo, a copied production
# config, or an injected environment variable must not be able to redirect
# macro source off-box.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Bypass the HTTP proxy for all spec-eval traffic (always localhost).
_session = requests.Session()
_session.trust_env = False


class SpecEvalResult(TypedDict):
    ok: bool
    exit_code: int | None
    timed_out: bool
    output: str          # clean command output (between SPEC_EVAL markers)
    log: str             # full session log including startup/teardown noise
    duration_s: float | None
    run_id: str | None
    error: str | None
    reply: str | None           # SV_REPLY payload (TCP mode only)
    output_complete: bool | None


def _error_result(message: str) -> SpecEvalResult:
    return SpecEvalResult(
        ok=False,
        exit_code=None,
        timed_out=False,
        output="",
        log="",
        duration_s=None,
        run_id=None,
        error=message,
        reply=None,
        output_complete=None,
    )


def evaluate_spec_macro(
    macro: str,
    preload: list[str] | None = None,
    timeout_s: int = 30,
    api_url: str = DEFAULT_API_URL,
    mode: str = "screen",
) -> SpecEvalResult:
    """Run a SPEC macro in a disposable sandbox container and return the log.

    ``api_url`` must be a loopback address. Never raises — failures,
    including a rejected URL, are reported via the ``error`` field so the
    agent can handle outcomes inline.
    """
    host = urlsplit(api_url).hostname
    if host not in LOOPBACK_HOSTS:
        # Return before building the request: nothing is sent to a
        # non-loopback host, not even a connection attempt.
        return _error_result(
            f"spec-eval URL must be loopback: {api_url!r} has host {host!r}, "
            f"expected one of {sorted(LOOPBACK_HOSTS)}. This tool posts SPEC "
            f"macro source for execution, so the service is only ever "
            f"addressed on this machine. Fix SPEC_EVAL_URL."
        )

    payload: dict[str, Any] = {
        "macro": macro,
        "preload": preload or [],
        "timeout_s": timeout_s,
    }
    endpoint = "/evaluate_tcp" if mode == "tcp" else "/evaluate"
    url = api_url.rstrip("/") + endpoint

    try:
        resp = _session.post(url, json=payload, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("spec-eval transport error: %s", e)
        # The "transport error: " prefix is a sentinel — spec_cmd.dispatch()
        # matches the substring "transport error" to decide that a mock-mode
        # call should fall back to the in-memory simulator. Keep it leading;
        # the prose after it is for a human reading the tool's output.
        return _error_result(
            f"transport error: {e}. This is the one tool in the package that "
            f"cannot answer from the mock: it needs a local spec-eval service "
            f"at {api_url}, which is a Docker container running a licensed "
            f"SPEC install on an Ubuntu host. If you have not set that up, "
            f"this tool is unavailable and every other tool still works — see "
            f"`beamtimehero ref agent-integration`."
        )

    if resp.status_code >= 500:
        return _error_result(f"server error {resp.status_code}: {resp.text[:500]}")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        return _error_result(f"bad request ({resp.status_code}): {detail}")

    data = resp.json()
    timed_out = bool(data.get("timed_out"))
    exit_code = data.get("exit_code")
    return SpecEvalResult(
        ok=(exit_code == 0 and not timed_out),
        exit_code=exit_code,
        timed_out=timed_out,
        output=data.get("output", ""),
        log=data.get("log", ""),
        duration_s=data.get("duration_s"),
        run_id=data.get("run_id"),
        error=None,
        reply=data.get("reply"),
        output_complete=data.get("output_complete"),
    )
