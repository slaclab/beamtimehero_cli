"""Lineage for the CHERFD tools; merged into ``TOOL_LINEAGE`` by ``lineage.py``.

``mutates`` has the meaning given in ``lineage.py``: True means the handler
sends a command registered as ``action`` or ``stop`` in
``cherfd_control/commands.py`` through ``audited_cherfd``, so it requires a
justification and is action-logged before dispatch. Writing an export file,
reading sweep data, simulating a profile -- all False.
"""
from __future__ import annotations

_SRV = "cherfd control server REST API (server/server.py, default :5002); mock unless CHERFD_MOCK=0."
_DAQ = "cscan_daq FastAPI app (cscan_daq/app.py, default :5004); mock unless CHERFD_MOCK=0."
_DATA = ("Sweep pickles under CHERFD_DATA_DIR/<file_dir>/: scan_results_*_dataframe.pkl (frames), "
         "vortex_*.pkl (Xspress3), scan_results_*.pkl (ScanResults via stub unpickler).")


def _e(long, func, out, source, detail, depends=(), mutates=False):
    return {"long_description": long, "python_func": func, "spec_command": None,
            "mutates": mutates, "output": out, "source": source, "source_detail": detail,
            "depends_on": list(depends)}


def _read(route, long, out, depends=()):
    return _e(long, f"tools_cherfd -> audited_cherfd('{route}')", out,
              "cherfd_server", _SRV, depends)


def _action(route, long, out, depends=(), source="cherfd_server"):
    return _e(long, f"tools_cherfd -> audited_cherfd('{route}', body, justification)",
              out, source, _SRV if source == "cherfd_server" else _DAQ, depends, mutates=True)


def _data(func, long, out, depends=("cherfd_list_runs",), source="cherfd_datafile"):
    return _e(long, func, out, source, _DATA, depends)


