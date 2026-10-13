"""CHERFD tool handlers: ``t_<name>(args) -> (text, images_b64)``.

The continuous-scan counterpart of ``tools_core.py``, kept in its own module
because it talks to a different transport. Same layering rule: unpack the
arguments, ask ``cherfd_control`` / ``spec_data.cherfd`` for replies or arrays,
call a ``science.cherfd`` function, serialise. Every request to a cherfd
service goes through ``audited_cherfd`` with a *literal* command name -- no
handler builds a URL, and ``tests/test_mutates.py`` reads those literals by
AST exactly as it reads ``audited_call`` in ``tools_core.py``.

``tools_core`` merges ``HANDLERS`` into its ``_HANDLERS``.

Errors: a ``ValueError`` raised anywhere below a handler becomes
``{"ok": false, "error": ...}`` via ``_tool``; anything else propagates to
the executor's ``Tool error (...)`` envelope, as upstream.
"""
from __future__ import annotations

import functools
import json
import re
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from beamtimehero_cli.audited_call import audited_call  # noqa: E402
from beamtimehero_cli.science.plots.scan import fig_to_base64  # noqa: E402

from beamtimehero_cli.cherfd_control import settings as config  # noqa: E402
from beamtimehero_cli.cherfd_control import simulator as sim_bridge  # noqa: E402
from beamtimehero_cli.audited_cherfd import audited_cherfd  # noqa: E402
from beamtimehero_cli.spec_data.cherfd import export, files, loader, sweeps as sweep_data  # noqa: E402
from beamtimehero_cli.science.cherfd import (  # noqa: E402
    analysis, beamline, channels as chan_sci, command as cmd_sci, frames, policy, reduce,
)
from beamtimehero_cli.science.plots import cherfd as plots  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_json(obj) -> str:
    return obj if isinstance(obj, str) else json.dumps(obj, indent=2, default=_default)


def _default(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, Path):
        return str(o)
    return str(o)


def _tool(fn):
    """ValueError -> {"ok": false, "error"} JSON; the one expected failure path."""
    @functools.wraps(fn)
    def wrapper(args: dict) -> tuple[str, list[str]]:
        try:
            return fn(args or {})
        except (ValueError, sim_bridge.SimulatorUnavailable) as e:
            return _as_json({"ok": False, "error": str(e)}), []
    return wrapper


def _j(args: dict) -> str:
    return (args.get("justification") or "").strip()


def _img(fig) -> list[str]:
    try:
        return [fig_to_base64(fig)]
    finally:
        plt.close(fig)


def _round_list(a, sig=7):
    """JSON-friendly list at ``sig`` significant figures (NaN -> null)."""
    return [None if (v is None or not np.isfinite(v)) else float(f"{float(v):.{sig}g}") for v in a]


def _status_body(res: dict) -> dict:
    return ((res.get("result") or {}).get("status") or {}) if res.get("ok") else {}


def _station():
    """This deployment's station profile (BTH_STATION)."""
    return config.station()


def _crystal(args) -> str:
    """The crystal set for offline planning: the caller's, else the station's
    (its crystal file when it has one, as cherfd_claude reads it)."""
    return beamline.crystal_set(args.get("crystal") or config.station_crystal(), station=_station())


def _server_station() -> dict | None:
    """The live server's GET /station body, or None when unreachable."""
    res = audited_cherfd("station")
    return (res.get("result") or {}) if res.get("ok") else None


def _energy_range(server: dict | None):
    rng = (server or {}).get("mono_energy_range_eV")
    return tuple(rng) if rng and len(rng) == 2 else None


def _normalize_command(command: str) -> str:
    command = (command or "").strip()
    if not command.split()[:1] or command.split()[0].lower() not in cmd_sci.COMMAND_KEYWORDS:
        command = "cherfd " + command
    return command


def _needs_undulator(what: str) -> None:
    st = _station()
    if not st.undulator:
        raise ValueError(f"{st.label} has no undulator: {what} does not apply here")


def _needs_daq(what: str) -> None:
    st = _station()
    if not st.daq_available:
        raise ValueError(f"the cscan_daq app is not used at {st.label} ({what}); "
                         "counts there go through SPEC (csct) or cscan_acq")


def _station_mismatch(server: dict | None) -> str | None:
    """Why the live server is not this deployment's station, or None."""
    if not server or not server.get("station"):
        return None
    st = _station()
    if server["station"] != st.name:
        return (f"the cherfd server is station {server['station']!r} but this deployment is "
                f"BTH_STATION={st.name!r}: refusing to plan or scan with the wrong beamline's settings")
    if server.get("table_tracking_available") and not st.table_tracking:
        return (f"the cherfd server offers table tracking but the table never moves in a "
                f"continuous scan at {st.label}: refusing until its station config is put back")
    return None


def _no_table_motion(what: str) -> None:
    """Refuse anything that would set up table motion where the station forbids it."""
    st = _station()
    if not st.table_tracking:
        raise ValueError(f"{st.label}: the table is never moved by a continuous scan, so "
                         f"{what} is refused")


def _table_tracking_on() -> str | None:
    """Why the live server would move the table on the next scan, or None."""
    t = (audited_cherfd("table_tracking").get("result") or {})
    if t.get("enabled") and not _station().table_tracking:
        return ("the cherfd server has table tracking enabled; the table never moves in a "
                f"continuous scan at {_station().label}")
    return None


# ---------------------------------------------------------------------------
# Control -- reads
# ---------------------------------------------------------------------------

@_tool
def t_get_status(args):
    res = audited_cherfd("status")
    done = audited_cherfd("spec_status")
    if res.get("ok"):
        st = _status_body(res)
        res["summary"] = {
            "state": st.get("state"),
            "acquisition_active": st.get("acquisition_active"),
            "done": (done.get("result") or {}).get("state") == "1" if done.get("ok") else None,
            "error_message": st.get("error_message"),
            "current_scan": st.get("current_scan"),
            "stop_latched": st.get("state") in ("Stopped", "error"),
        }
    return _as_json(res), []


@_tool
def t_get_station(args):
    """The deployment's station profile, and what the live server says about it."""
    st = _station()
    profile = {
        "station": st.name, "label": st.label,
        "detector": st.detector, "detector_label": st.detector_label,
        "default_signal": st.default_signal, "detector_setup": st.detector_setup,
        "undulator": st.undulator, "cscan_daq": st.daq_available,
        "crystals": st.crystal_names, "crystal_in_use_offline": config.station_crystal(st),
        "motor_step_deg": st.motor_step_deg, "encoder_resolution_deg": st.encoder_resolution_deg,
        "offline_energy_window_ev": ([st.energy_min_ev, st.energy_max_ev]
                                     if st.energy_min_ev is not None else None),
        "per_sweep_overhead_s": st.per_sweep_overhead_s,
    }
    server = _server_station()
    det = audited_cherfd("detector")
    det_body = (det.get("result") or {}) if det.get("ok") else {}
    problems = []
    mismatch = _station_mismatch(server)
    if mismatch:
        problems.append(mismatch)
    if server and server.get("motor_step_degrees") not in (None, st.motor_step_deg):
        problems.append(f"server motor_step_degrees {server['motor_step_degrees']} != "
                        f"station profile {st.motor_step_deg}")
    if det_body.get("detector") not in (None, st.detector, "none"):
        problems.append(f"server scan detector is {det_body['detector']!r}; {st.label} uses "
                        f"{st.detector!r}")
    return _as_json({"ok": not problems, "profile": profile, "server": server,
                     "server_detector": det_body.get("detector"),
                     "server_reachable": server is not None, "problems": problems,
                     "mock": config.mock_enabled()}), []


