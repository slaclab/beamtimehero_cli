"""A stand-in for the cherfd control server and the cscan_daq app.

Used whenever ``CHERFD_MOCK`` is not exactly ``"0"``. It answers every
registered command with the same JSON shapes the real routes return
(``server/server.py``, ``cscan_daq/app.py``), including their validation
errors, so tool handlers are exercised against realistic responses.

State persists in a small JSON file (``CHERFD_MOCK_STATE``) so a sequence
of separate CLI invocations -- start, poll status, stop -- behaves like one
session. Scan progress is computed from the wall clock (scaled by
``CHERFD_MOCK_TIME_SCALE``) at every read, so ``wait`` tools actually wait.

Two behaviours of the real server are reproduced on purpose because tools
must cope with them:

* ``/start_acquisition`` returns ``success: true`` as soon as its worker
  thread starts, **even when the controller then refuses the scan** (not
  idle, stop latched). Only ``/status`` tells you whether it is running.
* After ``/stop`` the controller sits in ``Stopped`` and refuses new scans
  until ``/clear_stop`` or ``/reset``.

It never writes data files: a mock scan produces status and a small
synthetic ``/results`` payload, not pickles on disk.
"""
from __future__ import annotations

import fcntl
import json
import math
import time
from contextlib import contextmanager

from beamtimehero_cli.cherfd_control import settings as config
from beamtimehero_cli.science.cherfd import beamline, command as cmd_sci, policy


def _default_state() -> dict:
    return {
        "settings": {
            "gap_calibration_offset": 0.03, "gap_move_latency": 0.2,
            "data_directory": "testing", "file_root": "testing",
            "mono_xtal": None, "mono_enc": None,
            "table_tracking_enabled": False, "table_anchor_energy": None,
            "table_anchor_z1": None, "table_anchor_z2": None, "table_anchor_crystal": None,
        },
        "undulator": {"enabled": True, "harmonic": None, "gap": 8.20},
        "mono_energy": 9000.0,
        "table_z": [0.0, 0.0],
        "controller": {"state": "idle", "error": None},
        "scan": None,
        "last_results": None,
        "daq": {"state": "idle", "started_at": None, "duration_s": 0.0, "params": None,
                "last_result": None},
    }


@contextmanager
def _state():
    path = config.mock_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        raw = fh.read()
        try:
            st = json.loads(raw) if raw.strip() else _default_state()
        except json.JSONDecodeError:
            st = _default_state()
        yield st
        fh.seek(0)
        fh.truncate()
        json.dump(st, fh, indent=1)
        fcntl.flock(fh, fcntl.LOCK_UN)


def reset_mock_state() -> None:
    """Discard all mock state (tests; operator 'fresh session')."""
    path = config.mock_state_path()
    if path.exists():
        path.unlink()


def _now() -> float:
    return time.time()


def _elapsed(t0: float) -> float:
    scale = config.mock_time_scale() or 1.0
    return (_now() - t0) / scale


# ---------------------------------------------------------------------------
# Scan progression
# ---------------------------------------------------------------------------

def _profile_seconds(command: str) -> float:
    v = cmd_sci.validate_command(command)
    motion = v.get("estimated_motion_s") or 5.0
    return motion + policy.PROFILE_SETTLING_S + 0.3


