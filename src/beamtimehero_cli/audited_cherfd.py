"""Audited dispatch to the CHERFD services -- ``audited_call`` for the cherfd transport.

Mirrors ``audited_call.py`` and writes to the **same** action
and query logs, so a beamtime's audit trail is one table regardless of
whether a hardware action went through SPEC or through the cherfd server:

* reads  -> ``query_log`` row with latency and result.
* actions/stops -> justification required; an ``action_log`` row is opened
  *before* the request leaves (a durable trace even if the service hangs),
  then finalized with the reply. Command names are logged as
  ``cherfd:<name>`` so they cannot be confused with SPEC commands.

The write safety switch is beamtimehero's file (``safety_switches_path()``),
not a second one: an operator who turns off ``spec_write_enabled`` means
"nothing moves", and a different transport must not be a way around that.
``cherfd_write_enabled`` narrows it to cherfd only. ``stop``-class commands
bypass both, as beamtimehero's ``abort`` does.

Service-level success is reported separately from transport success: the
cherfd routes put ``success: false`` in a 200 body for some refusals
(``/stop`` with nothing running), and cscan_daq uses HTTP 409/422.
"""
from __future__ import annotations

import json
import logging
import time

from beamtimehero_cli import runtime_state
from beamtimehero_cli.action_log.db import finish_action, log_query, mark_action_started, start_action
from beamtimehero_cli.spec_control.spec_cmd import safety_switches_path

from beamtimehero_cli.cherfd_control import commands, transport

logger = logging.getLogger(__name__)

WRITE_SWITCHES = ("spec_write_enabled", "cherfd_write_enabled")
READ_SWITCHES = ("cherfd_read_enabled",)


def _switch_refusal(keys: tuple[str, ...], *, fail_closed: bool) -> str | None:
    path = safety_switches_path()
    try:
        with open(path) as fh:
            switches = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        if fail_closed:
            return (f"SAFETY SWITCH: {path} exists but could not be read ({e}); "
                    "refusing cherfd write commands until it parses")
        return None
    for k in keys:
        if switches.get(k, True) is False:
            return f"SAFETY SWITCH: {k} is false in {path}; cherfd command refused"
    return None


def service_ok(reply: transport.Reply) -> bool:
    if not reply.ok or reply.status_code is None or reply.status_code >= 400:
        return False
    body = reply.body or {}
    return body.get("success", True) is not False


def _envelope(cmd, reply: transport.Reply, **extra) -> dict:
    out = {
        "ok": service_ok(reply),
        "kind": cmd.kind,
        "command": cmd.name,
        "transport": reply.transport,
        "http_status": reply.status_code,
        "result": reply.body,
        "elapsed_s": round(reply.elapsed_s, 3),
    }
    if reply.error:
        out["error"] = reply.error
    elif not out["ok"]:
        body = reply.body or {}
        out["error"] = body.get("error") or body.get("message") or body.get("detail") or \
            f"HTTP {reply.status_code}"
    out.update(extra)
    return out


def audited_cherfd(name: str, args: dict | None = None, justification: str = "",
                    *, agent: str = "llm", experiment_id: str | None = None) -> dict:
    try:
        cmd = commands.get(name)
    except KeyError as e:
        return {"ok": False, "kind": "unknown", "command": name, "error": str(e)}
    try:
        body = cmd.build(dict(args or {}))
    except (KeyError, TypeError, ValueError) as e:
        return {"ok": False, "kind": cmd.kind, "command": name,
                "error": f"bad arguments for {name}: {e}"}

    phase = runtime_state.get_phase()
    exp_id = experiment_id or runtime_state.get_experiment_id()
    wire = transport.describe(cmd, body)

    if cmd.kind == "read":
        refusal = _switch_refusal(READ_SWITCHES, fail_closed=False)
        if refusal:
            return {"ok": False, "kind": "read", "command": name, "error": refusal}
        t0 = time.time()
        reply = transport.send(cmd, body)
        try:
            log_query(f"cherfd:{name}", [wire], reply.body if service_ok(reply) else None,
                      phase=phase, experiment_id=exp_id,
                      error_message=None if service_ok(reply) else (reply.error or str(reply.body)),
                      latency_ms=int((time.time() - t0) * 1000))
        except Exception as e:  # noqa: BLE001 -- the query log must never block a read
            logger.warning("query log write failed: %s", e)
        return _envelope(cmd, reply)

    if not (justification or "").strip():
        return {"ok": False, "kind": cmd.kind, "command": name,
                "error": "justification is required for cherfd action commands"}
    if cmd.kind == "action":
        refusal = _switch_refusal(WRITE_SWITCHES, fail_closed=True)
        if refusal:
            return {"ok": False, "kind": "action", "command": name, "error": refusal}

    row = start_action(command=f"cherfd:{name}", args=[json.dumps(body, default=str)],
                       justification=justification, phase=phase, spec_string=wire,
                       experiment_id=exp_id, agent=agent)
    mark_action_started(row.id)
    reply = transport.send(cmd, body)
    ok = service_ok(reply)
    env = _envelope(cmd, reply, action_id=row.id)
    finish_action(row.id, success=ok, result=reply.body if ok else None,
                  screen_output=json.dumps(reply.body, default=str)[:4000] if reply.body else None,
                  error_message=None if ok else env.get("error"))
    return env