@_tool
def t_check_command(args):
    """The server's own dry run of a command against the live mono (read-only)."""
    command = _normalize_command(args["command"])
    res = audited_cherfd("check_command", {"command": command})
    if not res.get("ok"):
        return _as_json(res), []
    body = res.get("result") or {}
    out = {k: v for k, v in body.items() if k != "success"}
    out["transport"] = res.get("transport")
    return _as_json(out), []


@_tool
def t_get_server_settings(args):
    return _as_json(audited_cherfd("server_settings")), []


@_tool
def t_get_undulator_status(args):
    return _as_json(audited_cherfd("undulator_status")), []


@_tool
def t_get_table_tracking(args):
    return _as_json(audited_cherfd("table_tracking")), []


@_tool
def t_get_scan_results(args):
    """Last completed sweep from the controller, summarized (not the full frame dump)."""
    res = audited_cherfd("results")
    if not res.get("ok"):
        return _as_json(res), []
    r = (res.get("result") or {}).get("results") or {}
    rows = r.get("data_df") or []
    cols = sorted({k for row in rows[:50] for k in row})
    preview_n = int(args.get("preview_rows", 5))
    out = {"ok": True, "transport": res.get("transport"), "parameters": r.get("parameters"),
           "summary": r.get("summary"), "completion_time": r.get("completion_time"),
           "n_rows": len(rows), "columns": cols, "preview": rows[:preview_n]}
    if rows and "absev" in cols:
        e = np.array([row.get("absev") for row in rows], float)
        out["energy_range"] = [float(np.nanmin(e)), float(np.nanmax(e))]
    return _as_json(out), []


@_tool
def t_wait_for_scan(args):
    """Poll /spec_status until the acquisition is done or timeout (read-only)."""
    timeout = float(args.get("timeout_s", 600))
    poll = max(0.2, float(args.get("poll_s", 2.0)))
    if timeout > 7200:
        raise ValueError("timeout_s is capped at 7200 s; call again to keep waiting")
    t0 = time.time()
    confirmations = 0
    polls = 0
    while True:
        polls += 1
        r = audited_cherfd("spec_status")
        if r.get("ok") and (r.get("result") or {}).get("state") == "1":
            confirmations += 1
            if confirmations >= 2:           # cherfd_wait confirms "done" twice
                break
        else:
            confirmations = 0
        if time.time() - t0 >= timeout:
            st = _status_body(audited_cherfd("status"))
            return _as_json({"ok": False, "done": False, "timed_out": True,
                             "waited_s": round(time.time() - t0, 1), "polls": polls,
                             "state": st.get("state"), "current_scan": st.get("current_scan")}), []
        time.sleep(poll)
    st = _status_body(audited_cherfd("status"))
    failed = st.get("state") == "error"
    out = {"ok": not failed, "done": True, "waited_s": round(time.time() - t0, 1),
           "polls": polls, "final_state": st.get("state"),
           "error_message": st.get("error_message"),
           "stopped": st.get("state") == "Stopped",
           "next": "cherfd_get_latest_run then cherfd_assess_run_quality"}
    if failed:
        # the controller ends a sweep in ERROR on purpose: short detector
        # frames, a profile cut short, the box triggers changed under it
        out["error"] = f"the scan ended in error: {st.get('error_message')}"
        out["next"] = ("read error_message; cherfd_clear_stop before the next scan. Data saved "
                       "before the failure is still on disk (cherfd_get_latest_run)")
    return _as_json(out), []


@_tool
def t_get_daq_status(args):
    _needs_daq("cherfd_get_daq_status")
    return _as_json({"health": audited_cherfd("daq_health"),
                     "status": audited_cherfd("daq_status")}), []