def _advance(st: dict) -> None:
    scan = st.get("scan")
    if not scan or scan.get("finished"):
        return
    per = scan["profile_s"] + scan["overhead_s"]
    total = per * scan["n_profiles"]
    el = _elapsed(scan["started_at"])
    if el >= total:
        scan["finished"] = True
        st["controller"]["state"] = "idle"
        st["mono_energy"] = scan["end_ev"] if not scan["reverse"] else scan["start_ev"]
        st["last_results"] = _synthetic_results(scan, completed=True)
        return
    k = int(el // per)
    within = el - k * per
    scan["current_profile"] = k + 1
    st["controller"]["state"] = "moving_to_start" if within < scan["overhead_s"] * 0.5 else "Running"


def _current_scan_block(st: dict) -> dict | None:
    scan = st.get("scan")
    if not scan or scan.get("finished"):
        return None
    p = cmd_sci.parse_command(scan["command"])
    k = scan.get("current_profile", 1)
    per_sweep = 2 if scan["reverse"] else 1
    return {
        "start_eV": p["start_ev"], "final_eV": p["final_ev"], "freq": p["freq_hz"],
        "command": scan["command"], "region_specs": [x for r in p["regions"] for x in (r["end_ev"], r["speed"])],
        "current_sweep": (k - 1) // per_sweep + 1, "total_sweeps": scan["sweeps"],
        "is_reverse": bool(scan["reverse"] and (k % 2 == 0)),
    }


def _synthetic_results(scan: dict, completed: bool) -> dict:
    p = cmd_sci.parse_command(scan["command"])
    e0, e1 = p["start_ev"], p["final_ev"]
    n = 40
    rows = []
    mid = 0.5 * (e0 + e1)
    for i in range(n):
        e = e0 + (e1 - e0) * i / (n - 1)
        rows.append({"trig_1": (i + 1) * 10000, "absev": e,
                     "vortex": 100.0 + 400.0 / (1 + math.exp(-(e - mid) / 2.0)),
                     "adc_1": 2**31 + 1.0e7, "counter_1": 52})
    return {
        "parameters": {"start_eV": e0, "final_eV": e1, "freq": p["freq_hz"],
                       "command": scan["command"], "direction": 0 if e1 > e0 else 1},
        "data_df": rows,
        "summary": {"parsed_data_points": n, "vortex_mode": "TS", "mock": True,
                    "completed": completed},
        "completion_time": _now(),
    }


# ---------------------------------------------------------------------------
# Route handlers: (status_code, json)
# ---------------------------------------------------------------------------

def _status(st, body):
    _advance(st)
    scan = st.get("scan")
    active = bool(scan and not scan.get("finished") and st["controller"]["state"] not in ("Stopped", "error"))
    return 200, {"success": True, "status": {
        "state": st["controller"]["state"],
        "error_message": st["controller"]["error"],
        "acquisition_active": active,
        "current_scan": _current_scan_block(st),
        "data_summary": {},
        "timestamp": _now(),
        "mock": True,
    }}


def _spec_status(st, body):
    _, s = _status(st, body)
    s = s["status"]
    done = s["state"] in ("Completed", "Stopped", "error", "idle") and not s["acquisition_active"]
    return 200, {"state": "1" if done else "0"}


def _results(st, body):
    if not st.get("last_results"):
        return 400, {"success": False, "error": "No results available"}
    return 200, {"success": True, "results": st["last_results"]}


def _server_settings(st, body):
    return 200, {"success": True, "settings": dict(st["settings"])}


def _undulator_status(st, body):
    u = st["undulator"]
    return 200, {"success": True, "status": {
        "enabled": u["enabled"], "harmonic": u["harmonic"],
        "gap_position": u["gap"] if u["enabled"] else None,
        "gap_calibration_offset": st["settings"]["gap_calibration_offset"],
        "gap_move_latency": st["settings"]["gap_move_latency"]}}


def _anchor(st):
    s = st["settings"]
    if s["table_anchor_energy"] is None:
        return None
    return {"energy": s["table_anchor_energy"], "z1": s["table_anchor_z1"],
            "z2": s["table_anchor_z2"], "crystal": s["table_anchor_crystal"]}


def _table_tracking(st, body):
    a = _anchor(st)
    warning = None
    if a and a.get("crystal") and a["crystal"] != policy.DEFAULT_CRYSTAL:
        warning = (f"anchor was set with crystal {a['crystal']} but crystal "
                   f"{policy.DEFAULT_CRYSTAL} is selected - the anchor is not valid")
    return 200, {"success": True, "enabled": st["settings"]["table_tracking_enabled"],
                 "anchor": a, "crystal_warning": warning}


def _start(st, body):
    if not body or "command" not in body:
        return 400, {"success": False, "error": "Missing command parameter"}
    _advance(st)
    sweeps = int(body.get("sweeps", 1))
    reverse = bool(body.get("reverse", False))
    label = f"{sweeps}-sweep scan" if sweeps > 1 else "Scan"
    if reverse:
        label = f"Forward+Reverse {label}" if sweeps > 1 else "Forward+Reverse scan"
    # The real route answers success before the controller decides; refusals
    # only surface in /status. Reproduced deliberately.
    refused = st["controller"]["state"] != "idle"
    if not refused:
        try:
            p = cmd_sci.parse_command(body["command"])
        except ValueError as e:
            st["controller"].update(state="error", error=f"Failed to parse command: {e}")
            refused = True
    if not refused and st["settings"]["table_tracking_enabled"] and _anchor(st) is None:
        st["controller"].update(state="error", error="table tracking enabled with no anchor")
        refused = True
    if not refused:
        if not 1 <= sweeps <= policy.SWEEPS_MAX:
            st["controller"].update(state="error", error="sweeps out of range")
        else:
            st["scan"] = {
                "command": body["command"], "sweeps": sweeps, "reverse": reverse,
                "undulator_scan": bool(body.get("undulator_scan", False)),
                "file_dir": body.get("file_dir") or st["settings"]["data_directory"],
                "file_root": body.get("file_root") or st["settings"]["file_root"],
                "started_at": _now(), "profile_s": _profile_seconds(body["command"]),
                "overhead_s": policy.PER_SWEEP_OVERHEAD_S,
                "n_profiles": sweeps * (2 if reverse else 1), "current_profile": 1,
                "start_ev": p["start_ev"], "end_ev": p["final_ev"], "finished": False,
            }
            st["controller"].update(state="initializing", error=None)
    _, s = _status(st, None) if not refused else (None, {"status": {"state": st["controller"]["state"]}})
    return 200, {"success": True, "message": f"{label} started", "status": s["status"]}


def _stop(st, body):
    _advance(st)
    scan = st.get("scan")
    if scan and not scan.get("finished"):
        scan["finished"] = True
        st["last_results"] = _synthetic_results(scan, completed=False)
        st["controller"].update(state="Stopped")
        return 200, {"success": True, "message": "Scan stopped"}
    return 200, {"success": False, "message": "No scan running"}


def _reset(st, body):
    scan = st.get("scan")
    if scan:
        scan["finished"] = True
    st["controller"].update(state="idle", error=None)
    return 200, {"success": True, "message": "Controller state reset"}


def _clear_stop(st, body):
    if st["controller"]["state"] in ("Stopped", "error"):
        st["controller"].update(state="idle", error=None)
        return 200, {"success": True, "message": "Stop state cleared"}
    return 200, {"success": False, "message": f"Not in stopped state ({st['controller']['state']})"}


def _set_data_path(st, body):
    if not body:
        return 400, {"success": False, "error": "No data provided"}
    st["settings"]["data_directory"] = body.get("file_dir", "")
    st["settings"]["file_root"] = body.get("file_root", "")
    return 200, {"success": True, "message": f"Data path configured: dir={body.get('file_dir', '')}, "
                 f"root={body.get('file_root', '')}", "settings": dict(st["settings"])}


def _set_undulator_enable(st, body):
    if not body or body.get("enable") is None:
        return 400, {"success": False, "error": "Enable flag is required"}
    st["undulator"]["enabled"] = bool(body["enable"])
    if not st["undulator"]["enabled"]:
        st["undulator"]["harmonic"] = None
    s = "enabled" if st["undulator"]["enabled"] else "disabled"
    return 200, {"success": True, "message": f"Undulator control {s}", "enabled": st["undulator"]["enabled"]}


def _set_gap_offset(st, body):
    try:
        off = float(body["offset"])
    except (KeyError, TypeError, ValueError):
        return 400, {"success": False, "error": "Offset value is required"}
    st["settings"]["gap_calibration_offset"] = off
    return 200, {"success": True, "message": f"Gap calibration offset set to {off:.4f} mm",
                 "offset": off, "settings": dict(st["settings"])}


def _set_gap_latency(st, body):
    try:
        lat = float(body["latency"])
    except (KeyError, TypeError, ValueError):
        return 400, {"success": False, "error": "Latency value is required"}
    if lat < 0:
        return 400, {"success": False, "error": "Latency must be non-negative"}
    st["settings"]["gap_move_latency"] = lat
    return 200, {"success": True, "message": f"Gap move latency set to {lat:.3f} s",
                 "latency": lat, "settings": dict(st["settings"])}


def _set_table_tracking(st, body):
    if not body or body.get("enable") is None:
        return 400, {"success": False, "error": "Enable flag is required"}
    enable = bool(int(body["enable"]))
    if enable and _anchor(st) is None:
        return 400, {"success": False,
                     "error": "No table anchor is set - set one with /set_table_anchor first"}
    st["settings"]["table_tracking_enabled"] = enable
    return 200, {"success": True, "message": f"Table tracking {'enabled' if enable else 'disabled'}",
                 "enabled": enable, "anchor": _anchor(st)}


def _set_table_anchor(st, body):
    body = body or {}
    if body.get("capture"):
        energy, (z1, z2), crystal = st["mono_energy"], st["table_z"], policy.DEFAULT_CRYSTAL
    else:
        try:
            energy, z1, z2 = float(body["energy"]), float(body["z1"]), float(body["z2"])
        except (KeyError, TypeError, ValueError):
            return 400, {"success": False, "error": "energy, z1 and z2 numeric values are required "
                         "(or pass capture: true)"}
        crystal = body.get("crystal")
        if crystal is not None:
            crystal = str(crystal).strip().upper()
            if crystal not in ("A", "B"):
                return 400, {"success": False, "error": "crystal must be A or B"}
    s = st["settings"]
    s.update(table_anchor_energy=energy, table_anchor_z1=z1, table_anchor_z2=z2,
             table_anchor_crystal=crystal)
    return 200, {"success": True, "message": f"Table anchor set: {energy:.2f} eV", "anchor": _anchor(st)}


def _move_energy(st, body):
    try:
        energy = float((body or {})["energy"])
    except (KeyError, TypeError, ValueError):
        return 400, {"success": False, "error": "Energy is required"}
    if not 4500 <= energy <= 25000:
        return 400, {"success": False, "error": f"Energy must be between 4500 and 25000 eV (got {energy})"}
    _advance(st)
    if st.get("scan") and not st["scan"].get("finished"):
        return 500, {"success": False, "error": "Mono is moving (scan in progress)"}
    st["mono_energy"] = energy
    if st["undulator"]["enabled"]:
        hg = beamline.harmonic_gap(energy, st["settings"]["gap_calibration_offset"])
        st["undulator"].update(harmonic=hg["harmonic"], gap=hg["commanded_gap_mm"])
    return 200, {"success": True, "message": f"Mono move initiated to {energy}eV"}


def _move_gap(st, body):
    try:
        gap = float((body or {})["gap"])
    except (KeyError, TypeError, ValueError):
        return 400, {"success": False, "error": "Gap value is required"}
    if not policy.GAP_MIN_MM <= gap <= policy.GAP_MAX_MM:
        return 500, {"success": False, "error": f"Gap {gap} outside {policy.GAP_MIN_MM}-{policy.GAP_MAX_MM} mm"}
    st["undulator"]["gap"] = gap
    return 200, {"success": True, "message": f"Gap move initiated to {gap} mm"}


def _stop_gap(st, body):
    return 200, {"success": True, "message": "Gap motor stopped"}


def _calibrate_energy(st, body):
    e = st["mono_energy"]
    xtal = beamline.bragg_angle_deg(e)
    enc = 267_300_000.0
    st["settings"].update(mono_xtal=xtal, mono_enc=enc)
    return 200, {"success": True, "message": f"Mono calibrated at {e:.1f} eV",
                 "details": {"energy": e, "xtal": xtal, "enc": enc}}


def _daq_advance(st):
    d = st["daq"]
    if d["state"] == "running" and _elapsed(d["started_at"]) >= d["duration_s"]:
        dur = d["duration_s"]
        d["state"] = "idle"
        d["last_result"] = {"ok": True, "duration_s": dur, "gate_mode": d["params"]["gate_mode"],
                            "counters": {"ctr1": round(100 * dur), "ctr2": 0, "ctr3": 0, "ctr4": 0},
                            "hw_window_us": int(dur * 1e6), "mock": True}


def _daq_count(st, body):
    body = body or {}
    _daq_advance(st)
    if st["daq"]["state"] == "running":
        return 409, {"detail": "a count is already running"}
    dur = float(body.get("duration_s", 1.0))
    freq = float(body.get("freq_hz", 10.0))
    mode = body.get("gate_mode", "single")
    if not 0.01 <= dur <= 60:
        return 422, {"detail": "duration_s must be in [0.01, 60]"}
    if not 0.5 <= freq <= 100:
        return 422, {"detail": "freq_hz must be in [0.5, 100]"}
    if mode not in ("single", "chopped"):
        return 422, {"detail": "gate_mode must be 'single' or 'chopped'"}
    st["daq"].update(state="running", started_at=_now(), duration_s=dur,
                     params={"duration_s": dur, "freq_hz": freq, "gate_mode": mode,
                             "quick": bool(body.get("quick", False))})
    return 202, {"accepted": True, "plan": st["daq"]["params"]}


def _daq_status(st, body):
    _daq_advance(st)
    d = st["daq"]
    return 200, {"state": d["state"], "params": d["params"], "last_result": d["last_result"], "mock": True}


def _daq_abort(st, body):
    st["daq"]["state"] = "idle"
    return 200, {"aborted": True}


def _daq_health(st, body):
    return 200, {"ok": True, "ioc": "mock", "mock": True}


ROUTES = {
    "status": _status, "spec_status": _spec_status, "results": _results,
    "server_settings": _server_settings, "undulator_status": _undulator_status,
    "table_tracking": _table_tracking, "start_acquisition": _start, "stop": _stop,
    "reset": _reset, "clear_stop": _clear_stop, "set_data_path": _set_data_path,
    "set_undulator_enable": _set_undulator_enable,
    "set_gap_calibration_offset": _set_gap_offset, "set_gap_move_latency": _set_gap_latency,
    "set_table_tracking": _set_table_tracking, "set_table_anchor": _set_table_anchor,
    "move_energy": _move_energy, "move_gap": _move_gap, "stop_gap": _stop_gap,
    "calibrate_energy": _calibrate_energy, "daq_count": _daq_count, "daq_status": _daq_status,
    "daq_abort": _daq_abort, "daq_health": _daq_health,
}


def handle(command: str, body: dict | None) -> tuple[int, dict]:
    fn = ROUTES[command]
    with _state() as st:
        return fn(st, body or {})
