"""Scientific and operational defaults for CHERFD, in one place.

Same contract as beamtimehero_cli's ``science/*/policy.py``: every constant
here is pinned by ``tests/test_science_policy.py``, and the tool schemas read
their defaults *from* this module rather than restating a literal. Changing a
number fails the suite; update the pinned value in the same commit -- that diff
is the record of which default moved.

Where a limit mirrors cherfd_claude code, the source is cited so a change on
that side can be followed here. Where two layers of cherfd_claude disagree,
the **stricter** value is used, because an agent-issued command must pass
every layer it could be routed through (REST and SPEC).

What differs between endstations (mono crystals and step size, the scan
detector, undulator, cscan_daq, per-sweep overhead) is in ``STATION_PROFILES``
at the end; the deployment picks one with ``BTH_STATION``
(``beamtimehero_cli/station.py``) and handlers pass it in, so nothing here
reads the environment.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Command limits (what a cherfd command may ask for)
# ---------------------------------------------------------------------------

#: Speed-factor clamp every real scan applies (cherfd.py
#: CherfdController.parse_cherfd_command). A request outside it is not
#: refused by the controller -- it is silently clamped -- so validation
#: reports it as a warning with the value that will actually run.
SPEED_FACTOR_MIN = 0.2
SPEED_FACTOR_MAX = 1.0

#: SPEC's validate_cherfd_command refuses speeds outside this (spec/cherfd.mac).
SPEED_FACTOR_HARD_MIN = 0.1

#: Trigger frequency clamp (cscan_box/triggers.py trigger_timing).
FREQ_MIN_HZ = 20.0
FREQ_MAX_HZ = 500.0
#: Above this the Xspress3 is outside its tested rate (triggers.py warning;
#: both stations' detectors are read by an Xspress3).
FREQ_XSPRESS_WARN_HZ = 80.0

#: CherfdController.scan_multi_sweep accepts 1..500 sweeps.
SWEEPS_MAX = 500

#: A region shorter than this many motor steps is refused by the profile
#: generator in practice (cannot decelerate); used only as an early warning,
#: the simulator is the authority.
MIN_REGION_STEPS_WARN = 200

# ---------------------------------------------------------------------------
# Motion / mono (cscan_box/motion.py, utils/mono.py)
# ---------------------------------------------------------------------------

#: Settling time appended to every profile (multisegment_move default).
PROFILE_SETTLING_S = 0.5
# ---------------------------------------------------------------------------
# Undulator (utils/undulator.py)
# ---------------------------------------------------------------------------

GAP_MIN_MM = 7.3
GAP_MAX_MM = 22.0
#: A measured-vs-ideal gap residual scatter above this is reported.
GAP_TRACKING_WARN_MM = 0.02

# ---------------------------------------------------------------------------
# Data reduction
# ---------------------------------------------------------------------------

#: Default energy bin for rebinning a continuous sweep. A 0.4-speed Cu K
#: sweep at 100 Hz lands ~7 frames/eV, so 0.25 eV keeps ~2 frames per bin
#: per sweep; merging sweeps fills it further. The cherfd_claude viewer's
#: 0.1 eV (data_processing/data_loader.py average_sweeps) leaves most bins
#: empty on a single sweep.
DEFAULT_BIN_EV = 0.25

#: ADC words are offset binary: signed counts = (word - ADC_OFFSET) /
#: ADC_WORD_SCALE (cherfd_claude cscan_box/data_frame.py, which decodes them
#: on write since 2026-10-08; older sweeps hold the raw word). Counts, not
#: volts: ~3100 counts/V on BL4-1 ADC1, gain not yet measured.
ADC_OFFSET = 2 ** 31
ADC_WORD_SCALE = 2 ** 16
#: A decoded value is a signed 16-bit count; anything larger is a raw word.
ADC_COUNTS_MAX = 2 ** 15

#: trig_1 bit 31 marks an FPGA status-report frame, not a trigger
#: (cscan_box/references/DMA_structure_definition.txt). cherfd_claude cuts
#: the frames at the first one since 2026-10-08 (data_frame.py
#: _drop_invalid_frames); older sweeps still carry them, so they are dropped
#: here too.
STATUS_FRAME_BIT = 31

#: Detector frame k is the k-th trigger (0-based) when this is 0. The Xspress3
#: time-series frames are recorded in trigger order. Measured for the BL4-1
#: Ge detector (2026-10-10: N triggers make N + 1 DO1 gates, the extra one
#: after the time series is full, so Ge point k is box frame k); not yet
#: pinned for the BL15-2 Vortex. A one-frame error would shift fwd and rev
#: sweeps oppositely.
DETECTOR_FRAME_OFFSET = 0

#: Frames whose energy falls this far outside the commanded range are
#: treated as corrupt (e.g. a 205917 eV frame in 2025-10_Sokaras_Pt).
ENERGY_OUTLIER_MARGIN_EV = 20.0

#: Sweeps from one acquisition sit tens of seconds apart; a gap longer than
#: this starts a new run even when the file root repeats.
RUN_GAP_S = 600.0

# ---------------------------------------------------------------------------
# QC / comparison thresholds
# ---------------------------------------------------------------------------

#: Dropped-trigger fraction above which a sweep is flagged.
DROPPED_FRAME_WARN_FRACTION = 0.01
#: fwd/rev energy offset worth reporting (backlash / encoder lag), eV.
DIRECTION_OFFSET_WARN_EV = 0.1
#: Search half-width for the fwd/rev and sweep-to-reference shift, eV.
SHIFT_SEARCH_EV = 5.0
#: Gaussian sigma applied to both spectra before the shift search. Must be
#: well above the point spacing (~0.05-0.1 eV) to stop interpolation
#: pixel-locking, and well below feature widths (core-hole ~1.5 eV at Cu K).
SHIFT_SMOOTH_EV = 0.3
#: Edge drift over a run worth flagging, eV.
EDGE_DRIFT_WARN_EV = 0.2
#: Relative intensity change over a run worth flagging (damage, beam decay).
INTENSITY_DRIFT_WARN_FRACTION = 0.05
#: Derivative smoothing span used for E0 (passed to beamtimehero find_e0).
E0_SMOOTH_SPAN_EV = 2.0

# Default region layout for build_command, relative to the edge (eV).
BUILD_PRE_EDGE_EV = 30.0
BUILD_EDGE_LO_EV = 10.0
BUILD_EDGE_HI_EV = 30.0
BUILD_POST_EDGE_EV = 100.0
BUILD_EDGE_POINTS_PER_EV = 10.0
BUILD_OUTER_SPEED = 1.0
BUILD_FREQ_HZ = 100.0

# build_exafs_command: the XANES regions above up to edge + BUILD_EDGE_HI_EV,
# then k-space from there to k_max in EXAFS_K_REGIONS equal-k regions, each
# sped up so the k spacing never exceeds EXAFS_DK (1/Angstrom).
EXAFS_DK = 0.05
EXAFS_K_REGIONS = 8
#: E - E0 = k^2 / ETOK  (eV, 1/Angstrom): hbar^2 / 2 m_e = 3.81 eV A^2.
ETOK = 0.2624682843

# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StationProfile:
    """Everything CHERFD does differently at one endstation.

    Mirrors cherfd_claude ``confs/<station>_config.py``; the live server
    reports the same facts at ``GET /station`` (``cherfd_get_station``), and
    ``cherfd_check_readiness`` refuses when the two disagree.
    """
    name: str
    label: str
    #: Mono: Si lattice constant (A), sqrt(h2+k2+l2) and name per crystal set,
    #: beam-height offsets for table tracking (utils/mono.py).
    mono_dspacing: float
    lattice_factor: dict
    crystal_names: dict
    mono_min_gap_mm: dict
    #: Crystal set when neither the caller nor the server says (cherfd_claude
    #: MONO_DEFAULT_CRYSTAL); a station with a crystal file reads it first.
    default_crystal: str
    crystal_file: str | None
    #: deg per encoder tick (MONO_ENCODER_RESOLUTION) and per motor step
    #: (MOTOR_STEP_DEGREES = MOTOR1 MRES).
    encoder_resolution_deg: float
    motor_step_deg: float
    #: Cruise pulse rate at speed factor 1.0, steps/s: the station's default
    #: motion profile set (cherfd_claude confs/motion_profiles.py). The
    #: server reports the set it will use at /station full_speed_steps_per_s;
    #: cherfd_get_station flags a difference.
    full_speed_steps_per_s: float
    #: Offline energy window. None: only the live soft limits bound it
    #: (server /check_command and /station mono_energy_range_eV).
    energy_min_ev: float | None
    energy_max_ev: float | None
    undulator: bool
    #: The station's one scan detector (cherfd_claude /set_detector name),
    #: what it is, its signal column, and how the sweep must be set up for it.
    detector: str
    detector_label: str
    default_signal: str
    detector_setup: str
    #: Is the cscan_daq count / stream app used here?
    daq_available: bool
    #: Measured per-sweep overhead (move to start, arming, wrap-up, saving)
    #: on top of the profile duration.
    per_sweep_overhead_s: float
    #: A sweep whose energy coverage falls short of the command by more than
    #: this is flagged incomplete.
    range_shortfall_warn_ev: float
    #: May a continuous scan move the table (beam-height tracking)? Mirrors
    #: cherfd_claude TABLE_TRACKING_AVAILABLE. False at both stations; at
    #: BL4-1 it is beamline policy that the table never moves in a continuous
    #: scan. Not read from the environment: no deployment setting changes it.
    table_tracking: bool = False


STATION_PROFILES: dict[str, StationProfile] = {
    "bl15": StationProfile(
        name="bl15", label="BL15-2",
        mono_dspacing=5.42973,
        lattice_factor={"A": 3 ** 0.5, "B": 11 ** 0.5},
        crystal_names={"A": "Si(111)", "B": "Si(311)"},
        mono_min_gap_mm={"A": 6.5024, "B": 6.5278},
        default_crystal="B", crystal_file=None,
        encoder_resolution_deg=-1.0 / 160057.0, motor_step_deg=1e-5,
        # motion profile set legacy_32k: 100 us cruise interval
        full_speed_steps_per_s=10000.0,
        # SPEC refuses < 4950 eV (spec/cherfd.mac); stricter than REST's 4500
        energy_min_ev=4950.0, energy_max_ev=25000.0,
        undulator=True,
        detector="vortex",
        detector_label="Vortex on the 8-channel Xspress3 (XSP3_8Chan), MCA1",
        # ROI1 + 2*ROI_double (xspress/vortex.py), IOC dead-time corrected
        default_signal="vortex",
        detector_setup="SPEC must have run `xtc 1` so cherfd owns the Xspress3 "
                       "(else the vortex column is all zero)",
        daq_available=True,
        # 2025-10_Sokaras 16_MEA1058..._spot1: fwd/rev sweeps every ~30.5 s
        # for a 13.6 s profile
        per_sweep_overhead_s=17.0,
        range_shortfall_warn_ev=2.0,
    ),
    "bl41": StationProfile(
        name="bl41", label="BL4-1",
        # 41_spec/mono.mac: A = Si(220), B = Si(111)
        mono_dspacing=5.43102,
        lattice_factor={"A": 8 ** 0.5, "B": 3 ** 0.5},
        crystal_names={"A": "Si(220)", "B": "Si(111)"},
        # UNVERIFIED at BL4-1 (BL15-2 values, as in mono.mac)
        mono_min_gap_mm={"A": 6.5024, "B": 6.5278},
        default_crystal="B", crystal_file="/usr/local/lib/spec.d/mono.cfg",
        # MOTOR1 ERES / MRES, caget 2026-10-08
        encoder_resolution_deg=6.25e-6, motor_step_deg=2e-5,
        # motion profile set bl41_conservative (2026-10-16): 125 us cruise
        # interval, capped below SPEC's 10000 steps/s crystal slew
        full_speed_steps_per_s=8000.0,
        energy_min_ev=None, energy_max_ev=None,
        undulator=False,
        detector="ge32",
        detector_label="24-element Ge detector (XSP3_32Chan, xsp32 service)",
        # sum of the good elements' photons, ROI1 + 2*ROI2 on Double elements
        default_signal="xsum",
        detector_setup="the xsp32 service must be running (it gives the element count "
                       "and masks; no service, no Ge scan)",
        # cscan_daq is superseded at BL4-1 by cscan_acq (BACKPORTS.md)
        daq_available=False,
        # data/testing g4 (2026-10-10, box session, no SPEC count waiting):
        # a profile every 7.67 s for a 2.84 s simulated profile
        per_sweep_overhead_s=4.8,
        # a sweep that starts by reversing loses ~0.0084 deg of drive slack:
        # ~2 eV short at 7.1 keV (CLAUDE.md "BL4-1 mono backlash")
        range_shortfall_warn_ev=3.0,
    ),
}

DEFAULT_STATION = "bl15"


# CITATIONS — method -> reference. ``None`` = implemented, not yet attributed.
CITATIONS = {}  # limits cite their cherfd_claude source inline