@_tool
def t_check_readiness(args):
    """Aggregate pre-flight for a scan. Read-only; says what blocks and what to fix."""
    blockers, warnings, info = [], [], {}
    station = _station()
    info["station"] = station.name
    server = _server_station()
    mismatch = _station_mismatch(server)
    if mismatch:
        blockers.append(mismatch)
    st = audited_cherfd("status")
    if not st.get("ok"):
        blockers.append(f"control server unreachable: {st.get('error')}")
    else:
        s = _status_body(st)
        info["state"] = s.get("state")
        if s.get("state") in ("Stopped", "error"):
            blockers.append(f"controller is '{s.get('state')}' ({s.get('error_message')}); "
                            "run cherfd_clear_stop (or cherfd_reset_controller)")
        elif s.get("state") != "idle" or s.get("acquisition_active"):
            blockers.append(f"controller busy (state={s.get('state')}); wait or stop the scan")
    settings = audited_cherfd("server_settings")
    sset = ((settings.get("result") or {}).get("settings") or {}) if settings.get("ok") else {}
    info["data_path"] = {"file_dir": sset.get("data_directory"), "file_root": sset.get("file_root")}
    if settings.get("ok") and not sset.get("data_directory"):
        warnings.append("no data directory set: files go to the data root (cherfd_set_data_path)")
    if settings.get("ok") and sset.get("mono_enc") is None:
        warnings.append("no stored mono encoder calibration (cherfd_calibrate_encoder)")
    u = {}
    if station.undulator:
        und = audited_cherfd("undulator_status")
        u = ((und.get("result") or {}).get("status") or {}) if und.get("ok") else {}
        info["undulator"] = u
        if und.get("ok") and not u.get("enabled"):
            warnings.append("undulator control disabled: gap will not track the scan")
    det = audited_cherfd("detector")
    det_name = ((det.get("result") or {}).get("detector")) if det.get("ok") else None
    info["detector"] = det_name
    if det_name == "none":
        warnings.append(f"scan detector is 'none': only the box channels are recorded "
                        f"(transmission / TFY); the {station.detector} is not read")
    elif det_name not in (None, station.detector):
        blockers.append(f"scan detector is {det_name!r}; {station.label} uses {station.detector!r}")
    box = audited_cherfd("box_status")
    b = (box.get("result") or {}) if box.get("ok") else {}
    if box.get("ok") and not b.get("motion_busy"):
        info["box"] = {k: b.get(k) for k in ("trig", "armed_axes", "idle")}
        if b.get("count_running"):
            warnings.append("a count is running on the cscan box (SPEC ct?): the scan waits for it "
                            "(up to ~70 s) before taking the box")
        if b.get("triggers_enabled"):
            blockers.append("cscan box TRIG is Enable with no cherfd scan running: another client "
                            "(cscan_acq?) or a dead run holds the box; scans refuse")
        if b.get("armed_axes"):
            warnings.append(f"axes left armed: {b['armed_axes']}; the next scan disarms them first")
        if b.get("error"):
            warnings.append(f"cscan box status: {b['error']}")
    tt = audited_cherfd("table_tracking")
    t = tt.get("result") or {}
    info["table_tracking"] = {"enabled": t.get("enabled"), "anchor": t.get("anchor")}
    if t.get("enabled") and not station.table_tracking:
        blockers.append(f"table tracking is enabled on the server; the table never moves in a "
                        f"continuous scan at {station.label} (cherfd_set_table_tracking enable=false)")
    elif t.get("enabled") and not t.get("anchor"):
        blockers.append("table tracking enabled with no anchor: scans will refuse to start")
    if t.get("crystal_warning"):
        blockers.append(t["crystal_warning"])
    command = args.get("command")
    if command:
        command = _normalize_command(command)
        crystal = (server or {}).get("crystal") or config.station_crystal(station)
        v = cmd_sci.validate_command(command, crystal, station=station,
                                     energy_range_ev=_energy_range(server))
        info["command_validation"] = {k: v[k] for k in ("ok", "effective_command", "errors",
                                                        "warnings", "harmonic_changes")}
        blockers += [f"command: {e}" for e in v["errors"]]
        warnings += [f"command: {w}" for w in v["warnings"] if "checked live" not in w]
        if v["ok"]:
            chk = audited_cherfd("check_command", {"command": command})
            c = chk.get("result") or {}
            info["live_check"] = {k: c.get(k) for k in ("ok", "error", "theta_start_deg",
                                                        "theta_end_deg", "crystal")}
            if chk.get("ok") and c.get("ok") is False:
                blockers.append(f"command (server check): {c.get('error')}")
            elif not chk.get("ok"):
                warnings.append(f"server command check unavailable: {chk.get('error')}")
        if v.get("harmonic_changes") and u.get("enabled"):
            warnings.append("scan range crosses an undulator harmonic change "
                            f"{v['harmonic_changes']}; expect an intensity step there")
    if args.get("include_beam_status", True):
        beam = audited_call("beam_status", [], justification="")
        b = beam.get("result") or {}
        if beam.get("ok"):
            info["beam"] = {k: b.get(k) for k in ("spear_current_ma", "beamline_state",
                                                  "gap_owned", "beam_good", "reason")}
            if b.get("beam_good") is False:
                blockers.append(f"beam not good: {b.get('reason')}")
        else:
            warnings.append(f"beam status unavailable (spec-read beam_status): {beam.get('error')}")
    if det_name != "none":
        warnings.append(f"cannot be checked from here: {station.detector_setup}")
    return _as_json({"ok": True, "ready": not blockers, "blockers": blockers,
                     "warnings": warnings, "info": info,
                     "mock": config.mock_enabled()}), []


# ---------------------------------------------------------------------------
# Control -- actions
# ---------------------------------------------------------------------------

@_tool
def t_start_scan(args):
    command = _normalize_command(args.get("command") or "")
    station = _station()
    sweeps = int(args.get("sweeps", 1))
    if not 1 <= sweeps <= policy.SWEEPS_MAX:
        raise ValueError(f"sweeps must be 1..{policy.SWEEPS_MAX}")
    undulator_scan = args.get("undulator_scan")
    undulator_scan = station.undulator if undulator_scan is None else bool(undulator_scan)
    if undulator_scan and not station.undulator:
        raise ValueError(f"{station.label} has no undulator: undulator_scan must be false")
    server = _server_station()
    mismatch = _station_mismatch(server)
    if mismatch:
        return _as_json({"ok": False, "refused": "station", "error": mismatch}), []
    crystal = (server or {}).get("crystal") or config.station_crystal(station)
    v = cmd_sci.validate_command(command, crystal, station=station,
                                 energy_range_ev=_energy_range(server))
    if not v["ok"]:
        return _as_json({"ok": False, "refused": "validation", "errors": v["errors"],
                         "warnings": v["warnings"]}), []
    if not _j(args):
        return _as_json({"ok": False, "error": "justification is required"}), []
    table = _table_tracking_on()
    if table:
        return _as_json({"ok": False, "refused": "table_tracking", "error": table}), []
    pre = _status_body(audited_cherfd("status"))
    if pre.get("state") != "idle" or pre.get("acquisition_active"):
        return _as_json({"ok": False, "refused": "controller_not_idle", "state": pre.get("state"),
                         "hint": "cherfd_wait_for_scan, cherfd_stop_scan or cherfd_clear_stop"}), []
    body = {"command": command, "sweeps": sweeps, "reverse": bool(args.get("reverse", False)),
            "undulator_scan": undulator_scan,
            "file_dir": args.get("file_dir"), "file_root": args.get("file_root")}
    res = audited_cherfd("start_acquisition", body, _j(args))
    if not res.get("ok"):
        return _as_json(res), []
    # /start_acquisition answers success before the controller decides; confirm
    # from the status it embeds (taken 0.1 s after the worker starts) and from
    # a fresh read. Either showing the acquisition active counts as started.
    running = ("initializing", "moving_to_start", "Running")
    embedded = ((res.get("result") or {}).get("status") or {})
    time.sleep(0.5)
    post = _status_body(audited_cherfd("status"))
    started = any(bool(s.get("acquisition_active")) or s.get("state") in running
                  for s in (embedded, post))
    per = v["estimated_motion_s"] + policy.PROFILE_SETTLING_S
    est = cmd_sci.estimate_acquisition_time(per, sweeps, body["reverse"], station.per_sweep_overhead_s)
    res.update({"started": started, "state_after": post.get("state"),
                "error_message": post.get("error_message"),
                "validation_warnings": v["warnings"], "estimate": est})
    if not started:
        res["ok"] = False
        res["error"] = (f"controller did not start the scan (state={post.get('state')}, "
                        f"error={post.get('error_message')})")
    return _as_json(res), []


@_tool
def t_stop_scan(args):
    return _as_json(audited_cherfd("stop", {}, _j(args))), []


@_tool
def t_clear_stop(args):
    return _as_json(audited_cherfd("clear_stop", {}, _j(args))), []


@_tool
def t_reset_controller(args):
    return _as_json(audited_cherfd("reset", {}, _j(args))), []


@_tool
def t_stop_gap(args):
    _needs_undulator("cherfd_stop_gap")
    return _as_json(audited_cherfd("stop_gap", {}, _j(args))), []


@_tool
def t_daq_abort(args):
    """Abort a cscan_daq count and any ADC stream."""
    _needs_daq("cherfd_daq_abort")
    return _as_json({"count": audited_cherfd("daq_abort", {}, _j(args)),
                     "stream": audited_cherfd("daq_stream_abort", {}, _j(args))}), []


@_tool
def t_calibrate_encoder(args):
    return _as_json(audited_cherfd("calibrate_energy", {}, _j(args))), []


