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
"""
from __future__ import annotations

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

#: Energy window. SPEC refuses < 4950 eV (spec/cherfd.mac); REST
#: /move_energy refuses < 4500 eV (server/server.py). Stricter wins.
ENERGY_MIN_EV = 4950.0
ENERGY_MAX_EV = 25000.0

#: Trigger frequency clamp (cscan_box/triggers.py trigger_timing).
FREQ_MIN_HZ = 20.0
FREQ_MAX_HZ = 500.0
#: Above this the Xspress3 is outside its tested rate (triggers.py warning).
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

#: Motor resolution, degrees per step (motion.MOTOR_STEP_DEGREES).
MOTOR_STEP_DEG = 1e-5
#: Full-speed cruise pulse rate, steps/s at speed factor 1.0
#: (FULL_SPEED_CRUISE_INTERVAL_US = 100 us -> 10 kHz).
FULL_SPEED_STEPS_PER_S = 10000.0
#: Settling time appended to every profile (multisegment_move default).
PROFILE_SETTLING_S = 0.5
#: Default crystal set. utils/mono.py hardcodes set_crystal('B') at import
#: and exposes no API to change it, so every live scan so far is Si(311).
DEFAULT_CRYSTAL = "B"

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

#: Signal column when the caller names none. The Xspress3 "vortex" column is
#: ROI1 + 2*ROI_double (xspress/vortex.py) with IOC dead-time correction.
#: Echoed back on every result; see refdoc counter selection.
DEFAULT_SIGNAL = "vortex"

#: FPGA ADC channels arrive offset-binary (~2.2e9 at zero). Subtracting 2**31
#: gives the signed reading. Presentation is on cscan_daq's unverified
#: hardware checklist (item 5), so every result that uses it says so.
ADC_OFFSET = 2 ** 31

#: trig_1 bit 31 marks an FPGA status-report frame, not a trigger
#: (cscan_box/references/DMA_structure_definition.txt). Scan data does not
#: filter them upstream; this package does.
STATUS_FRAME_BIT = 31

#: Detector frame k is the k-th trigger (0-based) when this is 0. The Xspress3
#: time-series frames are recorded in trigger order, so 0 is the design
#: assumption; it is a policy knob because no hardware test has pinned it
#: (a one-frame error here would shift fwd and rev sweeps oppositely).
DETECTOR_FRAME_OFFSET = 0

#: Frames whose energy falls this far outside the commanded range are
#: treated as corrupt (e.g. a 205917 eV frame in 2025-10_Sokaras_Pt).
ENERGY_OUTLIER_MARGIN_EV = 20.0

#: Sweeps from one acquisition sit tens of seconds apart; a gap longer than
#: this starts a new run even when the file root repeats.
RUN_GAP_S = 600.0

#: Measured per-sweep overhead (move to start, arming, wrap-up, pickling)
#: on top of the profile duration: 2025-10_Sokaras run
#: 16_MEA1058..._spot1 fwd/rev sweeps land every ~30.5 s for a 13.6 s profile.
PER_SWEEP_OVERHEAD_S = 17.0

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

# CITATIONS — method -> reference. ``None`` = implemented, not yet attributed.
CITATIONS = {}  # limits cite their cherfd_claude source inline
