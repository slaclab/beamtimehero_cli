"""Mono, undulator and trigger physics for CHERFD. Pure functions.

Ports of the *pure* parts of cherfd_claude (``utils/mono.py``,
``utils/undulator.py``, ``cscan_box/triggers.py``). They are ported rather
than imported because the originals carry import-time side effects (log
files, ``set_crystal('B')``, pyepics) and module-global state (the selected
crystal, the calibration offset) that a tool must not inherit. The numbers
are identical and the source of each is cited; the parity tests in
``tests/test_cherfd_command_and_beamline.py`` pin them against values printed
by the originals.

Every mono function takes the station profile (``policy.STATION_PROFILES``)
as a required keyword: the crystals, lattice constant, encoder and step size
differ between BL15-2 and BL4-1, and a silent default would give the other
beamline's answer. The undulator functions are BL15-2's (BL4-1 has none).
"""
from __future__ import annotations

import math

from beamtimehero_cli.science.cherfd import policy

HC_EV_ANGSTROM = 12398.4244          # utils/mono.py _hc_
# utils/mono.py's own (truncated) angle constants. Used instead of
# math.degrees/radians so energies and int() step counts match the
# controller bit-for-bit, not just to 1e-8 deg.
_RAD_2_DEG = 57.295779524
_DEG_2_RAD = 0.017453293
#: BL15-2 undulator gap polynomial per odd harmonic, gap = a E^3 + b E^2 + c E + d
#: (utils/undulator.py get_gap).
GAP_COEFFS: dict[int, tuple[float, float, float, float]] = {
    13: (2.87553e-13, -2.15033e-8, 0.000783879, -2.108852014),
    11: (2.9022e-13, -1.75345e-8, 0.000640705, 0.034380738),
    9: (5.30864e-13, -2.6328e-8, 0.000786974, 0.00060388),
    7: (1.03125e-12, -3.9192e-8, 0.00094854, 0.30570625),
    5: (3.03704e-12, -8.32698e-8, 0.001393992, 0.088356085),
    3: (1.36667e-11, -2.209e-7, 0.002240993, 0.299072),
}

CITATIONS = {
    "bragg": "utils/mono.py xtal_from_mono / mono_from_xtal (cherfd_claude)",
    "encoder": "utils/mono.py enc_to_xtal: xtal = enc * MONO_ENCODER_RESOLUTION + offset",
    "gap": "utils/undulator.py get_gap / find_harmonic_gap (cherfd_claude)",
    "triggers": "cscan_box/triggers.py trigger_timing (cherfd_claude)",
    "table": "utils/mono.py _calc_table_track; SPEC tracking.mac",
}


Station = policy.StationProfile


def crystal_set(crystal: str | None, *, station: Station) -> str:
    """The crystal set letter, checked against the station's sets."""
    c = (crystal or station.default_crystal).strip().upper()
    if c not in station.lattice_factor:
        sets = ", ".join(f"'{k}' ({v})" for k, v in station.crystal_names.items())
        raise ValueError(f"crystal must be {sets} at {station.label}, got {crystal!r}")
    return c


def bragg_angle_deg(energy_ev: float, crystal: str | None = None, *, station: Station) -> float:
    """Bragg angle (deg) for a photon energy on crystal set A or B."""
    c = crystal_set(crystal, station=station)
    if energy_ev <= 0:
        raise ValueError("energy must be positive")
    s = HC_EV_ANGSTROM * station.lattice_factor[c] / (2.0 * energy_ev * station.mono_dspacing)
    if s >= 1.0:
        raise ValueError(f"{energy_ev} eV is below the {station.crystal_names[c]} Bragg cutoff")
    return _RAD_2_DEG * math.asin(s)


def energy_from_bragg(angle_deg: float, crystal: str | None = None, *, station: Station) -> float:
    c = crystal_set(crystal, station=station)
    s = math.sin(angle_deg * _DEG_2_RAD)
    if s <= 0:
        raise ValueError("angle must be in (0, 90) degrees")
    return HC_EV_ANGSTROM * station.lattice_factor[c] / (2.0 * station.mono_dspacing * s)


def angle_from_encoder(encoder: float, calibration_offset_deg: float, *, station: Station) -> float:
    """Encoder ticks to Bragg angle, given the stored calibration offset."""
    return encoder * station.encoder_resolution_deg + calibration_offset_deg


def encoder_calibration_offset(angle_deg: float, encoder: float, *, station: Station) -> float:
    """Offset that maps ``encoder`` onto ``angle_deg`` (utils/mono.set_mono_offset)."""
    return angle_deg - encoder * station.encoder_resolution_deg


def motor_steps(start_ev: float, end_ev: float, crystal: str | None = None, *,
                station: Station) -> int:
    """Signed motor steps between two energies (cscan_box/motion.get_num_steps)."""
    d = (bragg_angle_deg(end_ev, crystal, station=station)
         - bragg_angle_deg(start_ev, crystal, station=station))
    return int(d / station.motor_step_deg)


def ev_per_second(energy_ev: float, speed_factor: float, crystal: str | None = None, *,
                  station: Station) -> float:
    """Cruise scan rate |dE/dt| at a speed factor (eV/s).

    dE/dtheta = -E / tan(theta); the mono cruises at
    FULL_SPEED_STEPS_PER_S * speed_factor steps of the station's step size.
    """
    theta = math.radians(bragg_angle_deg(energy_ev, crystal, station=station))
    omega = math.radians(policy.FULL_SPEED_STEPS_PER_S * speed_factor * station.motor_step_deg)
    return energy_ev / math.tan(theta) * omega


