"""Run cherfd_claude's production motion simulator, out of process.

``cscan_box/simulate.py simulate_command`` is the authoritative answer to
"will this command run, and how": it drives the same profile generator,
segment budgeting and trigger timing a real scan uses, with caget/caput
patched to raise. It is not re-implemented here, because a second copy of
the FPGA profile math would drift from the one that actually runs.

It runs in a **subprocess** with the checkout's own interpreter because
importing it in-process would pull pyepics, the cherfd config side effects
(log/data dir creation) and module-global crystal state into this process.
The child is given a sentinel line so the stray prints the cherfd modules
emit on import (``setting crystal``) cannot corrupt the JSON.
"""
from __future__ import annotations

import json
import subprocess

from beamtimehero_cli.cherfd_control import settings as config

_SENTINEL = "@@CHERFD_SIM_JSON@@"

_RUNNER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import logging
import matplotlib
matplotlib.use("Agg")
# cscan_box/triggers.py creates a log file in the checkout on import; a
# simulation must leave no trace there, so hand it a plain logger instead.
import utils.logging_utility as _lu
_lu.setup_logger = lambda name, *a, **k: logging.getLogger(name)
from cscan_box.simulate import simulate_command
req = json.loads(sys.argv[2])
res = simulate_command(req["command"], ramp_type=req["ramp_type"], clamp=True,
                       per_step=False, table=req["table"], table_anchor=req["table_anchor"])
sys.stdout.write("\n" + SENTINEL + json.dumps(res, default=str) + "\n")
""".replace("SENTINEL", repr(_SENTINEL))


class SimulatorUnavailable(RuntimeError):
    pass


def simulate(command: str, *, ramp_type: str = "s_ramp", table: bool = False,
             table_anchor: dict | None = None, timeout_s: float = 60.0) -> dict:
    """Return the simulator's result dict (``ok`` False on a refused command)."""
    proj = config.project_dir()
    if proj is None or not (proj / "cscan_box" / "simulate.py").exists():
        raise SimulatorUnavailable(
            "production simulator not found: set CHERFD_PROJECT_DIR to a cherfd_claude "
            "checkout (needs cscan_box/simulate.py)"
        )
    req = {"command": command, "ramp_type": ramp_type, "table": bool(table),
           "table_anchor": table_anchor}
    try:
        proc = subprocess.run(
            [config.simulator_python(), "-c", _RUNNER, str(proj), json.dumps(req)],
            capture_output=True, text=True, timeout=timeout_s, cwd=str(proj),
        )
    except subprocess.TimeoutExpired:
        raise SimulatorUnavailable(f"simulator timed out after {timeout_s} s") from None
    except OSError as e:
        raise SimulatorUnavailable(f"cannot run simulator interpreter: {e}") from None
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(_SENTINEL):
            return json.loads(line[len(_SENTINEL):])
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
    raise SimulatorUnavailable("simulator produced no result: " + " | ".join(tail))


def summarize(sim: dict) -> dict:
    """The part of a simulator result an agent needs; drops the bulky series."""
    if not sim.get("ok"):
        return {"ok": False, "error": sim.get("error"), "log_tail": (sim.get("log") or "")[-1500:]}
    out = {
        "ok": True,
        "command": sim.get("command"),
        "ramp_type": sim.get("ramp_type"),
        "summary": sim.get("summary"),
        "regions": sim.get("regions"),
        "checks": sim.get("checks"),
        "warnings": sim.get("warnings"),
        "region_boundaries_s": sim.get("boundaries"),
    }
    if "table" in sim:
        t = sim["table"] or {}
        out["table"] = {k: t.get(k) for k in ("ok", "error", "summary", "checks", "warnings",
                                               "anchor_eV", "anchor_is_default") if k in t}
    return out
