"""The cherfd command string: parse, validate, reverse, build, time.

Format (cscan_box/motion.py parse_cherfd_command)::

    cherfd start_eV end1_eV speed1 [end2_eV speed2 ...] freq_Hz

(``cscan`` is accepted as the keyword too -- the BL4-1 UI's name for it.)

Validation applies every layer a command can meet on its way to hardware --
the SPEC macro's refusal rules, the controller's silent speed clamp, the
FPGA's millisecond trigger quantization -- and reports each as an *error*
(will be refused), or a *warning* (will run, but not as written). The
production simulator (``bridge/simulate.py``) remains the authority on
whether a motion profile fits the FPGA, and the live server's
``/check_command`` is the authority on the mono's soft limits; this module is
the fast, offline first pass that needs neither.
"""
from __future__ import annotations

from beamtimehero_cli.science.cherfd import beamline, policy


#: Command keywords cherfd_claude accepts (cscan_box/motion.py COMMAND_KEYWORDS).
COMMAND_KEYWORDS = ("cherfd", "cscan")


def _tokens(command: str) -> list[str]:
    parts = (command or "").split()
    if parts and parts[0].lower() in COMMAND_KEYWORDS:
        parts = parts[1:]
    return parts


def parse_command(command: str) -> dict:
    """Split a command into start, regions [(end_eV, speed)], freq. Raises ValueError."""
    parts = _tokens(command)
    try:
        values = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"non-numeric token in command {command!r}") from None
    if len(values) < 4:
        raise ValueError(
            "need at least 'cherfd start_eV end_eV speed freq_Hz' (4 numbers)"
        )
    start, freq, specs = values[0], values[-1], values[1:-1]
    if len(specs) % 2:
        raise ValueError("region specs must be (end_eV, speed) pairs")
    regions = [(specs[i], specs[i + 1]) for i in range(0, len(specs), 2)]
    final = regions[-1][0]
    return {
        "start_ev": start,
        "final_ev": final,
        "freq_hz": freq,
        "regions": [{"end_ev": e, "speed": s} for e, s in regions],
        "direction": "increasing" if final > start else "decreasing",
    }


def format_command(start_ev: float, regions: list[dict], freq_hz: float) -> str:
    """Inverse of parse_command. ``regions`` = [{"end_ev", "speed"}]."""
    def g(x: float) -> str:
        return f"{float(x):g}"
    body = " ".join(f"{g(r['end_ev'])} {g(r['speed'])}" for r in regions)
    return f"cherfd {g(start_ev)} {body} {g(freq_hz)}"


def reverse_command(command: str) -> str:
    """The same regions traversed backwards (cscan_box/motion.reverse_cherfd_command)."""
    p = parse_command(command)
    energies = [p["start_ev"]] + [r["end_ev"] for r in p["regions"]]
    speeds = [r["speed"] for r in p["regions"]]
    rev_regions = [
        {"end_ev": energies[i], "speed": speeds[i]}
        for i in range(len(speeds) - 1, -1, -1)
    ]
    return format_command(energies[-1], rev_regions, p["freq_hz"])