@_tool
def t_set_data_path(args):
    body = {"file_dir": args.get("file_dir", ""), "file_root": args.get("file_root", "")}
    return _as_json(audited_cherfd("set_data_path", body, _j(args))), []


@_tool
def t_set_detector(args):
    name = str(args.get("detector") or "").strip().lower()
    station = _station()
    if name not in ("none", station.detector):
        raise ValueError(f"{station.label} detectors are 'none' and {station.detector!r}, not {name!r}")
    mismatch = _station_mismatch(_server_station())
    if mismatch:
        raise ValueError(mismatch)
    # the server refuses during an acquisition (400); confirm it took from /get_detector
    res = audited_cherfd("set_detector", {"detector": name}, _j(args))
    after = audited_cherfd("detector")
    res["detector_after"] = (after.get("result") or {}).get("detector") if after.get("ok") else None
    if res.get("ok") and res["detector_after"] != name:
        res["ok"] = False
        res["error"] = f"server reports detector {res['detector_after']!r} after setting {name!r}"
    return _as_json(res), []


@_tool
def t_set_undulator_tracking(args):
    if args.get("enable"):
        _needs_undulator("undulator tracking")
    return _as_json(audited_cherfd("set_undulator_enable", {"enable": args["enable"]}, _j(args))), []


@_tool
def t_set_gap_calibration_offset(args):
    _needs_undulator("a gap calibration offset")
    return _as_json(audited_cherfd("set_gap_calibration_offset",
                                   {"offset": args["offset_mm"]}, _j(args))), []


@_tool
def t_set_gap_move_latency(args):
    _needs_undulator("a gap move latency")
    return _as_json(audited_cherfd("set_gap_move_latency", {"latency": args["latency_s"]}, _j(args))), []


@_tool
def t_set_table_tracking(args):
    if args.get("enable"):
        _no_table_motion("enabling table tracking")
    return _as_json(audited_cherfd("set_table_tracking", {"enable": args["enable"]}, _j(args))), []


@_tool
def t_set_table_anchor(args):
    _no_table_motion("a table anchor")
    if not args.get("capture") and any(args.get(k) is None for k in ("energy_ev", "z1_mm", "z2_mm")):
        raise ValueError("give energy_ev, z1_mm and z2_mm, or capture=true")
    body = {"capture": bool(args.get("capture"))} if args.get("capture") else {
        "energy": args["energy_ev"], "z1": args["z1_mm"], "z2": args["z2_mm"],
        "crystal": args.get("crystal")}
    return _as_json(audited_cherfd("set_table_anchor", body, _j(args))), []


@_tool
def t_move_energy(args):
    e = float(args["energy_ev"])
    station = _station()
    server = _server_station()
    mismatch = _station_mismatch(server)
    if mismatch:
        raise ValueError(mismatch)
    rng = _energy_range(server)
    if rng and not min(rng) <= e <= max(rng):
        raise ValueError(f"energy {e} outside the mono's reach {min(rng):.0f}-{max(rng):.0f} eV "
                         "(MOTOR1 soft limits)")
    # the server answers 409 while a scan (or the gap between its sweeps) owns the mono
    res = audited_cherfd("move_energy", {"energy": e}, _j(args))
    if station.undulator:
        settings = audited_cherfd("server_settings")
        offset = float(((settings.get("result") or {}).get("settings") or {}).get(
            "gap_calibration_offset") or 0.0)
        res["expected_gap"] = beamline.harmonic_gap(e, offset)
    return _as_json(res), []


@_tool
def t_move_gap(args):
    _needs_undulator("cherfd_move_gap")
    g = float(args["gap_mm"])
    if not policy.GAP_MIN_MM <= g <= policy.GAP_MAX_MM:
        raise ValueError(f"gap {g} outside {policy.GAP_MIN_MM}-{policy.GAP_MAX_MM} mm")
    return _as_json(audited_cherfd("move_gap", {"gap": g}, _j(args))), []


@_tool
def t_daq_count(args):
    _needs_daq("cherfd_daq_count")
    body = {"duration_s": float(args.get("duration_s", 1.0)),
            "freq_hz": float(args.get("freq_hz", 10.0)),
            "gate_mode": args.get("gate_mode", "single"), "quick": bool(args.get("quick", False))}
    res = audited_cherfd("daq_count", body, _j(args))
    if res.get("ok") and args.get("wait", True):
        deadline = time.time() + body["duration_s"] + 15
        while time.time() < deadline:
            time.sleep(min(0.5, body["duration_s"] / 4 + 0.05))
            s = audited_cherfd("daq_status")
            if s.get("ok") and (s.get("result") or {}).get("state") not in ("running", "counting"):
                res["count_result"] = (s.get("result") or {}).get("last_result")
                break
        else:
            res["count_result"] = None
            res["note"] = "count did not report completion in time; check cherfd_get_daq_status"
    return _as_json(res), []


# ---------------------------------------------------------------------------
# Planning (offline)
# ---------------------------------------------------------------------------

@_tool
def t_validate_command(args):
    return _as_json(cmd_sci.validate_command(args["command"], _crystal(args), station=_station())), []


@_tool
def t_build_command(args):
    edge_ev = args.get("edge_ev")
    edge_info = None
    if edge_ev is None:
        if not args.get("element"):
            raise ValueError("give edge_ev, or element (+ edge, default K)")
        from beamtimehero_cli.science.tables.edges import get_edge_info
        edge_info = get_edge_info(args["element"], args.get("edge", "K"))
        edge_ev = edge_info["tabulated_energy_ev"]
    kw = {k: args[k] for k in ("pre_edge_ev", "edge_lo_ev", "edge_hi_ev", "post_edge_ev",
                               "edge_points_per_ev", "outer_speed", "freq_hz")
          if args.get(k) is not None}
    out = cmd_sci.build_command(float(edge_ev), crystal=_crystal(args), station=_station(), **kw)
    out["edge_info"] = edge_info
    return _as_json(out), []


@_tool
def t_build_exafs_command(args):
    edge_ev = args.get("edge_ev")
    edge_info = None
    if edge_ev is None:
        if not args.get("element"):
            raise ValueError("give edge_ev, or element (+ edge, default K)")
        from beamtimehero_cli.science.tables.edges import get_edge_info
        edge_info = get_edge_info(args["element"], args.get("edge", "K"))
        edge_ev = edge_info["tabulated_energy_ev"]
    if args.get("k_max") is None:
        raise ValueError("k_max is required")
    kw = {k: args[k] for k in ("dk", "k_regions", "pre_edge_ev", "edge_lo_ev", "edge_hi_ev",
                               "edge_points_per_ev", "outer_speed", "freq_hz")
          if args.get(k) is not None}
    if "k_regions" in kw:
        kw["k_regions"] = int(kw["k_regions"])
    out = cmd_sci.build_exafs_command(float(edge_ev), float(args["k_max"]), crystal=_crystal(args),
                                      station=_station(), **kw)
    out["edge_info"] = edge_info
    return _as_json(out), []


