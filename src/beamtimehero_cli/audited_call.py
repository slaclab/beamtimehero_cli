"""Audited dispatch wrapper around `spec_cmd.call`.

`spec_cmd.call` is the primitive: render → reserve transport → dispatch →
parse → return. It knows nothing about phases, experiments, or the
action_log. This wrapper adds the higher-level concerns:

  * Pull phase + experiment_id from `beamtimehero_cli.runtime_state`.
  * Enforce that action commands have a justification.
  * Open an `action_log` row before SPEC sees the command (durable trace
    even when SPEC hangs / process is killed) and finalize it after.
  * For read commands, write a row to `query_log` with timing + result.
  * Best-effort scan-number extraction from the SPEC output for the
    scan-emitting commands so the host can correlate.

Returns the same dict shape callers expect today:
  {"ok": bool, "kind": "read"|"action"|"unknown",
   "action_id"?: str, "result"?: any, "elapsed_s"?: float, "error"?: str}
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable, Optional

from beamtimehero_cli import runtime_state
from beamtimehero_cli.action_log.db import (
    finish_action,
    log_query,
    mark_action_started,
    start_action,
)
from beamtimehero_cli.spec_control import spec_cmd

logger = logging.getLogger(__name__)


_SCAN_EMITTING_COMMANDS = {
    "ascan", "dscan", "d2scan", "gscan", "kscan",
    "run_xas", "emiss_scan", "run_shortcut",
}


#: Set by a host that runs agents per phase run (BL4-1's phase runner) so
#: each action-log row names the run it belongs to. Unset -> NULL, as before.
PHASE_RUN_ENV = "BEAMTIMEHERO_PHASE_RUN_ID"


def _phase_run_id() -> Optional[str]:
    return os.environ.get(PHASE_RUN_ENV) or None


#: Set by the same host to the running phase's slug: the label written on
#: action and query rows. Unset -> runtime_state's workflow phase, as before
#: (which stays at the plan's phase, e.g. "setup", while a phase agent runs).
PHASE_ENV = "BEAMTIMEHERO_PHASE"


def phase_label() -> str:
    return os.environ.get(PHASE_ENV) or runtime_state.get_phase()


def _extract_scan_number(output: str | None) -> Optional[int]:
    if not output:
        return None
    m = re.search(r"(?:scan[_ ]?n|scan)\s*=?\s*#?(\d+)", output, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def audited_call(
    command: str,
    args: list[str] | tuple[str, ...] | None,
    justification: str = "",
    *,
    agent: str = "llm",
    experiment_id: str | None = None,
) -> dict:
    """Phase/experiment-aware audited SPEC dispatch."""
    args_list = list(args or [])
    phase = phase_label()
    exp_id = experiment_id or runtime_state.get_experiment_id()

    kind = spec_cmd.command_kind(command)
    if kind is None:
        return {"ok": False, "kind": "unknown", "error": f"unknown command: {command}"}

    # -------- READ path ---------------------------------------------------
    if kind == "read":
        t0 = time.time()
        result = spec_cmd.call(command, args_list, action_id="query")
        latency_ms = int((time.time() - t0) * 1000)
        if not result.get("ok"):
            log_query(
                command, args_list, None,
                phase=phase, experiment_id=exp_id,
                error_message=result.get("error"),
                latency_ms=latency_ms,
            )
            return result
        log_query(
            command, args_list, result.get("result"),
            phase=phase, experiment_id=exp_id,
            latency_ms=latency_ms,
        )
        return result

    # -------- ACTION path -------------------------------------------------
    if not (justification or "").strip():
        return {
            "ok": False, "kind": "action",
            "error": "justification is required for action commands",
        }

    try:
        spec_string = spec_cmd.render(command, args_list)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False, "kind": "action",
            "error": f"failed to render command: {e}",
        }

    row = start_action(
        command=command,
        args=args_list,
        justification=justification,
        phase=phase,
        spec_string=spec_string,
        experiment_id=exp_id,
        phase_run_id=_phase_run_id(),
        agent=agent,
    )
    mark_action_started(row.id)

    result = spec_cmd.call(
        command, args_list,
        justification=justification, action_id=row.id,
    )

    if not result.get("ok"):
        finish_action(
            row.id,
            success=False,
            error_message=result.get("error"),
            screen_output=result.get("output") or "",
        )
        return {**result, "action_id": row.id}

    parsed = result.get("result") or {}
    scan_number = None
    if command in _SCAN_EMITTING_COMMANDS:
        scan_number = _extract_scan_number(result.get("output"))

    finish_action(
        row.id,
        success=True,
        result=parsed,
        screen_output=result.get("output"),
        scan_number=scan_number,
    )
    out: dict[str, Any] = {
        "ok": True, "kind": "action", "action_id": row.id,
        "result": parsed,
    }
    if "elapsed_s" in result:
        out["elapsed_s"] = result["elapsed_s"]
    return out


def audited_device_call(
    command: str,
    args: list[str] | tuple[str, ...] | None,
    justification: str,
    run: Callable[[], dict],
    *,
    wire: str,
    agent: str = "llm",
    experiment_id: str | None = None,
) -> dict:
    """Audited write to beamline hardware that is not reached through SPEC.

    For a station's own device paths — an EPICS IOC it writes directly, a
    detector owned by another project's agent — that must still answer to
    the same operator controls as a SPEC action:

      * the justification is required;
      * the SPEC-write safety switch is checked (STOP ALL turns it off), with
        the same fail-closed rules and refusal text as ``spec_cmd.call``;
      * an ``action_log`` row is written *before* ``run()`` is called, with
        ``wire`` as the human-readable record of what was sent, and is
        finalized with the outcome.

    ``run`` does the I/O and returns a dict; ``{"ok": False, "error": ...}``
    marks a failure, and an exception is caught and recorded as one.
    Returns the same envelope shape as :func:`audited_call`.
    """
    args_list = [str(a) for a in (args or [])]
    if not (justification or "").strip():
        return {
            "ok": False, "kind": "action",
            "error": "justification is required for action commands",
        }
    safety_err = spec_cmd.safety_check("action")
    if safety_err:
        return {"ok": False, "kind": "action", "error": safety_err}

    phase = phase_label()
    exp_id = experiment_id or runtime_state.get_experiment_id()
    row = start_action(
        command=command,
        args=args_list,
        justification=justification,
        phase=phase,
        spec_string=wire,
        experiment_id=exp_id,
        phase_run_id=_phase_run_id(),
        agent=agent,
    )
    mark_action_started(row.id)
    t0 = time.time()
    try:
        result = run() or {}
    except Exception as e:  # noqa: BLE001 — recorded, never raised to the agent
        logger.exception("device call %s failed", command)
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    elapsed = time.time() - t0
    ok = bool(result.get("ok", True))
    if not ok:
        finish_action(row.id, success=False,
                      error_message=str(result.get("error") or "device call failed"),
                      result=result)
        return {"ok": False, "kind": "action", "action_id": row.id,
                "error": result.get("error") or "device call failed",
                "result": result, "elapsed_s": elapsed}
    finish_action(row.id, success=True, result=result)
    return {"ok": True, "kind": "action", "action_id": row.id,
            "result": result, "elapsed_s": elapsed}