def validate_command(command: str, crystal: str | None = None, *,
                     station: policy.StationProfile,
                     energy_range_ev: tuple[float, float] | None = None) -> dict:
    """Every refusal and every silent modification the command will meet.

    ``energy_range_ev`` is the mono's live reach (the server's
    ``mono_energy_range_eV``, from MOTOR1's soft limits); without it the
    station's offline window applies, and a station with none (BL4-1) is
    bounded only by the Bragg cutoff here.
    """
    errors: list[str] = []
    warnings: list[str] = []
    try:
        p = parse_command(command)
    except ValueError as e:
        return {"ok": False, "command": command, "errors": [str(e)], "warnings": []}

    energies = [p["start_ev"]] + [r["end_ev"] for r in p["regions"]]
    if energy_range_ev is not None:
        lo, hi = sorted(energy_range_ev)
        window = "the mono's soft limits"
    else:
        lo, hi = station.energy_min_ev, station.energy_max_ev
        window = f"the {station.label} window"
    if lo is not None and hi is not None:
        for e in energies:
            if not lo <= e <= hi:
                errors.append(f"energy {e:g} eV outside {lo:g}-{hi:g} eV ({window})")
    else:
        warnings.append(f"no offline energy window at {station.label}: the mono's soft limits "
                        "are checked live (cherfd_check_command)")
    diffs = [b - a for a, b in zip(energies, energies[1:])]
    if any(d == 0 for d in diffs):
        errors.append("zero-length region (two consecutive energies are equal)")
    elif not (all(d > 0 for d in diffs) or all(d < 0 for d in diffs)):
        errors.append("energies must be monotonic (all increasing or all decreasing): a "
                      "profile has one direction; scan back with reverse")

    effective_regions = []
    for i, r in enumerate(p["regions"], start=1):
        s = r["speed"]
        if s < policy.SPEED_FACTOR_HARD_MIN or s > policy.SPEED_FACTOR_MAX:
            errors.append(
                f"region {i}: speed {s:g} outside {policy.SPEED_FACTOR_HARD_MIN:g}-"
                f"{policy.SPEED_FACTOR_MAX:g} (refused by SPEC validate_cherfd_command)"
            )
        used = min(max(s, policy.SPEED_FACTOR_MIN), policy.SPEED_FACTOR_MAX)
        if used != s:
            warnings.append(
                f"region {i}: speed {s:g} will be clamped to {used:g} by the controller"
            )
        effective_regions.append({"end_ev": r["end_ev"], "speed": used})

    freq = p["freq_hz"]
    if freq <= 0 or int(freq) != freq:
        errors.append(f"frequency must be a positive integer (SPEC rule), got {freq:g}")
    timing = beamline.trigger_timing(freq) if freq > 0 else None
    if timing:
        warnings.extend(timing["warnings"])

    regions_out = []
    if not errors:
        e0 = p["start_ev"]
        for i, r in enumerate(effective_regions, start=1):
            try:
                steps = abs(beamline.motor_steps(e0, r["end_ev"], crystal, station=station))
                t = beamline.region_cruise_time_s(e0, r["end_ev"], r["speed"], crystal,
                                                  station=station)
            except ValueError as ex:
                errors.append(f"region {i}: {ex}")
                break
            span = abs(r["end_ev"] - e0)
            real = timing["real_freq_hz"] if timing else freq
            regions_out.append({
                "index": i, "start_ev": e0, "end_ev": r["end_ev"], "speed": r["speed"],
                "steps": steps, "cruise_time_s": round(t, 3),
                "ev_per_s": round(span / t, 3) if t else None,
                "points_per_ev": round(real * t / span, 2) if span else None,
            })
            if steps < policy.MIN_REGION_STEPS_WARN:
                warnings.append(
                    f"region {i} is only {steps} steps; the profile generator may refuse "
                    "it (too short to decelerate) -- run cherfd_simulate_scan to confirm"
                )
            e0 = r["end_ev"]

    effective = (
        format_command(p["start_ev"], effective_regions, freq) if not errors else None
    )
    return {
        "ok": not errors,
        "command": command,
        "effective_command": effective,
        "reverse_command": reverse_command(command) if not errors else None,
        "parsed": p,
        "station": station.name,
        "crystal": (crystal or station.default_crystal).upper(),
        "trigger_timing": timing,
        "regions": regions_out,
        "estimated_motion_s": round(sum(r["cruise_time_s"] for r in regions_out), 3)
        if regions_out else None,
        "harmonic_changes": beamline.harmonic_changes(energies[0], energies[-1])
        if not errors and station.undulator else [],
        "errors": errors,
        "warnings": warnings,
        "note": "Cruise-only estimate (no ramps). cherfd_simulate_scan runs the production "
                "profile generator and is authoritative.",
    }


