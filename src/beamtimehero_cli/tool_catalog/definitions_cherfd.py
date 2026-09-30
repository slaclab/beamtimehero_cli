"""Tool schemas for the CHERFD continuous-scan surface.

Kept in its own module so ``definitions.py`` stays readable; appended to
``AUTONOMY_TOOL_DEFINITIONS`` there. Three branches, split the same way as
SPEC so an agent surface can grant them separately:

* ``cherfd``       -- offline: planning, sweep data, reduction, analysis, logs.
* ``cherfd-read``  -- live state from the cherfd server / cscan_daq (no mutation).
* ``cherfd-write`` -- actions; every leaf requires ``--justification``.

Every entry pins its branch with an explicit ``"tree"``. Names carry a
``cherfd_`` prefix: lineage is keyed by bare name, and a flat agent surface
shows names without their branch.

Only schema vocabulary beamtimehero's ``add_arg`` understands is used (type,
description, default, enum, items); scientific defaults are read from
``science.policy``, never restated.
"""
from __future__ import annotations

from beamtimehero_cli.science.cherfd import policy

TREE = "cherfd"
TREE_READ = "cherfd-read"
TREE_WRITE = "cherfd-write"

#: Live-state reads: the only non-mutating tools that reach a cherfd service.
_LIVE_READS = frozenset({
    "cherfd_get_status", "cherfd_get_server_settings", "cherfd_get_undulator_status",
    "cherfd_get_table_tracking", "cherfd_get_scan_results", "cherfd_wait_for_scan",
    "cherfd_get_daq_status", "cherfd_check_readiness",
})

# ---- shared fragments ---------------------------------------------------------

_J = {
    "justification": {
        "type": "string",
        "description": (
            "REQUIRED for every cherfd hardware/controller action. One sentence on why "
            "you are doing this now; stored in the action log before the request is sent. "
            "Empty justifications are refused."
        ),
    },
}

_RUN = {
    "run_id": {"type": "string", "description": (
        "Acquisition run id from cherfd_list_runs ('<file_dir>/<file_root>@<first timestamp>'). "
        "Preferred: file roots are reused, so a root alone can match several runs.")},
    "file_dir": {"type": "string", "description": "Data subdirectory (e.g. '2025-10_Sokaras'). "
                 "With file_root and no run_id, the LATEST matching run is used."},
    "file_root": {"type": "string", "description": "File root the run was saved under."},
    "include_partial": {"type": "boolean", "default": False,
                        "description": "Also consider partial sweeps saved by a stopped scan."},
}

_SELECT = {
    "sweeps": {"type": "array", "items": {"type": "integer"},
               "description": "Sweep numbers to use (default: all in the run)."},
    "direction": {"type": "string", "enum": ["both", "fwd", "rev"], "default": "both",
                  "description": "Which sweep directions to use."},
    "align": {"type": "string", "enum": ["trigger", "row"], "default": "trigger", "description": (
        "Detector-frame pairing. 'trigger' (default) re-pairs Xspress3 frame k with trigger k; "
        "'row' keeps the pairing recorded in the file, which is off by up to a few frames "
        "because rows are stored out of time order. Use 'row' only to compare.")},
}

_SIGNAL = {
    "signal": {"type": "string", "description": (
        f"Signal column (default {policy.DEFAULT_SIGNAL!r} = Xspress3 ROI1 + 2*ROI_double, IOC "
        "dead-time corrected). Set it explicitly; cherfd_read_sweep lists the columns. adc_* "
        "columns are converted from offset-binary.")},
    "normalize_by": {"type": "string", "description": (
        "Normalizer (I0) column, e.g. 'adc_1'. Merged value per bin = sum(signal)/sum(I0). "
        "Omit for raw signal (then beam-current variation is in the result).")},
}

_BIN = {
    "bin_ev": {"type": "number", "default": policy.DEFAULT_BIN_EV,
               "description": "Energy bin width (eV) for rebinning the continuous sweeps."},
    "e_min": {"type": "number", "description": "Lower energy bound (eV)."},
    "e_max": {"type": "number", "description": "Upper energy bound (eV)."},
}

