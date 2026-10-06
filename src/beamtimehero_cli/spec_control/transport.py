"""Shared primitives for SPEC dispatch — independent of transport.

Owns the things every transport (and the spec_cmd router) needs:
  * `DispatchResult` — uniform return type for any dispatch call.
  * `_MockScreen`   — in-memory simulator used when `SPEC_MOCK=1`.
  * busy-state      — `reserve` / `release` / `get_state`, so spec_cmd can
                      serialize SPEC calls regardless of which transport
                      is active.

This module deliberately does NOT import `screen_client` or `tcp_client`.
The transport router lives in `spec_cmd.py` — see `spec_cmd.dispatch`.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Uniform dispatch result
# ---------------------------------------------------------------------------

@dataclass
class DispatchResult:
    """Outcome of a single injected SPEC command."""
    ok: bool
    output: str
    prompt_seen: bool
    elapsed_s: float
    error: Optional[str] = None
    # --- enriched (transport-dependent; None when unavailable) ---
    reply: Optional[str] = None
    reply_err: Optional[int] = None
    exit_code: Optional[int] = None
    timed_out: Optional[bool] = None
    output_complete: Optional[bool] = None
    run_id: Optional[str] = None
    log: Optional[str] = None
    transport: Optional[str] = None


# ---------------------------------------------------------------------------
# Busy-state machine
# ---------------------------------------------------------------------------

STATE_IDLE = "idle"
STATE_BUSY = "busy"
STATE_ERRORED = "errored"


@dataclass
class _SpecState:
    state: str = STATE_IDLE
    last_cmd: Optional[str] = None
    last_action_id: Optional[str] = None
    started_at: Optional[float] = None
    last_output: Optional[str] = None
    lock: threading.RLock = field(default_factory=threading.RLock)


_state = _SpecState()


def get_state() -> dict:
    with _state.lock:
        return {
            "state": _state.state,
            "command": _state.last_cmd,
            "action_id": _state.last_action_id,
            "started_at": _state.started_at,
            "elapsed_s": (time.time() - _state.started_at) if _state.started_at else None,
            "last_output": _state.last_output,
        }


def reserve(action_id: str, command: str) -> bool:
    """Try to mark SPEC busy; return False if already busy."""
    with _state.lock:
        if _state.state == STATE_BUSY:
            return False
        _state.state = STATE_BUSY
        _state.last_cmd = command
        _state.last_action_id = action_id
        _state.started_at = time.time()
        _state.last_output = None
        return True


def release(output: str | None, errored: bool) -> None:
    with _state.lock:
        _state.state = STATE_ERRORED if errored else STATE_IDLE
        _state.last_output = output


# ---------------------------------------------------------------------------
# In-memory simulator (SPEC_MOCK=1)
# ---------------------------------------------------------------------------

def _sim_engine():
    """Return the simulation engine module if active, else None."""
    try:
        from simulation import engine as eng  # type: ignore
    except Exception:
        return None
    return eng if eng.is_active() else None


def _mock_default_positions() -> dict:
    """Mock motor set — mirrors BL15-2 by default.

    Another station's app may replace it wholesale via SPEC_MOCK_MOTORS
    (a JSON object of motor -> position); the reported cwd is likewise
    overridable via SPEC_MOCK_PWD. Both default to the historical BL15-2
    values, so existing consumers see no change.
    """
    positions = {
        "m1vert": 1.93, "m1pitch": 0.0, "m2vert": 0.0, "m2horz": 0.0,
        "pitcha": 0.0, "pitchb": 0.0, "energy": 7100.0, "emiss": 6400.0,
        "Sx": 0.0, "Sy": 0.0, "Sz": 10.0, "Sr": 0.0, "filter": 0,
        "mono": 7100.0, "gap": 38.0, "crystal": 0,
        "Az": 0.0, "Dz": 0.0, "Bx": 0.0, "Bz": 0.0, "Tz": 0.0, "Tp": 0.0,
    }
    raw = os.environ.get("SPEC_MOCK_MOTORS", "").strip()
    if raw:
        try:
            override = json.loads(raw)
            if isinstance(override, dict) and override:
                positions = {str(k): float(v) for k, v in override.items()}
        except (ValueError, TypeError):
            pass  # malformed override -> keep defaults
    return positions


def _mock_counters() -> dict:
    """Station counter set for the mock `ct`, from SPEC_MOCK_COUNTERS.

    A JSON object of counter -> value. Unset (the default) keeps the
    historical BL15-2 reply (I0, I1, vortDT, I2).
    """
    raw = os.environ.get("SPEC_MOCK_COUNTERS", "").strip()
    if not raw:
        return {}
    try:
        override = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(override, dict):
        return {}
    out = {}
    for k, v in override.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


class _MockScreen:
    """In-memory stand-in that synthesizes believable SPEC output.

    Not a full SPEC model — just enough to exercise the dispatcher,
    action_log, orchestrator, and UI without a live beamline. When the
    `simulation` package has been bootstrapped, scan-producing commands
    are routed through `simulation.engine` so a real (mock) SPEC file
    appears on disk and `get_latest_scan` etc. surface the new data.
    """

    _scan_counter = itertools.count(1)
    _positions = _mock_default_positions()
    _pwd = os.environ.get("SPEC_MOCK_PWD", "/data/fifteen/mock")
    _scan_n = 1000
    _filename = "mock.01"
    _logfile = "mock.log"

    @classmethod
    def _filename_active(cls) -> str:
        eng = _sim_engine()
        return eng.current_file() if eng else cls._filename

    @classmethod
    def _set_filename(cls, name: str) -> None:
        cls._filename = name
        eng = _sim_engine()
        if eng:
            eng.set_current_file(name)

    _JUSTIFICATION_PRINT_RE = re.compile(r'^print\s+"(?:[^"\\]|\\.)*"\s*;\s*')

    @classmethod
    def inject(cls, cmd: str) -> str:
        cmd = cmd.strip()
        m = cls._JUSTIFICATION_PRINT_RE.match(cmd)
        if m:
            cmd = cmd[m.end():].strip()
        low = cmd.lower()
        if low == "wa":
            parts = ["Current motor positions:"]
            for m, v in cls._positions.items():
                parts.append(f"  {m:>10s} = {v:.4f}")
            return "\n".join(parts)
        if low == "fon":
            return f"data = {cls._filename} / log = {cls._logfile}"
        if low == "pwd":
            return cls._pwd
        if low.startswith("p scan_n"):
            return str(cls._scan_n)
        if low.startswith("show_elements"):
            return (
                "\n  Configured elements (1):\n"
                "    1. Au  (incident=14353 eV, emission=11610 eV)  << CURRENT\n"
            )
        if low.startswith("p element"):
            return "Au"
        if low.startswith("wbeamsize"):
            return "Beam size (X, Z): 0.35, 0.12 mm\nBeam mode (X, Z): small, small"
        if low.startswith("p beam_status"):
            from beamtimehero_cli.spec_control import spec_cmd
            if not spec_cmd.beam_status_requires_gap():
                # No-gap station (BL4-1): the macro prints two fields.
                return '{"spear_current": 485.20, "bl_state": "OPEN"}'
            return "{'spear_current': 485.2, 'bl_state': 'OPEN', 'gap_owned': 1}"
        if low.startswith("p cnt_mne("):
            return "I1"
        if low.startswith("wm "):
            motor = cmd.split()[1]
            val = cls._positions.get(motor, 0.0)
            return (
                f"\n            {motor}\n            {motor}\n"
                f"User\n High    99999.000\n Current {val}\n Low     -99999.000\n"
                f"Dial\n High    99999.000\n Current {val}\n Low     -99999.000"
            )
        if low.startswith("p a["):
            motor = cmd.split("[")[1].split("]")[0]
            return f"{cls._positions.get(motor, 0.0)}"
        if low.startswith("p "):
            rest = cmd[2:].strip()
            return f"{cls._positions.get(rest, 0.0)}"
        if low.startswith("p s"):
            return "[1.2e5, 8.9e4, 3.7e3, 2.1e2]  # I0, I1, vortDT, I2"
        if low.startswith("ct "):
            counters = _mock_counters()
            if counters:
                return "  ".join(f"{k}={v:g}" for k, v in counters.items())
            return "I0=1.2e5  I1=8.9e4  vortDT=3.7e3  I2=2.1e2"
        if low.startswith(("umv ", "mv ", "umvr ")):
            tokens = cmd.split()
            if len(tokens) >= 3:
                motor = tokens[1]
                try:
                    pos = float(tokens[2])
                    cls._positions[motor] = pos
                except ValueError:
                    pass
            return "Move complete."
        if low.startswith("gscan "):
            tokens = cmd.split()
            try:
                motor = tokens[1]
                nums = [float(t) for t in tokens[2:]]
                start, regions, ct = nums[0], nums[1:-1], nums[-1]
                end = regions[-2] if len(regions) >= 2 else start
            except (IndexError, ValueError):
                cls._scan_n += 1
                return f"Scan #{cls._scan_n} complete. File={cls._filename}"
            cls._positions[motor] = end
            eng = _sim_engine()
            if eng:
                append_gscan = getattr(eng, "append_gscan", None)
                if append_gscan is not None:
                    meta = append_gscan(motor, start, regions, ct,
                                        positions=dict(cls._positions))
                else:
                    # Engine without a dedicated gscan generator: fake it
                    # as a uniform scan over the full span.
                    npts = max(int(abs(end - start)), 10)
                    meta = eng.append_ascan(motor, start, end, npts, ct,
                                            positions=dict(cls._positions))
                cls._scan_n = meta["scan_number"]
                return (f"Scan #{meta['scan_number']} complete. "
                        f"File={meta['file_name']}  motor={motor}")
            cls._scan_n += 1
            return f"Scan #{cls._scan_n} complete. File={cls._filename}  motor={motor}"
        if low.startswith("kscan "):
            tokens = cmd.split()
            try:
                nums = [float(t) for t in tokens[1:]]
                start = nums[0]
                e0, _k1, _kstep, k2 = nums[-7], nums[-6], nums[-5], nums[-4]
                end_ev = e0 + k2 * k2 / 0.2625
            except (IndexError, ValueError):
                cls._scan_n += 1
                return f"Scan #{cls._scan_n} complete. File={cls._filename}"
            cls._positions["mono"] = end_ev
            eng = _sim_engine()
            if eng:
                append_kscan = getattr(eng, "append_kscan", None)
                if append_kscan is not None:
                    meta = append_kscan(nums, positions=dict(cls._positions))
                else:
                    npts = max(int(abs(end_ev - start)), 10)
                    meta = eng.append_ascan("mono", start, end_ev, npts,
                                            nums[2], positions=dict(cls._positions))
                cls._scan_n = meta["scan_number"]
                return (f"Scan #{meta['scan_number']} complete. "
                        f"File={meta['file_name']}  motor=mono")
            cls._scan_n += 1
            return f"Scan #{cls._scan_n} complete. File={cls._filename}  motor=mono"
        if low.startswith("absenergy "):
            tokens = cmd.split()
            try:
                target = float(tokens[1])
            except (IndexError, ValueError):
                return "absenergy: bad target"
            cls._positions["mono"] = target
            return (f"absenergy complete. absev={target:.2f} "
                    f"error=0.01 (successful_absenergy=1)")
        if low.startswith(("ascan ", "dscan ")):
            tokens = cmd.split()
            try:
                motor = tokens[1]
                lo = float(tokens[2]); hi = float(tokens[3])
                npts = int(tokens[4]); ct = float(tokens[5])
                if low.startswith("dscan "):
                    cur = cls._positions.get(motor, 0.0)
                    lo, hi = cur + lo, cur + hi
            except (IndexError, ValueError):
                cls._scan_n += 1
                return f"Scan #{cls._scan_n} complete. File={cls._filename}"
            eng = _sim_engine()
            if eng:
                meta = eng.append_ascan(motor, lo, hi, npts, ct,
                                        positions=dict(cls._positions))
                cls._scan_n = meta["scan_number"]
                return (f"Scan #{meta['scan_number']} complete. "
                        f"File={meta['file_name']}  motor={motor}")
            cls._scan_n += 1
            return f"Scan #{cls._scan_n} complete. File={cls._filename}"
        if low.startswith("d2scan "):
            tokens = cmd.split()
            try:
                m1 = tokens[1]
                _d_lo1 = float(tokens[2]); d_hi1 = float(tokens[3])
                m2 = tokens[4]
                _d_lo2 = float(tokens[5]); d_hi2 = float(tokens[6])
                npts = int(tokens[7]); ct = float(tokens[8])
                # d2scan leaves both motors at their end-of-scan position
                cls._positions[m1] = cls._positions.get(m1, 0.0) + d_hi1
                cls._positions[m2] = cls._positions.get(m2, 0.0) + d_hi2
            except (IndexError, ValueError):
                cls._scan_n += 1
                return f"Scan #{cls._scan_n} complete. File={cls._filename}"
            cls._scan_n += 1
            return (f"Scan #{cls._scan_n} complete. File={cls._filename}  "
                    f"motors={m1},{m2} npts={npts} ct={ct}")
        if low.startswith("get_herfd_energy"):
            # SPEC macro prints the fit's suggested emission energy. Pin
            # to a value the parser can pull out so dispatcher tests get
            # a structured result back.
            return ("get_HERFD_energy: fitted Pseudo-Voigt + skew on the "
                    "current scan.\n"
                    "Suggested new emission value is 6404.20")
        if low.startswith("cen") or low.startswith("peak"):
            return "Moved scanned motor to feature."
        if low.startswith("align_the_beamline"):
            time.sleep(0.2)  # simulate long-running macro
            cls._positions["m1vert"] = 1.93
            cls._positions["m2horz"] = 0.12
            return (
                "align_the_beamline complete.\n"
                "final_energy_ev=7100 beam_size_h=0.35 beam_size_v=0.12 anchor=saved"
            )
        if low.startswith("run_spec_align") or low.startswith("xes_align"):
            return "run_spec_align complete. XES_EN_OFFSET=-0.42"
        if low.startswith("auto_sample_align"):
            return "auto_sample_align complete. samples_found=6"
        if low.startswith("run_collection"):
            return "run_collection complete. samples_completed=6 files=6"
        if low.startswith("select_element"):
            return "select_element complete."
        if low.startswith("calibrate_mono"):
            return "calibrate_mono complete. offset=-0.11"
        if low.startswith("peak_mono_pitch"):
            return "peak_mono_pitch complete. gain=1.18"
        if low.startswith("gaprequest"):
            return "gap granted."
        if low.startswith("newfile"):
            tokens = cmd.split()
            if len(tokens) >= 2:
                cls._set_filename(tokens[1])
            return f"new file: {cls._filename}"
        if low.startswith(("fson", "fsoff", "fsopen", "fsclose")):
            return f"shutter: {low}"
        if low.startswith(("set_i0_gain", "set_i1_gain", "set_i2_gain")):
            return "gain set."
        if low.startswith("vortex_roi"):
            return "ROI set."
        if low.startswith("safely_remove_filters"):
            cls._positions["filter"] = 0
            return "filters removed."
        if low.startswith("mvpinhole"):
            cls._positions["Sx"] = 0.0
            cls._positions["Sy"] = 0.0
            cls._positions["Sz"] = 0.0
            cls._positions["Sr"] = 0.0
            return "mvpinhole complete. Sx=0 Sy=0 Sz=0 Sr=0"
        if low.startswith("mvplastic"):
            cls._positions["Sx"] = 0.0
            cls._positions["Sy"] = 0.0
            cls._positions["Sz"] = -7.0
            cls._positions["Sr"] = -45.0
            return "mvplastic complete. Sx=0 Sy=0 Sz=-7 Sr=-45"
        if low.startswith("mvknifeclear"):
            cls._positions["Sx"] = 0.0
            cls._positions["Sy"] = 0.0
            cls._positions["Sz"] = 4.3
            cls._positions["Sr"] = 0.0
            return "mvknifeclear complete. Both slits are out of beam."
        if low.startswith("mvknifewayout"):
            cls._positions["Sx"] = 15.0
            cls._positions["Sy"] = 0.0
            cls._positions["Sz"] = 0.0
            cls._positions["Sr"] = 80.0
            return "mvknifewayout complete. Sx=15 Sy=0 Sz=0 Sr=80"
        if low.startswith("measure_beam_size"):
            time.sleep(0.1)
            return "measure_beam_size complete. beamsize_x=0.35 beamsize_z=0.12 mm"
        if low.startswith("zero_pinhole"):
            time.sleep(0.1)
            return "zero_pinhole complete. pinhole_offset x=0 y=0 z=0"
        if low.startswith("smallbeam"):
            return "Beam mode set to: small (both)"
        if low.startswith("bigbeam"):
            return "Beam mode set to: big (both)"
        if low.startswith("xtalalign"):
            time.sleep(0.05)
            return "xtalalign complete. crystal encoder restored to original position."
        if low.startswith("reset_gap"):
            time.sleep(0.05)
            return "reset_gap complete. gap encoder restored to original position."
        if low.startswith("m2_stripe"):
            # Parse the eV argument and mirror the macro's branch logic.
            ev = None
            try:
                inside = cmd.split("(", 1)[1].rsplit(")", 1)[0]
                ev = float(inside.strip())
            except Exception:
                ev = None
            if ev is None or ev >= 6200:
                cls._positions["m2vert"] = -3.5
                stripe = "Rh"
            elif ev < 4500:
                cls._positions["m2vert"] = -3.5
                stripe = "Rh"  # macro defaults to Rh on invalid low input
            else:
                cls._positions["m2vert"] = 9.69
                stripe = "Si"
            return f"m2_stripe complete. stripe={stripe} m2vert={cls._positions['m2vert']}"
        if low.startswith("get_anchor"):
            return (
                "\nAnchor positions: \n"
                "energy: 7100\n"
                "(m1vert: 1.93)\n"
                "m1vert1: 1.83\n"
                "m1vert2: 2.03\n"
                "(Tz: 0.0)\n"
                "Tz1: 0.0\n"
                "Tz2: 0.0\n"
                "crystal: A\n"
                "SPEAR steering: -0.05"
            )
        if low.startswith("set_anchor"):
            return (
                "Storing the current positions of energy, Tz, m1vert to track the beam\n"
                "Anchor positions:\n"
                "  energy: 7100.0\n"
                "  m1vert: 1.93\n"
                "  Tz: 0.0\n"
                "anchor saved to /usr/local/lib/spec.d/anchor.cfg"
            )
        if low.startswith("tracking"):
            tokens = cmd.split()
            arg = tokens[1] if len(tokens) >= 2 else ""
            return f"Set tracking to {arg}"
        if low.startswith(("vvv", "hhh", "m1m1", "m2m2", "ggg", "bzbz", "bxbx",
                           "dmm", "beamx", "beamz", "cm1m1", "cm2m2")):
            alias = low.split()[0]
            eng = _sim_engine()
            if eng:
                meta = eng.append_alias_scan(alias, positions=dict(cls._positions))
                cls._scan_n = meta["scan_number"]
                return (f"{alias} scan complete. scan_n={meta['scan_number']} "
                        f"file={meta['file_name']}")
            cls._scan_n += 1
            return f"{low} scan complete. scan_n={cls._scan_n}"
        if "_xas " in low or low.endswith("_xas"):
            tokens = cmd.split()
            elem = tokens[0].split("_xas")[0]
            try:
                ct = float(tokens[1]) if len(tokens) > 1 else 0.5
                reps = int(tokens[2]) if len(tokens) > 2 else 1
            except ValueError:
                ct, reps = 0.5, 1
            eng = _sim_engine()
            if eng:
                last = None
                for _ in range(max(reps, 1)):
                    last = eng.append_xas_scan(elem, count_time=ct,
                                               positions=dict(cls._positions))
                    cls._scan_n = last["scan_number"]
                return (f"{elem}_xas complete. reps={reps} "
                        f"last_scan={last['scan_number']} file={last['file_name']}")
            cls._scan_n += reps
            return f"{elem}_xas complete. reps={reps} scan_n={cls._scan_n}"
        if "_cee " in low or low.endswith("_cee"):
            tokens = cmd.split()
            elem = tokens[0].split("_cee")[0]
            try:
                ct = float(tokens[1]) if len(tokens) > 1 else 0.5
                reps = int(tokens[2]) if len(tokens) > 2 else 1
            except ValueError:
                ct, reps = 0.5, 1
            eng = _sim_engine()
            if eng:
                last = None
                for _ in range(max(reps, 1)):
                    last = eng.append_emiss_scan(elem, count_time=ct,
                                                 positions=dict(cls._positions))
                    cls._scan_n = last["scan_number"]
                return (f"{elem}_cee complete. reps={reps} "
                        f"last_scan={last['scan_number']} file={last['file_name']}")
            cls._scan_n += reps
            return f"{elem}_cee complete. reps={reps} scan_n={cls._scan_n}"
        return f"ok: {cmd}"