def build_command(
    edge_ev: float,
    *,
    pre_edge_ev: float = policy.BUILD_PRE_EDGE_EV,
    edge_lo_ev: float = policy.BUILD_EDGE_LO_EV,
    edge_hi_ev: float = policy.BUILD_EDGE_HI_EV,
    post_edge_ev: float = policy.BUILD_POST_EDGE_EV,
    edge_points_per_ev: float = policy.BUILD_EDGE_POINTS_PER_EV,
    outer_speed: float = policy.BUILD_OUTER_SPEED,
    freq_hz: float = policy.BUILD_FREQ_HZ,
    crystal: str | None = None,
    station: policy.StationProfile,
    energy_range_ev: tuple[float, float] | None = None,
) -> dict:
    """Three-region XANES command: fast pre-edge, slow edge, fast post-edge.

    The edge speed is solved for ``edge_points_per_ev`` at the edge energy,
    then clamped into the controller's allowed range (the clamp is reported,
    because a clamped speed means the requested density is not reachable at
    this frequency).
    """
    if not (0 <= edge_lo_ev < pre_edge_ev):
        raise ValueError("need 0 <= edge_lo_ev < pre_edge_ev")
    if not (0 < edge_hi_ev < post_edge_ev):
        raise ValueError("need 0 < edge_hi_ev < post_edge_ev")
    timing = beamline.trigger_timing(freq_hz)
    raw = beamline.speed_for_points_per_ev(edge_ev, timing["real_freq_hz"],
                                           edge_points_per_ev, crystal, station=station)
    edge_speed = round(min(max(raw, policy.SPEED_FACTOR_MIN), policy.SPEED_FACTOR_MAX), 3)
    notes = []
    if edge_speed != round(raw, 3):
        achieved = timing["real_freq_hz"] / beamline.ev_per_second(edge_ev, edge_speed, crystal,
                                                                   station=station)
        notes.append(
            f"edge speed {raw:.3f} clamped to {edge_speed}; edge density will be "
            f"{achieved:.2f} pts/eV, not {edge_points_per_ev:g} -- change freq_hz to reach it"
        )
    start = round(edge_ev - pre_edge_ev, 1)
    regions = [
        {"end_ev": round(edge_ev - edge_lo_ev, 1), "speed": outer_speed},
        {"end_ev": round(edge_ev + edge_hi_ev, 1), "speed": edge_speed},
        {"end_ev": round(edge_ev + post_edge_ev, 1), "speed": outer_speed},
    ]
    if edge_lo_ev == 0:
        regions = regions[1:]
    cmd = format_command(start, regions, int(round(freq_hz)))
    return {"command": cmd, "edge_ev": edge_ev, "edge_speed": edge_speed,
            "requested_edge_points_per_ev": edge_points_per_ev, "notes": notes,
            "validation": validate_command(cmd, crystal, station=station,
                                           energy_range_ev=energy_range_ev)}


def estimate_acquisition_time(
    motion_s_per_sweep: float, sweeps: int, reverse: bool, overhead_s: float,
) -> dict:
    """Wall time for a multi-sweep acquisition.

    ``reverse=True`` runs each sweep forward then backward (two profiles per
    sweep number, as scan_multi_sweep does).
    """
    if sweeps < 1 or sweeps > policy.SWEEPS_MAX:
        raise ValueError(f"sweeps must be 1..{policy.SWEEPS_MAX}")
    profiles = sweeps * (2 if reverse else 1)
    per = motion_s_per_sweep + overhead_s
    total = profiles * per
    return {
        "profiles": profiles,
        "motion_s_per_profile": round(motion_s_per_sweep, 3),
        "overhead_s_per_profile": overhead_s,
        "seconds_per_profile": round(per, 3),
        "total_s": round(total, 1),
        "total_min": round(total / 60.0, 2),
        "duty_cycle": round(motion_s_per_sweep / per, 3) if per else None,
    }

# CITATIONS — method -> reference. ``None`` = implemented, not yet attributed.
CITATIONS = {
    "cherfd command grammar and limits": "cherfd_claude cscan_box/motion.py parse_cherfd_command; spec/cherfd.mac validate_cherfd_command",
    "three-region XANES layout": None,
}