_DIRCORR = {"direction_correction_ev": {"type": "number", "default": 0.0, "description": (
    "rev-minus-fwd feature shift to remove before merging (from cherfd_compare_directions "
    "suggested_direction_correction_ev): fwd energies move +c/2, rev -c/2.")}}

_PLOT = {"plot": {"type": "boolean", "default": True, "description": "Return a PNG figure."}}

_COMMAND = {"command": {"type": "string", "description": (
    "cherfd command: 'cherfd start_eV end1_eV speed1 [end2_eV speed2 ...] freq_Hz'. Speeds are "
    f"fractions of full mono speed ({policy.SPEED_FACTOR_MIN}-{policy.SPEED_FACTOR_MAX} used); "
    "freq is the trigger/detector frame rate (integer Hz, typically 20-100). "
    "Example: 'cherfd 8968 8990 1 9010 .3 9100 1 100'.")}}

_CRYSTAL = {"crystal": {"type": "string", "enum": ["A", "B"], "description": (
    f"Mono crystal set: A = Si(111), B = Si(311). Default {policy.DEFAULT_CRYSTAL} "
    "(the controller is hardcoded to B).")}}


def _t(name: str, description: str, props: dict | None = None, required: list[str] | None = None,
       tree: str | None = None) -> dict:
    return {
        "type": "function",
        "tree": tree or (TREE_READ if name in _LIVE_READS else TREE),
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []},
        },
    }


def _act(name, description, props=None, required=None):
    return _t(name, description, {**_J, **(props or {})}, ["justification", *(required or [])],
              tree=TREE_WRITE)


