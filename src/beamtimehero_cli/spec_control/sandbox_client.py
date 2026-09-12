"""Sandbox transport — routes SPEC commands through the spec-eval Docker API.

Same interface as tcp_client / screen_client: exports dispatch() and
abort_current().  Also exports is_healthy() for the fallback logic in
spec_cmd.dispatch().

The spec-eval container runs sim-mode SPEC in a disposable, network-isolated
Docker container.  Each call is stateless (fresh container).
"""

from __future__ import annotations

import logging
import threading
import time

import requests

from beamtimehero_cli.config import SPEC_EVAL_URL, SPEC_TRANSPORT
from beamtimehero_cli.spec_control.transport import DispatchResult

logger = logging.getLogger(__name__)

# Bypass the HTTP proxy for all sandbox traffic (always localhost).
_session = requests.Session()
_session.trust_env = False

# ---------------------------------------------------------------------------
# Cached health probe
# ---------------------------------------------------------------------------

_health_lock = threading.Lock()
_health_cache: dict[str, object] = {"healthy": False, "checked_at": 0.0}

_HEALTH_TIMEOUT = 2.0  # seconds


def is_healthy(*, ttl_s: float = 60.0, api_url: str | None = None) -> bool:
    url = (api_url or SPEC_EVAL_URL).rstrip("/")
    with _health_lock:
        if time.time() - float(_health_cache["checked_at"]) < ttl_s:
            return bool(_health_cache["healthy"])
    try:
        resp = _session.get(f"{url}/healthz", timeout=_HEALTH_TIMEOUT)
        ok = resp.status_code == 200
    except Exception:
        ok = False
    with _health_lock:
        _health_cache["healthy"] = ok
        _health_cache["checked_at"] = time.time()
    if not ok:
        logger.debug("sandbox health check failed (%s)", url)
    return ok


def clear_health_cache() -> None:
    with _health_lock:
        _health_cache["healthy"] = False
        _health_cache["checked_at"] = 0.0


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(spec_string: str, *, timeout_s: float = 1800.0,
             api_url: str | None = None,
             mode: str | None = None) -> DispatchResult:
    """Run one SPEC command through the spec-eval API.

    ``mode`` selects the service endpoint: ``"screen"`` is ``/evaluate``,
    which runs the macro in a fresh ``--network none`` container, and
    ``"tcp"`` is ``/evaluate_tcp``, which talks to a long-lived spec server
    over the network. ``None`` keeps the historical rule of deriving it from
    ``SPEC_TRANSPORT``. Callers that are simulating rather than driving
    hardware should pass ``"screen"`` explicitly: the endpoint is a property
    of what the caller is doing, not of how this host happens to be
    configured to reach a real SPEC.
    """
    from beamtimehero_cli.spec_eval import evaluate_spec_macro

    url = api_url or SPEC_EVAL_URL
    if mode is None:
        mode = "tcp" if SPEC_TRANSPORT == "tcp" else "screen"
    t0 = time.time()
    result = evaluate_spec_macro(
        macro=spec_string,
        timeout_s=int(min(timeout_s, 300)),
        api_url=url,
        mode=mode,
    )
    elapsed = time.time() - t0

    _enriched = dict(
        exit_code=result.get("exit_code"),
        timed_out=result.get("timed_out"),
        output_complete=result.get("output_complete"),
        run_id=result.get("run_id"),
        log=result.get("log"),
        reply=result.get("reply"),
        transport="sandbox",
    )

    if result["error"]:
        return DispatchResult(
            ok=False,
            output=result["output"],
            prompt_seen=True,
            elapsed_s=result.get("duration_s") or elapsed,
            error=f"sandbox: {result['error']}",
            **_enriched,
        )

    if result["timed_out"]:
        return DispatchResult(
            ok=False,
            output=result["output"],
            prompt_seen=True,
            elapsed_s=result.get("duration_s") or elapsed,
            error="sandbox: macro timed out",
            **_enriched,
        )

    if result["exit_code"] and result["exit_code"] != 0:
        return DispatchResult(
            ok=False,
            output=result["output"],
            prompt_seen=True,
            elapsed_s=result.get("duration_s") or elapsed,
            error=f"sandbox: SPEC exited with code {result['exit_code']}",
            **_enriched,
        )

    return DispatchResult(
        ok=True,
        output=result["output"],
        prompt_seen=True,
        elapsed_s=result.get("duration_s") or elapsed,
        **_enriched,
    )


# ---------------------------------------------------------------------------
# Abort (no-op — sandbox commands are synchronous one-shot containers)
# ---------------------------------------------------------------------------

def abort_current() -> bool:
    logger.info("[sandbox] abort (no-op — sandbox commands are stateless)")
    return True
