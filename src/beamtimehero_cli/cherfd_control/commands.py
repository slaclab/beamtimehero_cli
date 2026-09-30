"""The allowlist: every request this package may send to cherfd hardware services.

The analogue of beamtimehero_cli's ``spec_cmd`` registry. No handler builds a
URL; it names a registered command, and the body goes through that command's
``build`` function, which is the single place its payload shape is decided.
Anything not registered here cannot be sent.

``kind`` is the safety class:

* ``read``   -- GET; no state change. Logged to the query log.
* ``action`` -- POST to a control service. Requires a justification, is
  written to the action log *before* dispatch, and is refused while the
  write safety switch is off.
* ``stop``   -- an action that halts motion (scan stop, gap stop, count
  abort). Justification + action log as for any action, but it **bypasses
  the write safety switch**, exactly as beamtimehero's ``abort`` does:
  stopping must work when writes are disabled -- especially then.

Every POST is an action, including ones that only change controller
settings (``set_data_path``, ``set_gap_move_latency``): those settings
decide where the next scan's data goes and how the gap tracks, and they are
persisted server-side across restarts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

SERVER = "server"   # server/server.py, :5002
DAQ = "daq"         # cscan_daq/app.py, :5004


@dataclass(frozen=True)
class RestCommand:
    name: str
    service: str
    method: str
    path: str
    kind: str                      # "read" | "action" | "stop"
    build: Callable[[dict], dict] = field(default=lambda a: {})
    timeout_s: float | None = None
    doc: str = ""


def _pick(*keys):
    return lambda a: {k: a[k] for k in keys if a.get(k) is not None}


def _start(a: dict) -> dict:
    body = {"command": a["command"], "sweeps": int(a.get("sweeps", 1)),
            "reverse": bool(a.get("reverse", False)),
            "undulator_scan": bool(a.get("undulator_scan", False))}
    for k in ("file_dir", "file_root"):
        if a.get(k) is not None:
            body[k] = a[k]
    return body


def _anchor(a: dict) -> dict:
    if a.get("capture"):
        return {"capture": True}
    body = {k: float(a[k]) for k in ("energy", "z1", "z2")}
    if a.get("crystal"):
        body["crystal"] = str(a["crystal"]).upper()
    return body


_ALL = [
    # ---- reads ----------------------------------------------------------
    RestCommand("status", SERVER, "GET", "/status", "read", doc="controller state + current scan"),
    RestCommand("spec_status", SERVER, "GET", "/spec_status", "read", doc="'1' when the acquisition is done"),
    RestCommand("results", SERVER, "GET", "/results", "read", timeout_s=30, doc="last completed sweep"),
    RestCommand("server_settings", SERVER, "GET", "/server_settings", "read"),
    RestCommand("undulator_status", SERVER, "GET", "/get_undulator_status", "read"),
    RestCommand("table_tracking", SERVER, "GET", "/get_table_tracking", "read"),
    RestCommand("daq_status", DAQ, "GET", "/api/status", "read"),
    RestCommand("daq_health", DAQ, "GET", "/api/health", "read"),
    # ---- actions --------------------------------------------------------
    RestCommand("start_acquisition", SERVER, "POST", "/start_acquisition", "action", _start,
                doc="start a (multi-)sweep continuous scan; returns immediately"),
    RestCommand("reset", SERVER, "POST", "/reset", "action"),
    RestCommand("clear_stop", SERVER, "POST", "/clear_stop", "action"),
    RestCommand("set_data_path", SERVER, "POST", "/set_data_path", "action",
                _pick("file_dir", "file_root")),
    RestCommand("set_undulator_enable", SERVER, "POST", "/set_undulator_enable", "action",
                lambda a: {"enable": bool(a["enable"])}),
    RestCommand("set_gap_calibration_offset", SERVER, "POST", "/set_gap_calibration_offset",
                "action", lambda a: {"offset": float(a["offset"])}),
    RestCommand("set_gap_move_latency", SERVER, "POST", "/set_gap_move_latency", "action",
                lambda a: {"latency": float(a["latency"])}),
    RestCommand("set_table_tracking", SERVER, "POST", "/set_table_tracking", "action",
                lambda a: {"enable": 1 if a["enable"] else 0}),
    RestCommand("set_table_anchor", SERVER, "POST", "/set_table_anchor", "action", _anchor),
    RestCommand("move_energy", SERVER, "POST", "/move_energy", "action",
                lambda a: {"energy": float(a["energy"])}, doc="mono + gap, asynchronous"),
    RestCommand("move_gap", SERVER, "POST", "/move_gap", "action",
                lambda a: {"gap": float(a["gap"])}),
    RestCommand("calibrate_energy", SERVER, "POST", "/calibrate_energy", "action"),
    RestCommand("daq_count", DAQ, "POST", "/api/count", "action", _pick(
        "duration_s", "freq_hz", "gate_mode", "quick")),
    # ---- stops (bypass the write switch) --------------------------------
    RestCommand("stop", SERVER, "POST", "/stop", "stop"),
    RestCommand("stop_gap", SERVER, "POST", "/stop_gap", "stop"),
    RestCommand("daq_abort", DAQ, "POST", "/api/abort", "stop"),
]

COMMANDS: dict[str, RestCommand] = {c.name: c for c in _ALL}


def get(name: str) -> RestCommand:
    try:
        return COMMANDS[name]
    except KeyError:
        raise KeyError(f"unknown cherfd command: {name}") from None


def known_commands() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"read": [], "action": [], "stop": []}
    for c in _ALL:
        out[c.kind].append(c.name)
    return out