CHERFD_TOOL_DEFINITIONS: list[dict] = [
    # ======================= control: reads =================================
    _t("cherfd_get_status",
       "cherfd controller state (idle / initializing / moving_to_start / Running / Stopped / error), "
       "whether an acquisition is active, the current sweep, and whether it is done. Read-only."),
    _t("cherfd_get_server_settings",
       "Persistent controller settings: data directory + file root, gap calibration offset, gap "
       "move latency, stored mono encoder calibration, table-tracking switch and anchor."),
    _t("cherfd_get_undulator_status",
       "Undulator tracking: enabled flag, latched harmonic, gap position, calibration offset, latency."),
    _t("cherfd_get_table_tracking",
       "Table (beam-height) tracking switch, the stored anchor, and a crystal-mismatch warning."),
    _t("cherfd_get_scan_results",
       "Summary of the last completed sweep held by the controller (parameters, summary, "
       "row count, energy range, a few preview rows). For analysis use the on-disk tools.",
       {"preview_rows": {"type": "integer", "default": 5, "description": "Rows to preview."}}),
    _t("cherfd_wait_for_scan",
       "Block until the running acquisition finishes (done confirmed twice) or timeout. Read-only "
       "polling of /spec_status. Returns the final state; 'Stopped' means it was stopped.",
       {"timeout_s": {"type": "number", "default": 600, "description": "Give up after this many seconds (max 7200)."},
        "poll_s": {"type": "number", "default": 2.0, "description": "Poll interval (s)."}}),
    _t("cherfd_get_daq_status",
       "cscan_daq count app health (IOC reachability) and state, including the last count result."),
    _t("cherfd_check_readiness",
       "Pre-flight before a scan: controller idle and not stop-latched, data path, encoder "
       "calibration, undulator and table-tracking state, beam status (via beamtimehero "
       "spec-read get_beam_status), and optional validation of the command you intend to run. "
       "Returns ready + blockers + warnings. Read-only.",
       {**_COMMAND, "include_beam_status": {"type": "boolean", "default": True,
                                            "description": "Also query beam status through beamtimehero."}}),
    # ======================= control: actions ===============================
    _act("cherfd_start_scan",
         "Start a continuous-scan acquisition (N sweeps, optionally forward+reverse each). Validates "
         "the command offline first, refuses unless the controller is idle, then confirms from "
         "/status that the scan really started (the server answers success before deciding). "
         "Returns immediately; follow with cherfd_wait_for_scan. Run cherfd_check_readiness first.",
         {**_COMMAND,
          "sweeps": {"type": "integer", "default": 1, "description": f"Number of sweeps (1-{policy.SWEEPS_MAX})."},
          "reverse": {"type": "boolean", "default": False,
                      "description": "Each sweep runs forward then reverse (two profiles per sweep)."},
          "undulator_scan": {"type": "boolean", "default": True,
                             "description": "Track the undulator gap during the sweep (production default)."},
          "file_dir": {"type": "string", "description": "Data subdirectory (default: the server's data_directory)."},
          "file_root": {"type": "string", "description": "File root for this run (default: the server's file_root)."}},
         ["command"]),
    _act("cherfd_stop_scan",
         "Stop the running acquisition: halts sweeps, disables triggers, stops the gap, saves partial "
         "data. The controller then stays 'Stopped' until cherfd_clear_stop. Works even when the "
         "write safety switch is off."),
    _act("cherfd_clear_stop", "Clear the 'Stopped'/'error' latch so a new scan can start."),
    _act("cherfd_reset_controller",
         "Reset controller state to idle (halts a running scan first). Heavier than clear_stop."),
    _act("cherfd_set_data_path",
         "Set where subsequent scans are saved (data/<file_dir>/..._<file_root>_...). Persisted.",
         {"file_dir": {"type": "string", "description": "Subdirectory under the cherfd data root."},
          "file_root": {"type": "string", "description": "File-name root for subsequent runs."}},
         ["file_dir", "file_root"]),
    _act("cherfd_set_undulator_tracking",
         "Enable/disable undulator gap control by the cherfd controller (disable resets the latched harmonic).",
         {"enable": {"type": "boolean", "description": "True to enable."}}, ["enable"]),
    _act("cherfd_set_gap_calibration_offset",
         "Set the constant added to the harmonic-polynomial gap for every gap move (mm). Persisted. "
         "cherfd_check_gap_tracking reports the offset implied by recorded data.",
         {"offset_mm": {"type": "number", "description": "Offset in mm (typically ~0.01-0.05)."}},
         ["offset_mm"]),
    _act("cherfd_set_gap_move_latency",
         "Set the look-ahead latency (s) of predictive gap tracking during a sweep. Persisted.",
         {"latency_s": {"type": "number", "description": "Latency in seconds (>= 0)."}}, ["latency_s"]),
    _act("cherfd_set_table_tracking",
         "Enable/disable table (beam-height) tracking during scans. Enabling needs a stored anchor; "
         "table motors are not yet commissioned on hardware.",
         {"enable": {"type": "boolean", "description": "True to enable."}}, ["enable"]),
    _act("cherfd_set_table_anchor",
         "Store the table-tracking anchor: aligned energy + both table heights (+ crystal), or capture "
         "the current mono energy and table readbacks.",
         {"capture": {"type": "boolean", "default": False, "description": "Snapshot current positions instead."},
          "energy_ev": {"type": "number", "description": "Anchor energy (eV)."},
          "z1_mm": {"type": "number", "description": "Upstream table height (mm)."},
          "z2_mm": {"type": "number", "description": "Downstream table height (mm)."},
          **_CRYSTAL}),
    _act("cherfd_move_energy",
         "Coordinated mono + undulator-gap move to an energy (asynchronous). Refused while a scan runs.",
         {"energy_ev": {"type": "number", "description":
                        f"Target energy ({policy.ENERGY_MIN_EV:g}-{policy.ENERGY_MAX_EV:g} eV)."}},
         ["energy_ev"]),
    _act("cherfd_move_gap", "Move the undulator gap directly (mm), asynchronously.",
         {"gap_mm": {"type": "number", "description": f"Gap ({policy.GAP_MIN_MM}-{policy.GAP_MAX_MM} mm)."}},
         ["gap_mm"]),
    _act("cherfd_stop_gap", "Stop undulator gap motion. Works even when the write safety switch is off."),
    _act("cherfd_calibrate_encoder",
         "Re-derive the energy-from-encoder calibration at the CURRENT mono position (set_absev: "
         "the motor record's angle is taken as truth for the current encoder reading). Only do "
         "this with the mono parked at a trusted position. Persisted."),
    _act("cherfd_daq_count",
         "Hardware-timed count on the cscan box counters (ctr1-4) via the cscan_daq app; "
         "waits for the result by default.",
         {"duration_s": {"type": "number", "default": 1.0, "description": "Count time (0.01-60 s)."},
          "freq_hz": {"type": "number", "default": 10.0, "description": "Chop rate for gate_mode='chopped' (0.5-100)."},
          "gate_mode": {"type": "string", "enum": ["single", "chopped"], "default": "single",
                        "description": "single = one gate for the whole window; chopped = trigger list."},
          "quick": {"type": "boolean", "default": False, "description": "Quick single-shot path (verify-only config)."},
          "wait": {"type": "boolean", "default": True, "description": "Wait for and return the result."}}),
    _act("cherfd_daq_abort", "Abort a running cscan_daq count. Works even when the write switch is off."),
    # ======================= planning (offline) =============================
    _t("cherfd_validate_command",
       "Check a cherfd command against every layer it meets (SPEC refusals, controller speed clamp, "
       "FPGA trigger quantization, energy limits), and estimate per-region steps, time and "
       "points/eV, the reverse command, and undulator harmonic changes. No hardware.",
       {**_COMMAND, **_CRYSTAL}, ["command"]),
    _t("cherfd_build_command",
       "Build a 3-region XANES command around an edge: fast pre-edge, slow edge region solved for "
       "a target points/eV, fast post-edge. Give edge_ev or element(+edge). No hardware.",
       {"element": {"type": "string", "description": "Absorber, e.g. 'Cu' (uses xraydb edge table)."},
        "edge": {"type": "string", "default": "K", "description": "Edge label."},
        "edge_ev": {"type": "number", "description": "Edge energy (eV); overrides element."},
        "pre_edge_ev": {"type": "number", "default": policy.BUILD_PRE_EDGE_EV, "description": "Scan start below the edge (eV)."},
        "edge_lo_ev": {"type": "number", "default": policy.BUILD_EDGE_LO_EV, "description": "Slow region starts this far below the edge."},
        "edge_hi_ev": {"type": "number", "default": policy.BUILD_EDGE_HI_EV, "description": "Slow region ends this far above the edge."},
        "post_edge_ev": {"type": "number", "default": policy.BUILD_POST_EDGE_EV, "description": "Scan end above the edge (eV)."},
        "edge_points_per_ev": {"type": "number", "default": policy.BUILD_EDGE_POINTS_PER_EV, "description": "Target frame density in the edge region."},
        "outer_speed": {"type": "number", "default": policy.BUILD_OUTER_SPEED, "description": "Speed factor outside the edge region."},
        "freq_hz": {"type": "number", "default": policy.BUILD_FREQ_HZ, "description": "Trigger rate (Hz)."},
        **_CRYSTAL}),
    _t("cherfd_simulate_scan",
       "Run the PRODUCTION motion-profile generator offline (cherfd_claude cscan_box/simulate.py, "
       "EPICS blocked): refusal if the profile cannot be built, duration, segments used/250, "
       "per-region speed and points/eV, landing error, optional table-tracking profile. Plot of "
       "energy and scan rate vs time. Needs CHERFD_PROJECT_DIR.",
       {**_COMMAND,
        "ramp_type": {"type": "string", "enum": ["s_ramp", "linear"], "default": "s_ramp",
                      "description": "Ramp shape (real scans use s_ramp)."},
        "table": {"type": "boolean", "default": False, "description": "Also derive the table-tracking profile."},
        "table_anchor_ev": {"type": "number", "description": "Table anchor energy (default: scan start)."},
        **_CRYSTAL, **_PLOT},
       ["command"]),
    _t("cherfd_estimate_acquisition_time",
       "Wall-clock estimate for N sweeps (x2 with reverse): profile time from the production "
       "simulator (or a cruise estimate) plus per-sweep overhead (measured default, or measured "
       "from a reference run's cadence).",
       {**_COMMAND,
        "sweeps": {"type": "integer", "default": 1, "description": "Number of sweeps."},
        "reverse": {"type": "boolean", "default": False, "description": "Forward+reverse per sweep."},
        "overhead_s": {"type": "number", "description": f"Per-profile overhead (default {policy.PER_SWEEP_OVERHEAD_S} s)."},
        "reference_run_id": {"type": "string", "description": "Measure overhead from this run's sweep cadence."},
        "use_simulator": {"type": "boolean", "default": True, "description": "Use the production simulator when available."}},
       ["command"]),
    _t("cherfd_undulator_gap_for_energy",
       "Undulator harmonic and gap for energies (controller polynomial + optional calibration "
       "offset), and harmonic switches inside a scan range. No hardware.",
       {"energy_ev": {"type": "number", "description": "One energy (eV)."},
        "energies_ev": {"type": "array", "items": {"type": "number"}, "description": "Several energies (eV)."},
        "e_start": {"type": "number", "description": "Scan range start (eV)."},
        "e_end": {"type": "number", "description": "Scan range end (eV)."},
        "calibration_offset_mm": {"type": "number", "default": 0.0, "description": "Gap calibration offset to add."}}),
    _t("cherfd_mono_convert",
       "Mono conversions for BL15-2: energy <-> Bragg angle <-> encoder (given the calibration "
       "offset), scan rate at full speed, beam-height offset vs an anchor, trigger quantization, "
       "and the speed factor for a target points/eV. No hardware.",
       {"energy_ev": {"type": "number", "description": "Energy (eV)."},
        "bragg_deg": {"type": "number", "description": "Bragg angle (deg)."},
        "encoder": {"type": "number", "description": "Encoder reading (ticks)."},
        "calibration_offset_deg": {"type": "number", "description": "Encoder calibration offset (deg)."},
        "anchor_ev": {"type": "number", "description": "Anchor energy for the beam-height offset."},
        "freq_hz": {"type": "number", "description": "Trigger rate for timing/speed calculations."},
        "points_per_ev": {"type": "number", "description": "Target frame density (needs freq_hz)."},
        **_CRYSTAL}),
    # ======================= data access ====================================
    _t("cherfd_list_data_dirs", "List data subdirectories with cherfd sweep files, newest file time."),
    _t("cherfd_list_runs",
       "List acquisition runs (one start_acquisition each: sweeps 1..N, fwd/rev) newest first, with "
       "run_id, sweep counts and cadence. Use the run_id in every analysis tool.",
       {"file_dir": _RUN["file_dir"], "file_root": _RUN["file_root"],
        "contains": {"type": "string", "description": "Case-insensitive substring filter on file_root."},
        "limit": {"type": "integer", "default": 20, "description": "Max runs to return."},
        "include_partial": _RUN["include_partial"]}),
    _t("cherfd_get_latest_run",
       "The newest run (optionally of a file_dir/file_root) with its sweep files and command.", {**_RUN}),
    _t("cherfd_get_sweep_metadata",
       "Per-sweep ScanResults metadata (command, parameters, detector mode, frame counts) read "
       "without importing the cherfd code.",
       {**_RUN, "sweeps": _SELECT["sweeps"], "direction": _SELECT["direction"]}),
    _t("cherfd_read_sweep",
       "One cleaned sweep: frames in trigger order, status frames and corrupt energies dropped, "
       "detector re-aligned; QC ledger, available columns, decimated data.",
       {**_RUN,
        "sweep": {"type": "integer", "description": "Sweep number (default: first)."},
        "direction": {"type": "string", "enum": ["fwd", "rev"], "default": "fwd", "description": "Direction."},
        "columns": {"type": "array", "items": {"type": "string"},
                    "description": "Columns to return (default trigger, absev, vortex)."},
        "max_points": {"type": "integer", "default": 200, "description": "Decimate to about this many rows."},
        "align": _SELECT["align"]}),
    # ======================= analysis =======================================
    _t("cherfd_assess_run_quality",
       "QC verdict per sweep and for the run: dropped triggers, status frames, corrupt energies, "
       "frame rate vs command, energy back-steps, incomplete coverage, all-zero detector (SPEC still "
       "owns the Xspress3), recorded detector-pairing offset. Run after every acquisition.",
       {**_RUN, **_SELECT, "signal": _SIGNAL["signal"]}),
    _t("cherfd_merge_sweeps",
       "Rebin sweeps onto one energy grid and merge (ratio of sums with a normalizer, frame-weighted "
       "mean without) with standard errors and frames per bin. Failed sweeps are excluded by default. "
       "Absolute intensity is preserved (no per-sweep min-max).",
       {**_RUN, **_SELECT, **_SIGNAL, **_BIN, **_DIRCORR,
        "per_sweep_scaling": {"type": "string", "enum": ["none", "mean"], "default": "none",
                              "description": "'mean' rescales each sweep to the first sweep's mean (I0 stand-in)."},
        "exclude_failed": {"type": "boolean", "default": True, "description": "Drop sweeps whose QC verdict is fail."},
        "max_points": {"type": "integer", "default": 400, "description": "Decimate the returned arrays."},
        **_PLOT}),
    _t("cherfd_compare_directions",
       "Forward vs reverse sweeps: energy shift between them (scale/offset-free least squares), E0 "
       "of each, intensity ratio. A shift means an encoder-latch/gate lag; correct before merging "
       "both directions.",
       {**_RUN, "sweeps": _SELECT["sweeps"], "align": _SELECT["align"], **_SIGNAL, **_BIN, **_PLOT}),
    _t("cherfd_analyze_sweep_drift",
       "Per-sweep E0, energy shift vs the first sweep, mean level and noise, with linear trends per "
       "direction; flags edge drift and intensity change (damage / beam decay).",
       {**_RUN, **_SELECT, **_SIGNAL, **_BIN}),
    _t("cherfd_analyze_convergence",
       "Noise of the running merge vs number of sweeps, fit to N^-alpha/2 (alpha~1: statistics "
       "limited), and sweeps/time still needed for a target relative noise.",
       {**_RUN, **_SELECT, **_SIGNAL, **_BIN, **_DIRCORR,
        "target_relative_noise": {"type": "number", "description": "Target noise / mean level, e.g. 0.005."},
        **_PLOT}),
    _t("cherfd_assess_energy_calibration",
       "Measured E0 of the merged spectrum vs a reference (value or element/edge table), converted "
       "into the Bragg-angle / encoder correction. Does not change anything.",
       {**_RUN, **_SELECT, **_SIGNAL, **_BIN, **_DIRCORR,
        "reference_e0_ev": {"type": "number", "description": "Reference edge energy (eV), e.g. a foil."},
        "element": {"type": "string", "description": "Use the tabulated edge of this element instead."},
        "edge": {"type": "string", "default": "K", "description": "Edge label."},
        **_CRYSTAL}),
    _t("cherfd_check_gap_tracking",
       "Recorded undulator gap vs the ideal harmonic gap at each frame's energy: implied calibration "
       "offset, tracking residual, and harmonic changes in the scanned range.",
       {**_RUN, **_SELECT}),
    _t("cherfd_export_merged",
       "Write the merged spectrum for the beamtimehero XAS tools: 'twocol' (energy, intensity; "
       "+ .sem.dat) or 'spec' (one #S per sweep + merged). Returns the path to pass as file_name.",
       {**_RUN, **_SELECT, **_SIGNAL, **_BIN, **_DIRCORR,
        "per_sweep_scaling": {"type": "string", "enum": ["none", "mean"], "default": "none",
                              "description": "As in cherfd_merge_sweeps."},
        "format": {"type": "string", "enum": ["twocol", "spec"], "default": "twocol", "description": "Output format."},
        "output_name": {"type": "string", "description": "File stem (default: from the run id)."}}),
    # ======================= plots ==========================================
    _t("cherfd_plot_sweeps",
       "Every selected sweep on the common grid plus the merge (optionally stacked).",
       {**_RUN, **_SELECT, **_SIGNAL, **_BIN, **_DIRCORR,
        "offset": {"type": "number", "default": 0.0, "description": "Vertical offset between sweeps."}}),
    _t("cherfd_plot_sweep_diagnostics",
       "Four-panel diagnostics for one sweep: energy vs trigger, per-frame energy step, undulator "
       "gap vs ideal, raw signal.",
       {**_RUN,
        "sweep": {"type": "integer", "description": "Sweep number (default: first)."},
        "direction": {"type": "string", "enum": ["fwd", "rev"], "default": "fwd", "description": "Direction."},
        "signal": _SIGNAL["signal"], "align": _SELECT["align"]}),
    # ======================= logs ===========================================
    _t("cherfd_list_logs",
       "cherfd_claude log files (cherfd_main, energy, undulator, triggers, vortex, ...), newest first.",
       {"component": {"type": "string", "description": "Only this component (file prefix)."},
        "limit": {"type": "integer", "default": 30, "description": "Max files."}}),
    _t("cherfd_read_log", "Tail of a cherfd log file (by name, or the newest of a component).",
       {"name": {"type": "string", "description": "Exact file name from cherfd_list_logs."},
        "component": {"type": "string", "description": "Newest file of this component."},
        "lines": {"type": "integer", "default": 100, "description": "Lines from the end (max 2000)."}}),
    _t("cherfd_search_logs", "Regex search over recent cherfd log files.",
       {"pattern": {"type": "string", "description": "Case-insensitive regular expression."},
        "component": {"type": "string", "description": "Only this component."},
        "max_files": {"type": "integer", "default": 10, "description": "Newest N files to search."},
        "limit": {"type": "integer", "default": 100, "description": "Max matches."}},
       ["pattern"]),
]