def speed_for_points_per_ev(energy_ev: float, freq_hz: float, points_per_ev: float,
                            crystal: str | None = None, *, station: Station) -> float:
    """Speed factor that yields ``points_per_ev`` frames/eV at ``freq_hz`` (unclamped)."""
    if points_per_ev <= 0:
        raise ValueError("points_per_ev must be positive")
    at_full = ev_per_second(energy_ev, 1.0, crystal, station=station)
    return (freq_hz / points_per_ev) / at_full


def region_cruise_time_s(start_ev: float, end_ev: float, speed_factor: float,
                         crystal: str | None = None, *, station: Station) -> float:
    """Cruise time for one region: |delta theta| / angular velocity (no ramps)."""
    steps = abs(motor_steps(start_ev, end_ev, crystal, station=station))
    return steps / (policy.FULL_SPEED_STEPS_PER_S * speed_factor)


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

def trigger_timing(freq_hz: float, duration_s: float = 0.0) -> dict:
    """What the FPGA will actually produce for a requested trigger rate.

    The period is quantized to whole ms, so e.g. 60 Hz runs at 62.5 Hz.
    """
    requested = float(freq_hz)
    warnings: list[str] = []
    f = requested
    if f > policy.FREQ_MAX_HZ:
        warnings.append(f"freq {requested} Hz clamped to {policy.FREQ_MAX_HZ} Hz")
        f = policy.FREQ_MAX_HZ
    if f < policy.FREQ_MIN_HZ:
        warnings.append(f"freq {requested} Hz clamped to {policy.FREQ_MIN_HZ} Hz")
        f = policy.FREQ_MIN_HZ
    if f > policy.FREQ_XSPRESS_WARN_HZ:
        warnings.append(
            f"{f} Hz exceeds the {policy.FREQ_XSPRESS_WARN_HZ} Hz tested Xspress3 rate"
        )
    period_ms = int(1000.0 / f)
    real = 1000.0 / period_ms
    if abs(real - requested) > 1e-9 and not any("clamped" in w for w in warnings):
        warnings.append(f"period quantized to {period_ms} ms: runs at {real:.4g} Hz, not {requested:g} Hz")
    return {
        "requested_freq_hz": requested,
        "real_freq_hz": real,
        "period_ms": period_ms,
        "period_us": period_ms * 1000,
        "n_triggers": int(duration_s * real),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Undulator
# ---------------------------------------------------------------------------

def gap_for_harmonic(harmonic: int, energy_ev: float) -> float:
    if harmonic not in GAP_COEFFS:
        raise ValueError(f"harmonic must be one of {sorted(GAP_COEFFS)}")
    a, b, c, d = GAP_COEFFS[harmonic]
    e = float(energy_ev)
    return a * e ** 3 + b * e ** 2 + c * e + d


def harmonic_gap(energy_ev: float, calibration_offset_mm: float = 0.0) -> dict:
    """Highest odd harmonic whose gap clears GAP_MIN_MM (utils/undulator.find_harmonic_gap).

    The live controller adds its ``gap_calibration_offset`` to the polynomial
    gap before moving; pass it to get the commanded value.
    """
    for h in sorted(GAP_COEFFS, reverse=True):
        g = gap_for_harmonic(h, energy_ev)
        if g >= policy.GAP_MIN_MM:
            commanded = g + calibration_offset_mm
            return {
                "energy_ev": float(energy_ev),
                "harmonic": h,
                "ideal_gap_mm": g,
                "commanded_gap_mm": commanded,
                "within_limits": policy.GAP_MIN_MM <= commanded <= policy.GAP_MAX_MM,
            }
    return {"energy_ev": float(energy_ev), "harmonic": None, "ideal_gap_mm": None,
            "commanded_gap_mm": None, "within_limits": False,
            "error": "energy too low for the undulator at any harmonic"}


def harmonic_changes(e_start: float, e_end: float, step_ev: float = 1.0) -> list[dict]:
    """Energies inside a scan range where the tracking harmonic switches.

    A harmonic switch mid-scan is a large gap jump the gap cannot track in
    time, so a scan straddling one shows an intensity discontinuity.
    """
    lo, hi = sorted((float(e_start), float(e_end)))
    out: list[dict] = []
    prev = harmonic_gap(lo).get("harmonic")
    e = lo + step_ev
    while e <= hi + 1e-9:
        h = harmonic_gap(e).get("harmonic")
        if h != prev:
            out.append({"energy_ev": round(e, 3), "from_harmonic": prev, "to_harmonic": h})
            prev = h
        e += step_ev
    return out


# ---------------------------------------------------------------------------
# Table (beam height) tracking
# ---------------------------------------------------------------------------

def beam_height_offset_mm(energy_ev: float, anchor_ev: float, crystal: str | None = None, *,
                          station: Station) -> float:
    """Beam-height change at the sample, 2*MIN_GAP*(cos theta - cos theta_anchor)."""
    c = crystal_set(crystal, station=station)
    t = math.radians(bragg_angle_deg(energy_ev, c, station=station))
    ta = math.radians(bragg_angle_deg(anchor_ev, c, station=station))
    return 2.0 * station.mono_min_gap_mm[c] * (math.cos(t) - math.cos(ta))