@_tool
def t_simulate_scan(args):
    command = args["command"]
    anchor = None
    if args.get("table_anchor_ev") is not None:
        anchor = {"energy": float(args["table_anchor_ev"]), "crystal": _crystal(args)}
    sim = sim_bridge.simulate(command, ramp_type=args.get("ramp_type", "s_ramp"),
                              table=bool(args.get("table", False)), table_anchor=anchor)
    out = sim_bridge.summarize(sim)
    imgs = _img(plots.plot_profile(sim)) if (args.get("plot", True) and sim.get("ok")) else []
    return _as_json(out), imgs


@_tool
def t_estimate_acquisition_time(args):
    command = args["command"]
    sweeps = int(args.get("sweeps", 1))
    reverse = bool(args.get("reverse", False))
    source = "cruise estimate (no ramps)"
    motion = None
    if args.get("use_simulator", True):
        try:
            s = sim_bridge.simulate(command)
            if s.get("ok"):
                motion = float(s["summary"]["scan_duration"])
                source = "production simulator"
            else:
                return _as_json({"ok": False, "error": f"simulator refused: {s.get('error')}"}), []
        except sim_bridge.SimulatorUnavailable as e:
            source = f"cruise estimate (simulator unavailable: {e})"
    if motion is None:
        v = cmd_sci.validate_command(command, _crystal(args), station=_station())
        if not v["ok"]:
            return _as_json({"ok": False, "errors": v["errors"]}), []
        motion = v["estimated_motion_s"] + policy.PROFILE_SETTLING_S
    overhead = args.get("overhead_s")
    overhead_src = f"{_station().label} per-sweep overhead (station profile)"
    if overhead is None and args.get("reference_run_id"):
        run = files.resolve_run(files.require_root(config.data_dir()), run_id=args["reference_run_id"])
        if run["seconds_per_profile"]:
            overhead = max(0.0, run["seconds_per_profile"] - motion)
            overhead_src = f"measured cadence of {run['run_id']} minus profile time"
    if overhead is None:
        overhead = _station().per_sweep_overhead_s
    est = cmd_sci.estimate_acquisition_time(motion, sweeps, reverse, float(overhead))
    est.update({"ok": True, "command": command, "motion_source": source,
                "overhead_source": overhead_src})
    return _as_json(est), []


@_tool
def t_undulator_gap_for_energy(args):
    _needs_undulator("cherfd_undulator_gap_for_energy")
    offset = float(args.get("calibration_offset_mm", 0.0))
    energies = args.get("energies_ev")
    if energies is None and args.get("energy_ev") is not None:
        energies = [args["energy_ev"]]
    out = {"points": [beamline.harmonic_gap(float(e), offset) for e in (energies or [])]}
    if args.get("e_start") is not None and args.get("e_end") is not None:
        e0, e1 = float(args["e_start"]), float(args["e_end"])
        out["scan_range"] = {"e_start": e0, "e_end": e1,
                             "start": beamline.harmonic_gap(e0, offset),
                             "end": beamline.harmonic_gap(e1, offset),
                             "harmonic_changes": beamline.harmonic_changes(e0, e1)}
    if not out["points"] and "scan_range" not in out:
        raise ValueError("give energy_ev, energies_ev, or e_start + e_end")
    return _as_json(out), []


@_tool
def t_mono_convert(args):
    station = _station()
    crystal = _crystal(args)
    out: dict = {"station": station.name, "crystal": crystal,
                 "crystal_name": station.crystal_names[crystal]}
    if args.get("energy_ev") is not None:
        e = float(args["energy_ev"])
        out["energy_ev"] = e
        out["bragg_deg"] = beamline.bragg_angle_deg(e, crystal, station=station)
        out["ev_per_s_at_speed_1"] = beamline.ev_per_second(e, 1.0, crystal, station=station)
        if args.get("anchor_ev") is not None:
            out["beam_height_offset_mm"] = beamline.beam_height_offset_mm(
                e, float(args["anchor_ev"]), crystal, station=station)
    elif args.get("bragg_deg") is not None:
        out["bragg_deg"] = float(args["bragg_deg"])
        out["energy_ev"] = beamline.energy_from_bragg(out["bragg_deg"], crystal, station=station)
    elif args.get("encoder") is not None:
        if args.get("calibration_offset_deg") is None:
            raise ValueError("encoder conversion needs calibration_offset_deg "
                             "(mono_xtal - mono_enc*resolution, see cherfd_get_server_settings)")
        ang = beamline.angle_from_encoder(float(args["encoder"]), float(args["calibration_offset_deg"]),
                                          station=station)
        out.update(encoder=float(args["encoder"]), bragg_deg=ang,
                   energy_ev=beamline.energy_from_bragg(ang, crystal, station=station))
    else:
        raise ValueError("give one of energy_ev, bragg_deg, encoder")
    if args.get("freq_hz") and out.get("energy_ev"):
        out["trigger_timing"] = beamline.trigger_timing(float(args["freq_hz"]))
        if args.get("points_per_ev"):
            out["speed_for_points_per_ev"] = beamline.speed_for_points_per_ev(
                out["energy_ev"], out["trigger_timing"]["real_freq_hz"], float(args["points_per_ev"]),
                crystal, station=station)
    return _as_json(out), []


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def _root() -> Path:
    return files.require_root(config.data_dir())


def _run(args) -> dict:
    return files.resolve_run(_root(), run_id=args.get("run_id"), file_dir=args.get("file_dir"),
                             file_root=args.get("file_root"),
                             include_partial=bool(args.get("include_partial", False)))


def _clean_selected(args) -> tuple[dict, list[dict]]:
    run = _run(args)
    sel = files.select_sweeps(run, args.get("sweeps"), args.get("direction", "both"))
    align = args.get("align", "trigger")
    cleaned = [sweep_data.load_clean(sf, align=align) for sf in sel]
    return run, cleaned


def _signal_args(args) -> tuple[str, str | None]:
    return args.get("signal") or _station().default_signal, args.get("normalize_by") or None


def _bin(cleaned: list[dict], args, signal, norm):
    items = [{"label": c["sweep"].label, "direction": c["sweep"].direction, "arrays": c["arrays"]}
             for c in cleaned]
    if args.get("direction_correction_ev"):
        items = analysis.apply_direction_correction(items, float(args["direction_correction_ev"]))
    return reduce.bin_sweeps(
        items,
        signal=signal, normalize_by=norm, bin_ev=float(args.get("bin_ev", policy.DEFAULT_BIN_EV)),
        e_min=args.get("e_min"), e_max=args.get("e_max"),
        per_sweep_scaling=args.get("per_sweep_scaling", "none"))


