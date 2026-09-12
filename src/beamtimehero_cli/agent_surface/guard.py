"""The motor allow-list, enforced where it cannot be walked around.

This check used to live in the consumer's CLI leaf runner: the parser
stamped ``_agent_role`` on the leaves it remembered to stamp, and the
runner looked the role up in a dict before calling ``execute_tool``. Two
holes followed from that. The stamping walked argparse privates for four
of nine branches, so five branches' leaves carried no role and were never
checked. And anything that called ``execute_tool`` directly — a harness,
an orchestrator, a test — bypassed the runner and therefore the guard.

Here the guard is a ``BeforeHook`` on the surface's own executor, so it
runs for every call through that executor regardless of who made it, and
the surface's parser cannot be built without it.
"""
from __future__ import annotations

import json
from typing import Iterable

#: The argument names that name a motor. Verified against
#: ``definitions.py``: these are the only ``motor*`` properties in the
#: whole catalogue (``read_motor_position``/``move_motor``/
#: ``move_motor_relative``/``run_motor_scan``/``run_motor_scan_relative``
#: take ``motor``; ``run_diagonal_scan`` takes ``motor1``/``motor2``).
#: ``tests/test_agent_surface.py`` asserts no ``^motor`` property escapes
#: this tuple, because a new one that did would be silently unguarded.
MOTOR_ARG_KEYS: tuple[str, ...] = ("motor", "motor1", "motor2")


def motor_arg_keys_of(definition: dict) -> tuple[str, ...]:
    """Which of :data:`MOTOR_ARG_KEYS` this tool actually accepts."""
    params = (definition.get("function") or {}).get("parameters") or {}
    properties = params.get("properties") or {}
    return tuple(key for key in MOTOR_ARG_KEYS if key in properties)


def motor_guard(motors: Iterable[str], surface_name: str):
    """A ``BeforeHook`` refusing any motor outside ``motors``.

    The refusal envelope is the one consumers already print — same keys,
    same wording — so a prompt or a log parser that recognised it still
    does. What changed is where it comes from: returning the string from
    a before-hook makes it the tool's result text, which is how an
    executor-level refusal reaches a caller that has no parser.
    """
    allowed = frozenset(motors)
    listing = sorted(allowed)

    def _guard(tree: tuple[str, ...], name: str, args: dict):
        for key in MOTOR_ARG_KEYS:
            value = args.get(key)
            if value and value not in allowed:
                return json.dumps({
                    "ok": False,
                    "error": f"motor {value!r} not in {surface_name} allowed set",
                    "agent_role": surface_name,
                    "allowed_motors": listing,
                })
        return None

    return _guard


__all__ = ["MOTOR_ARG_KEYS", "motor_arg_keys_of", "motor_guard"]