CHERFD_LINEAGE: dict[str, dict] = {
    # ---- control reads ----------------------------------------------------
    "cherfd_get_status": _read(
        "status", "Controller state machine and current scan; also /spec_status for 'done'.",
        "JSON: envelope + summary {state, acquisition_active, done, current_scan, stop_latched}"),
    "cherfd_get_server_settings": _read(
        "server_settings", "Persisted controller settings (server_settings.json on the host).",
        "JSON: settings dict"),
    "cherfd_get_undulator_status": _read(
        "undulator_status", "Undulator control flag, harmonic and gap as the controller sees them.",
        "JSON: {enabled, harmonic, gap_position, gap_calibration_offset, gap_move_latency}"),
    "cherfd_get_table_tracking": _read(
        "table_tracking", "Beam-height tracking switch, anchor, and crystal-mismatch warning.",
        "JSON: {enabled, anchor, crystal_warning}"),
    "cherfd_get_scan_results": _read(
        "results", "Last sweep the controller holds in memory, summarized so a multi-thousand "
        "row frame dump does not flood the agent's context.",
        "JSON: {parameters, summary, n_rows, columns, energy_range, preview}"),
    "cherfd_wait_for_scan": _e(
        "Polls /spec_status (the SPEC cherfd_wait contract: done confirmed twice) until the "
        "acquisition finishes or the timeout expires; read-only.",
        "tools_cherfd.t_wait_for_scan -> audited_cherfd('spec_status') loop", "JSON: {done, waited_s, final_state}",
        "cherfd_server", _SRV, ["cherfd_start_scan"]),
    "cherfd_get_daq_status": _e(
        "Health and state of the cscan_daq count app.",
        "tools_cherfd -> audited_cherfd('daq_health') + audited_cherfd('daq_status')",
        "JSON: {health, status}", "cherfd_daq", _DAQ),
    "cherfd_check_readiness": _e(
        "Aggregates controller state, settings, undulator, table tracking, optional command "
        "validation and beamtimehero's SPEC beam status into blockers/warnings before a scan.",
        "tools_cherfd.t_check_readiness -> audited_request(status|server_settings|undulator_status|"
        "table_tracking) + science.cherfd.command.validate_command + beamtimehero execute_tool(spec-read/get_beam_status)",
        "JSON: {ready, blockers, warnings, info}", "cherfd_server",
        _SRV + " Beam status via the SPEC beam_status read (SPEC_MOCK applies)."),
    # ---- control actions --------------------------------------------------
    "cherfd_start_scan": _action(
        "start_acquisition",
        "Offline validation, idle pre-check, POST /start_acquisition, then a /status "
        "confirmation because the route reports success before the controller accepts the scan.",
        "JSON: envelope + {started, state_after, estimate, validation_warnings}",
        ["cherfd_check_readiness", "cherfd_validate_command"]),
    "cherfd_stop_scan": _action("stop", "Stops sweeps, triggers and gap; bypasses the write switch.",
                                "JSON envelope", ["cherfd_get_status"]),
    "cherfd_clear_stop": _action("clear_stop", "Clears the Stopped/error latch.", "JSON envelope",
                                 ["cherfd_get_status"]),
    "cherfd_reset_controller": _action("reset", "Resets the controller to idle.", "JSON envelope"),
    "cherfd_set_data_path": _action("set_data_path", "Sets and persists file_dir/file_root.",
                                    "JSON envelope + settings"),
    "cherfd_set_undulator_tracking": _action("set_undulator_enable", "Toggles controller gap control.",
                                             "JSON envelope"),
    "cherfd_set_gap_calibration_offset": _action(
        "set_gap_calibration_offset", "Sets and persists the gap offset added to every gap move.",
        "JSON envelope", ["cherfd_check_gap_tracking"]),
    "cherfd_set_gap_move_latency": _action("set_gap_move_latency",
                                           "Sets and persists the predictive gap-tracking latency.",
                                           "JSON envelope"),
    "cherfd_set_table_tracking": _action("set_table_tracking", "Toggles table (beam-height) tracking.",
                                         "JSON envelope", ["cherfd_set_table_anchor"]),
    "cherfd_set_table_anchor": _action("set_table_anchor", "Stores or captures the table anchor.",
                                       "JSON envelope + anchor"),
    "cherfd_move_energy": _action("move_energy", "Coordinated mono+gap move (asynchronous).",
                                  "JSON envelope + expected_gap", ["cherfd_get_status"]),
    "cherfd_move_gap": _action("move_gap", "Direct undulator gap move (asynchronous).", "JSON envelope"),
    "cherfd_stop_gap": _action("stop_gap", "Stops gap motion; bypasses the write switch.", "JSON envelope"),
    "cherfd_calibrate_encoder": _action("calibrate_energy",
                                        "set_absev at the current mono position; persisted.",
                                        "JSON envelope + {energy, xtal, enc}",
                                        ["cherfd_assess_energy_calibration"]),
    "cherfd_daq_count": _action("daq_count", "Hardware-timed counter count; polls for the result.",
                                "JSON envelope + count_result", source="cherfd_daq"),
    "cherfd_daq_abort": _action("daq_abort", "Aborts a count; bypasses the write switch.",
                                "JSON envelope", source="cherfd_daq"),
    # ---- planning ---------------------------------------------------------
    "cherfd_validate_command": _e(
        "Layered command validation with per-region cruise estimates and harmonic changes.",
        "science.cherfd.command.validate_command", "JSON: {ok, errors, warnings, effective_command, regions, ...}",
        "tool_chain", "Pure computation (ports of cscan_box/motion.py, triggers.py, spec/cherfd.mac rules)."),
    "cherfd_build_command": _e(
        "Three-region XANES command solved for an edge-region frame density.",
        "science.cherfd.command.build_command (+ science.tables.edges.get_edge_info)",
        "JSON: {command, edge_speed, notes, validation}", "tool_chain",
        "Pure computation; edge energies from xraydb via beamtimehero."),
    "cherfd_simulate_scan": _e(
        "The production profile generator run out of process with EPICS blocked.",
        "cherfd_control.simulator.simulate -> subprocess cscan_box.simulate.simulate_command; science.plots.cherfd.plot_profile",
        "JSON: {ok, summary, regions, checks, warnings[, table]} + PNG", "cherfd_simulator",
        "CHERFD_PROJECT_DIR/cscan_box/simulate.py run with CHERFD_PYTHON.", ["cherfd_validate_command"]),
    "cherfd_estimate_acquisition_time": _e(
        "Sweeps x (profile time + overhead), overhead measured by default or from a run.",
        "cherfd_control.simulator.simulate | science.cherfd.command.validate_command -> science.cherfd.command.estimate_acquisition_time",
        "JSON: {total_s, total_min, seconds_per_profile, duty_cycle, ...}", "cherfd_simulator",
        "Simulator when available, else cruise estimate; optional run cadence from sweep timestamps."),
    "cherfd_undulator_gap_for_energy": _e(
        "Harmonic/gap polynomial of utils/undulator.py.", "science.cherfd.beamline.harmonic_gap / harmonic_changes",
        "JSON: {points, scan_range}", "tool_chain", "Pure computation."),
    "cherfd_mono_convert": _e(
        "Bragg / encoder / scan-rate / beam-height conversions of utils/mono.py.",
        "science.cherfd.beamline.*", "JSON: conversions", "tool_chain", "Pure computation."),
    # ---- data -------------------------------------------------------------
    "cherfd_list_data_dirs": _data("spec_data.cherfd.files.list_data_dirs", "Subdirectories holding sweeps.",
                                   "JSON: {data_root, dirs}", depends=()),
    "cherfd_list_runs": _data("spec_data.cherfd.files.find_sweeps + group_runs",
                              "Groups sweep files into acquisition runs (root reuse aware).",
                              "JSON: {runs: [{run_id, n_fwd, n_rev, seconds_per_profile, ...}]}", depends=()),
    "cherfd_get_latest_run": _data("spec_data.cherfd.files.resolve_run + spec_data.cherfd.loader.load_metadata",
                                   "Newest run and its sweep files.", "JSON: run + command", depends=()),
    "cherfd_get_sweep_metadata": _data("spec_data.cherfd.loader.load_metadata (stub unpickler)",
                                       "ScanResults parameters/summary without importing cherfd.",
                                       "JSON: per-sweep metadata"),
    "cherfd_read_sweep": _data("spec_data.cherfd.sweeps.load_clean -> science.cherfd.frames.clean_sweep",
                               "One cleaned, trigger-ordered sweep with QC ledger.",
                               "JSON: {qc, columns_available, data}"),
    # ---- analysis ---------------------------------------------------------
    "cherfd_assess_run_quality": _data("spec_data.cherfd.sweeps.load_clean -> science.cherfd.frames.sweep_quality",
                                       "Per-sweep and run QC verdicts.", "JSON: {overall, sweeps, usable_sweeps}"),
    "cherfd_merge_sweeps": _data("spec_data.cherfd.sweeps.load_clean -> science.cherfd.reduce.bin_sweeps; science.plots.cherfd.plot_merged",
                                 "Rebin + merge preserving absolute intensity.",
                                 "JSON: {energy, merged, sem, frames_per_bin, ...} + PNG",
                                 ("cherfd_list_runs", "cherfd_assess_run_quality")),
    "cherfd_compare_directions": _data("science.cherfd.analysis.compare_directions -> reduce.best_shift + science.xas.e0.find_e0",
                                       "fwd vs rev energy shift and E0.",
                                       "JSON: {rev_minus_fwd_shift_ev, per_direction_error_ev, e0_*} + PNG"),
    "cherfd_analyze_sweep_drift": _data("science.cherfd.analysis.sweep_drift",
                                        "Per-sweep E0/shift/level trends.", "JSON: {sweeps, trends, flags, verdict}"),
    "cherfd_analyze_convergence": _data("science.cherfd.reduce.bin_sweeps -> science.cherfd.reduce.convergence",
                                        "Noise vs sweeps and sweeps needed for a target.",
                                        "JSON: {curve, fit, sweeps_needed, interpretation} + PNG"),
    "cherfd_assess_energy_calibration": _data("science.cherfd.analysis.energy_calibration (+ science.xas.e0.find_e0, edges)",
                                              "Measured vs reference E0 as an angle correction.",
                                              "JSON: {measured_e0_ev, measured_minus_reference_ev, bragg_angle_correction_deg}"),
    "cherfd_check_gap_tracking": _data("science.cherfd.frames.gap_tracking + science.cherfd.beamline.harmonic_gap",
                                       "Recorded gap vs ideal harmonic gap.",
                                       "JSON: per sweep {implied_calibration_offset_mm, residual_rms_mm}"),
    "cherfd_export_merged": _data("science.cherfd.reduce.bin_sweeps -> spec_data.cherfd.export.write",
                                  "Writes twocol/SPEC files for the spec-file XAS tools "
                                  "(a file write, not a hardware action).",
                                  "JSON: {path, format, points}", ("cherfd_merge_sweeps",)),
    # ---- plots ------------------------------------------------------------
    "cherfd_plot_sweeps": _data("science.cherfd.reduce.bin_sweeps -> science.plots.cherfd.plot_sweeps",
                                "Sweep overlay plus merge.", "JSON + PNG"),
    "cherfd_plot_sweep_diagnostics": _data("spec_data.cherfd.sweeps.load_clean -> science.plots.cherfd.plot_diagnostics",
                                           "Four-panel single-sweep diagnostics.", "JSON + PNG"),
    # ---- logs -------------------------------------------------------------
    "cherfd_list_logs": _e("cherfd_claude log files by component.", "tools_cherfd.t_list_logs",
                           "JSON: {components, files}", "cherfd_logfile", "CHERFD_LOGS_DIR/*.log"),
    "cherfd_read_log": _e("Tail of one log file.", "tools_cherfd.t_read_log", "JSON: {file, lines}",
                          "cherfd_logfile", "CHERFD_LOGS_DIR/*.log", ["cherfd_list_logs"]),
    "cherfd_search_logs": _e("Regex over recent logs.", "tools_cherfd.t_search_logs", "JSON: {matches}",
                             "cherfd_logfile", "CHERFD_LOGS_DIR/*.log"),
}