def _selection_echo(run, cleaned, signal, norm, args) -> dict:
    notes = sorted({n for c in cleaned for n in c["notes"]})
    echo = {"run_id": run["run_id"], "sweeps_used": [c["sweep"].label for c in cleaned],
            "signal": signal, "normalize_by": norm, "alignment": args.get("align", "trigger"),
            "direction_correction_ev": args.get("direction_correction_ev") or 0.0}
    if not args.get("signal"):
        echo["signal_warning"] = (f"signal defaulted to {_station().default_signal!r} "
                                  f"({_station().label}); set it explicitly "
                                  "(counter selection is an experiment input)")
    if not norm:
        echo["normalization_note"] = ("no normalizer: intensity includes ring-current/I0 "
                                      "variation (set normalize_by, e.g. an adc_* I0 channel)")
    if notes:
        echo["notes"] = notes
    return echo


@_tool
def t_list_data_dirs(args):
    root = _root()
    return _as_json({"data_root": str(root), "dirs": files.list_data_dirs(root)}), []


@_tool
def t_list_runs(args):
    root = _root()
    found = files.find_sweeps(root, args.get("file_dir"), args.get("file_root"),
                              bool(args.get("include_partial", False)))
    runs = files.group_runs(found)
    pat = args.get("contains")
    if pat:
        runs = [r for r in runs if pat.lower() in r["file_root"].lower()]
    limit = int(args.get("limit", 20))
    sel = runs[-limit:]
    return _as_json({"total_runs": len(runs), "shown": len(sel),
                     "runs": [files.public_run(r, include_sweeps=False) for r in reversed(sel)]}), []


@_tool
def t_get_latest_run(args):
    run = _run(args)
    out = files.public_run(run)
    meta = loader.load_metadata(run["sweeps"][0])
    out["command"] = meta.get("command")
    return _as_json(out), []


@_tool
def t_get_sweep_metadata(args):
    run = _run(args)
    sel = files.select_sweeps(run, args.get("sweeps"), args.get("direction", "both"))
    return _as_json({"run_id": run["run_id"], "sweeps": [
        {"label": s.label, **loader.load_metadata(s)} for s in sel]}), []


@_tool
def t_read_sweep(args):
    run = _run(args)
    sel = files.select_sweeps(run, [int(args["sweep"])] if args.get("sweep") is not None else None,
                              args.get("direction", "fwd"))
    c = sweep_data.load_clean(sel[0], align=args.get("align", "trigger"))
    a = c["arrays"]
    n = len(a["absev"])
    max_points = int(args.get("max_points", 200))
    step = max(1, int(np.ceil(n / max_points)))
    cols = args.get("columns") or ["trigger", "absev", _station().default_signal]
    cols = [x for x in cols if x in a]
    out = {"run_id": run["run_id"], "sweep": sel[0].label, "file": Path(sel[0].path).name,
           "command": c["commanded"]["command"], "columns_available": sorted(a),
           "n_frames": n, "decimation": step, "qc": c["qc"], "provenance": c["provenance"],
           "notes": c["notes"],
           "data": {k: _round_list(a[k][::step]) for k in cols}}
    return _as_json(out), []


# ---------------------------------------------------------------------------
# Quality, reduction, analysis
# ---------------------------------------------------------------------------

@_tool
def t_assess_run_quality(args):
    run, cleaned = _clean_selected(args)
    signal, _ = _signal_args(args)
    per = []
    for c in cleaned:
        q = frames.sweep_quality(c, signal=signal, expected_range=c["commanded"]["energy_range"],
                                 expected_freq_hz=c["commanded"]["freq_hz"],
                                 range_shortfall_warn_ev=_station().range_shortfall_warn_ev)
        per.append({"sweep": c["sweep"].label, "verdict": q["verdict"], "issues": q["issues"],
                    "signal": q["signal"], "coverage": q["coverage"],
                    "frames": c["qc"]["frames_kept"],
                    "missing_triggers": c["qc"]["missing_triggers"],
                    "recorded_pairing_max_offset_frames": c["qc"].get("recorded_pairing_max_offset_frames")})
    verdicts = [p["verdict"] for p in per]
    overall = "fail" if "fail" in verdicts else "warn" if "warn" in verdicts else "ok"
    commands = sorted({c["commanded"]["command"] for c in cleaned if c["commanded"]["command"]})
    run_issues = []
    if len({c.split()[-1] for c in commands}) > 1:
        run_issues.append("sweeps in this run were taken at different trigger rates")
    offsets = [p["recorded_pairing_max_offset_frames"] for p in per if p["recorded_pairing_max_offset_frames"]]
    if offsets:
        run_issues.append(f"recorded detector pairing is off by up to {max(offsets)} frames; "
                          "these tools re-align by trigger number (align='trigger')")
    return _as_json({"run_id": run["run_id"], "overall": overall, "signal": signal,
                     "commands": commands, "run_issues": run_issues, "sweeps": per,
                     "usable_sweeps": [p["sweep"] for p in per if p["verdict"] != "fail"]}), []


@_tool
def t_merge_sweeps(args):
    run, cleaned = _clean_selected(args)
    signal, norm = _signal_args(args)
    if args.get("exclude_failed", True):
        verdicts = [frames.sweep_quality(c, signal=signal)["verdict"] for c in cleaned]
        excluded = [c["sweep"].label for c, v in zip(cleaned, verdicts) if v == "fail"]
        cleaned = [c for c, v in zip(cleaned, verdicts) if v != "fail"]
    else:
        excluded = []
    if not cleaned:
        raise ValueError(f"every selected sweep failed QC (excluded {excluded}); see cherfd_assess_run_quality")
    b = _bin(cleaned, args, signal, norm)
    max_points = int(args.get("max_points", 400))
    step = max(1, int(np.ceil(len(b["energy"]) / max_points)))
    out = {**_selection_echo(run, cleaned, signal, norm, args), "excluded_failed_sweeps": excluded,
           "bin_ev": b["bin_ev"], "n_bins": len(b["energy"]), "empty_bins": b["empty_bins"],
           "error_model": b["error_model"], "per_sweep_scaling": b["per_sweep_scaling"],
           "noise_estimate": reduce.noise_estimate(b["merged"]),
           "decimation": step,
           "energy": _round_list(b["energy"][::step]),
           "merged": _round_list(b["merged"][::step]),
           "sem": _round_list(b["sem"][::step]),
           "frames_per_bin": _round_list(b["frames_per_bin"][::step]),
           "next": "cherfd_export_merged, then the spec-file XAS tools on that file"}
    imgs = _img(plots.plot_merged(b, title=run["run_id"])) if args.get("plot", True) else []
    return _as_json(out), imgs


@_tool
def t_compare_directions(args):
    run = _run(args)
    signal, norm = _signal_args(args)
    align = args.get("align", "trigger")
    out = {"run_id": run["run_id"], "signal": signal, "normalize_by": norm, "alignment": align}
    groups = {}
    for d in ("fwd", "rev"):
        sel = files.select_sweeps(run, args.get("sweeps"), d)
        groups[d] = [sweep_data.load_clean(sf, align=align) for sf in sel]
    res = analysis.compare_directions(
        [{"label": c["sweep"].label, "arrays": c["arrays"]} for c in groups["fwd"]],
        [{"label": c["sweep"].label, "arrays": c["arrays"]} for c in groups["rev"]],
        signal=signal, normalize_by=norm, bin_ev=float(args.get("bin_ev", policy.DEFAULT_BIN_EV)),
        e_min=args.get("e_min"), e_max=args.get("e_max"))
    imgs = []
    if args.get("plot", True):
        imgs = _img(plots.plot_merged(res.pop("_fwd"), title=f"{run['run_id']}: fwd vs rev",
                                      others=[("rev", res.pop("_rev"))]))
    else:
        res.pop("_fwd"), res.pop("_rev")
    out.update(res)
    return _as_json(out), imgs