CHERFD_TOOL_CATEGORIES: list[tuple[str, list[str]]] = [
    ("CHERFD Control - state", ["cherfd_get_status", "cherfd_get_server_settings", "cherfd_get_undulator_status",
                         "cherfd_get_table_tracking", "cherfd_get_scan_results", "cherfd_wait_for_scan",
                         "cherfd_get_daq_status", "cherfd_check_readiness"]),
    ("CHERFD Control - acquisition", ["cherfd_start_scan", "cherfd_stop_scan", "cherfd_clear_stop",
                               "cherfd_reset_controller", "cherfd_set_data_path", "cherfd_daq_count",
                               "cherfd_daq_abort"]),
    ("CHERFD Control - beamline", ["cherfd_move_energy", "cherfd_move_gap", "cherfd_stop_gap",
                            "cherfd_set_undulator_tracking", "cherfd_set_gap_calibration_offset",
                            "cherfd_set_gap_move_latency", "cherfd_set_table_tracking",
                            "cherfd_set_table_anchor", "cherfd_calibrate_encoder"]),
    ("CHERFD Planning", ["cherfd_validate_command", "cherfd_build_command", "cherfd_simulate_scan",
                  "cherfd_estimate_acquisition_time", "cherfd_undulator_gap_for_energy",
                  "cherfd_mono_convert"]),
    ("CHERFD Data", ["cherfd_list_data_dirs", "cherfd_list_runs", "cherfd_get_latest_run",
              "cherfd_get_sweep_metadata", "cherfd_read_sweep"]),
    ("CHERFD Analysis", ["cherfd_assess_run_quality", "cherfd_merge_sweeps", "cherfd_compare_directions",
                  "cherfd_analyze_sweep_drift", "cherfd_analyze_convergence",
                  "cherfd_assess_energy_calibration", "cherfd_check_gap_tracking",
                  "cherfd_export_merged", "cherfd_plot_sweeps", "cherfd_plot_sweep_diagnostics"]),
    ("CHERFD Logs", ["cherfd_list_logs", "cherfd_read_log", "cherfd_search_logs"]),
]