@_tool
def t_analyze_sweep_drift(args):
    run, cleaned = _clean_selected(args)
    signal, norm = _signal_args(args)
    res = analysis.sweep_drift(
        [{"label": c["sweep"].label, "direction": c["sweep"].direction, "sweep": c["sweep"].sweep,
          "arrays": c["arrays"]} for c in cleaned],
        signal=signal, normalize_by=norm, bin_ev=float(args.get("bin_ev", policy.DEFAULT_BIN_EV)),
        e_min=args.get("e_min"), e_max=args.get("e_max"))
    return _as_json({**_selection_echo(run, cleaned, signal, norm, args), **res}), []


@_tool
def t_analyze_convergence(args):
    run, cleaned = _clean_selected(args)
    signal, norm = _signal_args(args)
    b = _bin(cleaned, args, signal, norm)
    conv = reduce.convergence([s["values"] for s in b["per_sweep"]],
                              [s["frames"] for s in b["per_sweep"]],
                              args.get("target_relative_noise"))
    if conv.get("additional_sweeps") is not None and cleaned:
        prof = run.get("seconds_per_profile") or None
        if prof:
            conv["additional_time_min"] = round(conv["additional_sweeps"] * prof / 60.0, 1)
    imgs = _img(plots.plot_convergence(conv, title=run["run_id"])) if args.get("plot", True) else []
    return _as_json({**_selection_echo(run, cleaned, signal, norm, args), **conv}), imgs


@_tool
def t_assess_energy_calibration(args):
    run, cleaned = _clean_selected(args)
    signal, norm = _signal_args(args)
    b = _bin(cleaned, args, signal, norm)
    ref = args.get("reference_e0_ev")
    edge_info = None
    if ref is None and args.get("element"):
        from beamtimehero_cli.science.tables.edges import get_edge_info
        edge_info = get_edge_info(args["element"], args.get("edge", "K"))
        ref = edge_info["tabulated_energy_ev"]
    res = analysis.energy_calibration(b["energy"], b["merged"], ref,
                                      crystal=_crystal(args), station=_station())
    res["edge_info"] = edge_info
    return _as_json({**_selection_echo(run, cleaned, signal, norm, args), **res}), []


@_tool
def t_check_gap_tracking(args):
    _needs_undulator("cherfd_check_gap_tracking")
    run, cleaned = _clean_selected(args)
    per = []
    for c in cleaned:
        a = c["arrays"]
        if "gap" not in a:
            per.append({"sweep": c["sweep"].label, "ok": False, "error": "no gap column"})
            continue
        r = frames.gap_tracking(a["absev"], a["gap"],
                                lambda e: beamline.harmonic_gap(e)["ideal_gap_mm"] or np.nan)
        per.append({"sweep": c["sweep"].label, **r})
    e_all = np.concatenate([c["arrays"]["absev"] for c in cleaned])
    return _as_json({"run_id": run["run_id"], "sweeps": per,
                     "harmonic_changes_in_range": beamline.harmonic_changes(
                         float(np.nanmin(e_all)), float(np.nanmax(e_all))),
                     "note": "implied_calibration_offset_mm should match the server's "
                             "gap_calibration_offset; residual_rms_mm is the tracking lag/jitter"}), []


def _ev_window(args, key) -> tuple[float, float] | None:
    w = args.get(key)
    if w is None:
        return None
    if not isinstance(w, (list, tuple)) or len(w) != 2:
        raise ValueError(f"{key} must be [lo_eV, hi_eV]")
    return float(w[0]), float(w[1])


@_tool
def t_channel_stats(args):
    run, cleaned = _clean_selected(args)
    cleaned.sort(key=lambda c: c["sweep"].time)
    pre, post = _ev_window(args, "pre_edge_ev"), _ev_window(args, "post_edge_ev")
    if (pre is None) != (post is None):
        raise ValueError("give both pre_edge_ev and post_edge_ev for an edge jump, or neither")
    signal = args.get("signal") or _station().default_signal
    chans = args.get("channels") or chan_sci.default_channels(cleaned[0]["arrays"], signal)
    window = (args.get("e_min"), args.get("e_max"))
    per = []
    for c in cleaned:
        s = chan_sci.sweep_channel_stats(c["arrays"], channels=chans, ratios=args.get("ratios"),
                                         window=window, pre=pre, post=post,
                                         period_us=c["qc"]["period_us"])
        per.append({"sweep": c["sweep"].label, "direction": c["sweep"].direction,
                    "time": c["sweep"].timestamp, **s})
    summary = {"all": chan_sci.run_channel_summary(per)}
    dirs = sorted({p["direction"] for p in per})
    if len(dirs) > 1:
        summary["by_direction"] = {d: chan_sci.run_channel_summary([p for p in per if p["direction"] == d])
                                   for d in dirs}
    notes = sorted({n for c in cleaned for n in c["notes"]})
    notes.append("window means are frame-weighted: where a channel changes with energy, a different "
                 "frame density (fwd vs rev, speed regions) moves them; compare in flat windows or "
                 "use the pre/post edge means")
    return _as_json({"run_id": run["run_id"], "sweeps_used": [p["sweep"] for p in per],
                     "channels": chans, "window_ev": list(window),
                     "pre_edge_ev": pre, "post_edge_ev": post,
                     "run": summary, "sweeps": per, "notes": notes}), []

@_tool
def t_export_merged(args):
    run, cleaned = _clean_selected(args)
    signal, norm = _signal_args(args)
    b = _bin(cleaned, args, signal, norm)
    fmt = args.get("format", "twocol")
    name = args.get("output_name") or re.sub(r"[^A-Za-z0-9_.-]+", "_", run["run_id"].replace("@", "_"))
    echo = _selection_echo(run, cleaned, signal, norm, args)
    written = export.write(b, fmt=fmt, name=name, provenance=echo, out_dir=config.export_dir())
    return _as_json({"ok": True, **echo, **written,
                     "next": "pass the returned file name to the spec-file XAS tools "
                             "(e.g. extract_xas_descriptors); they search CHERFD_EXPORT_DIR "
                             "after BL_SCAN_DIR, so no copy is needed"}), []


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

@_tool
def t_plot_sweeps(args):
    run, cleaned = _clean_selected(args)
    signal, norm = _signal_args(args)
    b = _bin(cleaned, args, signal, norm)
    fig = plots.plot_sweeps(b, title=run["run_id"], offset=float(args.get("offset", 0.0)))
    return _as_json({**_selection_echo(run, cleaned, signal, norm, args),
                     "n_sweeps": b["n_sweeps"], "bin_ev": b["bin_ev"]}), _img(fig)


@_tool
def t_plot_sweep_diagnostics(args):
    run = _run(args)
    sel = files.select_sweeps(run, [int(args["sweep"])] if args.get("sweep") is not None else None,
                              args.get("direction", "fwd"))
    c = sweep_data.load_clean(sel[0], align=args.get("align", "trigger"))
    signal, _ = _signal_args(args)
    a = c["arrays"]
    ideal = None
    if "gap" in a and _station().undulator:
        ideal = np.array([beamline.harmonic_gap(e)["ideal_gap_mm"] or np.nan for e in a["absev"]])
        r = frames.gap_tracking(a["absev"], a["gap"],
                                lambda e: beamline.harmonic_gap(e)["ideal_gap_mm"] or np.nan)
        if r.get("ok"):
            ideal = ideal + r["implied_calibration_offset_mm"]
    fig = plots.plot_diagnostics(a, signal, title=f"{run['run_id']} sweep {sel[0].label}", ideal_gap=ideal)
    return _as_json({"run_id": run["run_id"], "sweep": sel[0].label, "qc": c["qc"]}), _img(fig)


# ---------------------------------------------------------------------------
# Logs (cherfd_claude/logs)
# ---------------------------------------------------------------------------

def _logs_root() -> Path:
    d = config.logs_dir()
    if d is None or not d.is_dir():
        raise ValueError("cherfd logs directory not configured or missing: set CHERFD_LOGS_DIR "
                         "(or CHERFD_PROJECT_DIR)")
    return d


def _log_files(component: str | None = None) -> list[Path]:
    fs = sorted(_logs_root().glob("*.log"), key=lambda p: p.stat().st_mtime)
    if component:
        fs = [f for f in fs if f.name.startswith(component + "_")]
    return fs


@_tool
def t_list_logs(args):
    fs = _log_files(args.get("component"))
    limit = int(args.get("limit", 30))
    comps = sorted({f.name.rsplit("_", 2)[0] for f in fs})
    return _as_json({"logs_dir": str(_logs_root()), "components": comps,
                     "files": [{"name": f.name, "bytes": f.stat().st_size,
                                "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(f.stat().st_mtime))}
                               for f in reversed(fs[-limit:])]}), []


@_tool
def t_read_log(args):
    root = _logs_root()
    if args.get("name"):
        p = (root / args["name"]).resolve()
        if root.resolve() not in p.parents or not p.is_file():
            raise ValueError(f"log {args['name']!r} not found in {root}")
    else:
        fs = _log_files(args.get("component"))
        if not fs:
            raise ValueError("no matching log files")
        p = fs[-1]
    n = min(int(args.get("lines", 100)), 2000)
    lines = p.read_text(errors="replace").splitlines()
    return _as_json({"file": p.name, "total_lines": len(lines), "lines": lines[-n:]}), []


@_tool
def t_search_logs(args):
    try:
        rx = re.compile(args["pattern"], re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"bad regex: {e}") from None
    fs = _log_files(args.get("component"))[-int(args.get("max_files", 10)):]
    limit = int(args.get("limit", 100))
    hits = []
    for f in reversed(fs):
        for i, line in enumerate(f.read_text(errors="replace").splitlines(), start=1):
            if rx.search(line):
                hits.append({"file": f.name, "line": i, "text": line[:400]})
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            break
    return _as_json({"pattern": args["pattern"], "files_searched": [f.name for f in fs],
                     "matches": hits, "truncated": len(hits) >= limit}), []


# ---------------------------------------------------------------------------
# Registry: tool name -> handler
# ---------------------------------------------------------------------------

HANDLERS: dict[str, callable] = {
    # control reads
    "cherfd_get_status": t_get_status,
    "cherfd_get_station": t_get_station,
    "cherfd_check_command": t_check_command,
    "cherfd_get_server_settings": t_get_server_settings,
    "cherfd_get_undulator_status": t_get_undulator_status,
    "cherfd_get_table_tracking": t_get_table_tracking,
    "cherfd_get_scan_results": t_get_scan_results,
    "cherfd_wait_for_scan": t_wait_for_scan,
    "cherfd_get_daq_status": t_get_daq_status,
    "cherfd_check_readiness": t_check_readiness,
    # control actions
    "cherfd_start_scan": t_start_scan,
    "cherfd_stop_scan": t_stop_scan,
    "cherfd_clear_stop": t_clear_stop,
    "cherfd_reset_controller": t_reset_controller,
    "cherfd_set_data_path": t_set_data_path,
    "cherfd_set_detector": t_set_detector,
    "cherfd_set_undulator_tracking": t_set_undulator_tracking,
    "cherfd_set_gap_calibration_offset": t_set_gap_calibration_offset,
    "cherfd_set_gap_move_latency": t_set_gap_move_latency,
    "cherfd_set_table_tracking": t_set_table_tracking,
    "cherfd_set_table_anchor": t_set_table_anchor,
    "cherfd_move_energy": t_move_energy,
    "cherfd_move_gap": t_move_gap,
    "cherfd_stop_gap": t_stop_gap,
    "cherfd_calibrate_encoder": t_calibrate_encoder,
    "cherfd_daq_count": t_daq_count,
    "cherfd_daq_abort": t_daq_abort,
    # planning
    "cherfd_validate_command": t_validate_command,
    "cherfd_build_command": t_build_command,
    "cherfd_build_exafs_command": t_build_exafs_command,
    "cherfd_simulate_scan": t_simulate_scan,
    "cherfd_estimate_acquisition_time": t_estimate_acquisition_time,
    "cherfd_undulator_gap_for_energy": t_undulator_gap_for_energy,
    "cherfd_mono_convert": t_mono_convert,
    # data
    "cherfd_list_data_dirs": t_list_data_dirs,
    "cherfd_list_runs": t_list_runs,
    "cherfd_get_latest_run": t_get_latest_run,
    "cherfd_get_sweep_metadata": t_get_sweep_metadata,
    "cherfd_read_sweep": t_read_sweep,
    # analysis
    "cherfd_assess_run_quality": t_assess_run_quality,
    "cherfd_merge_sweeps": t_merge_sweeps,
    "cherfd_compare_directions": t_compare_directions,
    "cherfd_analyze_sweep_drift": t_analyze_sweep_drift,
    "cherfd_analyze_convergence": t_analyze_convergence,
    "cherfd_assess_energy_calibration": t_assess_energy_calibration,
    "cherfd_check_gap_tracking": t_check_gap_tracking,
    "cherfd_channel_stats": t_channel_stats,
    "cherfd_export_merged": t_export_merged,
    # plots
    "cherfd_plot_sweeps": t_plot_sweeps,
    "cherfd_plot_sweep_diagnostics": t_plot_sweep_diagnostics,
    # logs
    "cherfd_list_logs": t_list_logs,
    "cherfd_read_log": t_read_log,
    "cherfd_search_logs": t_search_logs,
}
