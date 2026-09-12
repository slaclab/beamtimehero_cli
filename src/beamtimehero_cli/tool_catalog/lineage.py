"""Per-tool lineage metadata for the /tools catalog page.

Every LLM-callable tool in :mod:`tool_catalog.definitions` has an entry
here. The metadata is
used by the ``/api/tools`` endpoint to render the "what the agent can
do" page with expanded descriptions, input/output shape, data source,
and cross-tool dependencies.

Schema per entry:

    long_description : str
        A few sentences elaborating on the one-line schema description.
    python_func : str
        The concrete Python call chain the executor performs. Shown in
        the UI so operators can trace a tool call to its implementation.
    spec_command : str | None
        The literal SPEC macro/command string (or multi-call chain) sent
        to the running SPEC session. ``None`` for tools that don't touch
        SPEC. Tools with a non-None value appear in the "SPEC-bound"
        section of the page.
    spec_commands : list[str], optional
        Only for the two handlers that choose their SPEC command at run
        time from an argument (``set_gain``, ``post_scan_move``): the
        registered command names they may issue. ``spec_command`` above
        is prose for the page; this is the machine-readable set, and
        ``tests/test_mutates.py`` requires it exactly when a handler's
        ``audited_call`` first argument is not a literal.
    mutates : bool
        Whether the tool mutates the beamline: it issues at least one
        SPEC command registered as ``action`` in ``spec_cmd``, so it
        requires a ``justification`` and is written to the action log
        before dispatch. This is the *declared* safety class and the
        only thing ``categorize()`` and consumers' write filters read —
        it is not inferred from the JSON schema.

        "Mutates" means that and nothing broader. A tool that writes a
        file (``write_summary``, ``write_macro``, ``save_plan``), a row
        (``record_energy_calibration``) or a Slack message
        (``post_slack_message``) changes state somewhere, and is still
        ``False``: none of them can move hardware, and none of them is
        audited as an action.
    output : str
        One-line description of what the tool returns.
    source : str
        Enum. Used to group tools visually and to colour the source
        badge. Values:
          * ``spec_datafile``  — reads a .dat SPEC file from BL_SCAN_DIR
          * ``spec_session``   — issues a command to the live SPEC session
          * ``spec_logfile``   — reads beamline control log files
          * ``spec_config``    — reads SPEC's config file
          * ``autonomy_db``    — reads/writes the autonomy SQLite DB
          * ``filesystem``     — non-SPEC files in the scan directory
          * ``tool_chain``     — consumes the output of another tool
          * ``postgres``       — reads the S3DF scan-metadata Postgres
          * ``camera``         — reads a beamline camera frame
          * ``slack``          — reads or posts to staff Slack
    source_detail : str
        Human-readable specifics about where the data comes from.
    depends_on : list[str]
        Other tools typically called first to obtain required arguments
        (e.g. ``list_scans`` before ``read_scan``). Empty when the tool
        has no prerequisite in the tool chain.
"""

from __future__ import annotations

TOOL_LINEAGE: dict[str, dict] = {

    # ---------- BeamtimeHero read-only tools (server/tools/definitions.py) ---

    "get_latest_scan": {
        "long_description": (
            "Return the most recently modified SPEC scan on disk, along "
            "with its metadata (file, scan number, command, counters) and "
            "a small numeric preview of the scan's points."
        ),
        "python_func": "spec_data.scans.list_processed_scans(limit=1) + read_processed_scan(...)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {file_name, scan_number, command, counters, data_preview}",
        "source": "spec_datafile",
        "source_detail": (
            "Reads .dat files under BL_SCAN_DIR via silx.io.specfile; "
            "metadata pulled from the sidecar .scan_metadata_cache.json."
        ),
        "depends_on": [],
    },
    "list_scans": {
        "long_description": (
            "Enumerate recent SPEC scans with metadata so the agent can "
            "pick a file_name/scan_number pair for follow-up tools."
        ),
        "python_func": "spec_data.scans.list_processed_scans(limit=20)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON array: [{file_name, scan_number, command, counters, npoints, timestamp}, ...]",
        "source": "spec_datafile",
        "source_detail": "SPEC file headers (#S, #D, #T, #L, #P) cached in .scan_metadata_cache.json.",
        "depends_on": [],
    },
    "read_scan": {
        "long_description": (
            "Return the full data array of a single scan, addressed by "
            "file_name + scan_number. Use list_scans first to discover the "
            "valid identifiers."
        ),
        "python_func": "spec_data.scans.get_scan_metadata(file_name, scan_number) + read_processed_scan(...)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {metadata, counters, data: {col: [values]}}",
        "source": "spec_datafile",
        "source_detail": "Parses the #S block of the SPEC file via silx.io.specfile.",
        "depends_on": ["list_scans"],
    },
    "get_latest_log_entries": {
        "long_description": (
            "Return the tail of the beamline control log. The agent uses "
            "this to see what SPEC just printed — prompts, warnings, the "
            "text of recent commands."
        ),
        "python_func": "spec_logs.log_reader.get_latest_log_entries(lines=100)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {log_file, lines: [str]}",
        "source": "spec_logfile",
        "source_detail": "Reads the newest file under BL_LOGS_DIR.",
        "depends_on": [],
    },
    "search_logs": {
        "long_description": (
            "Grep-like search across beamline control logs for a literal "
            "string (error message, motor name, macro name). Returns a "
            "bounded match list."
        ),
        "python_func": "spec_logs.log_reader.search_logs(query, max_results=50)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON array: [{log_file, line_number, line}, ...]",
        "source": "spec_logfile",
        "source_detail": "Scans all files under BL_LOGS_DIR via spec_logs.log_reader.",
        "depends_on": [],
    },
    "list_logs": {
        "long_description": (
            "List the available log files in BL_LOGS_DIR with their sizes "
            "and modification times."
        ),
        "python_func": "spec_logs.log_reader.list_logs(limit=20)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON array: [{name, size, mtime}, ...]",
        "source": "spec_logfile",
        "source_detail": "Directory listing of BL_LOGS_DIR.",
        "depends_on": [],
    },
    "get_active_counter": {
        "long_description": (
            "Pick the 'meaningful' counter for an energy scan. Heuristic: "
            "ppboff if it exists, else the vortDT/vortDT2/vortDT3/vortDT4 "
            "with the highest max count, else I1."
        ),
        "python_func": "spec_data.scans.get_active_counter(file_name, scan_number)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {counter: str, reason: str}",
        "source": "spec_datafile",
        "source_detail": "Reads per-point counter values from the SPEC scan.",
        "depends_on": ["list_scans"],
    },
    "get_scan_deadtime": {
        "long_description": (
            "Compute how much of a scan was acquisition vs overhead "
            "(motor moves, settling, comms). Useful when optimizing "
            "count time or diagnosing slow scans."
        ),
        "python_func": "spec_data.scans.get_scan_deadtime(file_name, scan_number)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {wall_s, acq_s, dead_s, dead_pct}",
        "source": "spec_datafile",
        "source_detail": "Uses per-point timestamps and #T header from the SPEC scan.",
        "depends_on": ["list_scans"],
    },
    "normalize_scan": {
        "long_description": (
            "Edge-step normalize an energy scan: divide the signal by I0 "
            "(or any chosen reference), then linearly rescale so the "
            "pre-edge reads 0 and the post-edge reads 1."
        ),
        "python_func": "spec_data.scans.edge_step_normalize_scan(file_name, scan_number, counter, normalize_by)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {x: [energy], y: [normalized], counter, normalize_by}",
        "source": "spec_datafile",
        "source_detail": "Reads counter + I0 arrays from the SPEC scan.",
        "depends_on": ["list_scans", "get_active_counter"],
    },
    "average_scans": {
        "long_description": (
            "Edge-step normalize every energy scan in a file, then average "
            "them. Returns the mean, the point-wise standard deviation, "
            "and the number of scans averaged."
        ),
        "python_func": "spec_data.scans.average_energy_scans(file_name)  |  average_latest_energy_scans()",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {x, mean, std, n_scans, file_name}",
        "source": "spec_datafile",
        "source_detail": "Iterates every #S block in the SPEC file.",
        "depends_on": ["list_scans"],
    },
    "analyze_convergence": {
        "long_description": (
            "Answer 'do I have enough scans?' via cosine similarity of "
            "each scan to the running mean, plus cumulative convergence "
            "and standard error."
        ),
        "python_func": "science.fitting.similarity.analyze_scan_quality (via spec_data.scans.get_normalized_scan_arrays)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {per_scan_similarity, cumulative, std_error, verdict}",
        "source": "spec_datafile",
        "source_detail": "Same scan set used by average_scans.",
        "depends_on": ["average_scans"],
    },
    "analyze_efficiency": {
        "long_description": (
            "Comprehensive scan-repetition efficiency report: "
            "convergence, coefficient of variation, comparison to the "
            "Poisson statistical limit, and a terminal verdict "
            "(needs_more / reasonable / marginal / wasteful)."
        ),
        "python_func": "science.statistics.efficiency.analyze_scan_efficiency (via spec_data.scans.get_normalized_scan_arrays)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {convergence, cv, poisson_ratio, recommended_n, verdict}",
        "source": "spec_datafile",
        "source_detail": "Superset of analyze_convergence.",
        "depends_on": ["analyze_convergence"],
    },
    "plot_scan": {
        "long_description": (
            "Render one scan as a PNG and return it to the user. Auto-"
            "detects the active counter; accepts an optional "
            "normalize_by counter."
        ),
        "python_func": "spec_data.plotting.plot_scan(file_name, scan_number, counter, normalize_by)",
        "spec_command": None,
        "mutates": False,
        "output": "Base64 PNG + a one-line caption",
        "source": "spec_datafile",
        "source_detail": "Reads the scan via silx, renders with matplotlib.",
        "depends_on": ["list_scans", "get_active_counter"],
    },
    "plot_averaged_scans": {
        "long_description": (
            "Edge-step normalize every scan in each given SPEC file, "
            "average, and overlay all samples on one plot with std-dev "
            "shading. The go-to cross-sample comparison plot."
        ),
        "python_func": "spec_data.plotting.plot_averaged_scans_overlay(file_names)",
        "spec_command": None,
        "mutates": False,
        "output": "Base64 PNG + a short text summary",
        "source": "spec_datafile",
        "source_detail": "Multiple SPEC files under BL_SCAN_DIR.",
        "depends_on": ["list_scans", "average_scans"],
    },
    "plot_data": {
        "long_description": (
            "General-purpose line plotter. The agent passes raw arrays — "
            "typically grabbed from read_scan or normalize_scan — and "
            "gets back a rendered PNG. Supports up to four overlaid series."
        ),
        "python_func": "matplotlib.pyplot (in-process, via spec_data.plotting)",
        "spec_command": None,
        "mutates": False,
        "output": "Base64 PNG + a one-line caption",
        "source": "tool_chain",
        "source_detail": "Pure rendering — x/y arrays come from other tools or the conversation.",
        "depends_on": ["read_scan", "normalize_scan"],
    },
    "list_files": {
        "long_description": (
            "List non-SPEC files (macros, configs, notes) in the scan "
            "directory so the agent can decide what to read or edit."
        ),
        "python_func": "spec_data.local_data.list_files(pattern)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON array: [{name, size, mtime}, ...]",
        "source": "filesystem",
        "source_detail": "Glob within BL_SCAN_DIR (excluding .dat SPEC files).",
        "depends_on": [],
    },
    "read_file": {
        "long_description": (
            "Read a text file from the scan directory — typically a .mac "
            "macro the agent wants to inspect or edit."
        ),
        "python_func": "spec_data.local_data.read_file(path)",
        "spec_command": None,
        "mutates": False,
        "output": "Raw text file contents",
        "source": "filesystem",
        "source_detail": "Arbitrary text file under BL_SCAN_DIR.",
        "depends_on": ["list_files"],
    },
    "write_summary": {
        "long_description": (
            "Persist a conversation summary into the scan directory as a "
            "timestamped .txt file. Used so operators can review agent "
            "reasoning offline."
        ),
        "python_func": "spec_data.local_data.write_file(beamtimehero_conversation_summary_<ts>.txt, content)",
        "spec_command": None,
        "mutates": False,
        "output": "Relative path of the written file",
        "source": "filesystem",
        "source_detail": "Writes into BL_SCAN_DIR.",
        "depends_on": [],
    },
    "write_macro": {
        "long_description": (
            "Save an edited SPEC macro under a new name with a "
            "_heroic_<date> suffix so the original macro is never "
            "overwritten."
        ),
        "python_func": "spec_data.local_data.write_file(<original>_heroic_<date>.mac, content)",
        "spec_command": None,
        "mutates": False,
        "output": "Relative path of the new .mac file",
        "source": "filesystem",
        "source_detail": "Writes into BL_SCAN_DIR alongside existing macros.",
        "depends_on": ["read_file"],
    },
    "save_plan": {
        "long_description": (
            "Persist a markdown plan into the project's logs/plans/ directory. "
            "Used at the start of multi-step tasks (typically beamline "
            "optimization) to record the step-by-step plan the agent "
            "intends to follow, so future sessions can review what was "
            "attempted and why. Filenames are validated against a strict "
            "allow-list and writes are confined to PLANS_DIR — no path "
            "traversal, no overwriting unless explicitly requested."
        ),
        "python_func": "PLANS_DIR.joinpath(filename).write_text(content)  (with regex + path-confinement checks)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {ok, path, bytes, overwrote}",
        "source": "filesystem",
        "source_detail": "Writes into PLANS_DIR (./logs/plans/ under the project root).",
        "depends_on": [],
    },
    "get_motor_config": {
        "long_description": (
            "Return SPEC's motor table: per-motor controller, steps/unit, "
            "slew rate, flags, and mnemonic. The motor index (MOTnnn) "
            "maps directly to the A[] array in SPEC."
        ),
        "python_func": "spec_data.spec_config.get_motor_config()",
        "spec_command": None,
        "mutates": False,
        "output": "Plain-text table (one row per motor)",
        "source": "spec_config",
        "source_detail": "Parses the SPEC config file on disk.",
        "depends_on": [],
    },
    "get_counter_config": {
        "long_description": (
            "Return SPEC's counter table: per-counter controller, unit, "
            "channel, scale, flags, and mnemonic. The counter index "
            "(CNTnnn) maps to the S[] array."
        ),
        "python_func": "spec_data.spec_config.get_counter_config()",
        "spec_command": None,
        "mutates": False,
        "output": "Plain-text table (one row per counter)",
        "source": "spec_config",
        "source_detail": "Parses the SPEC config file on disk.",
        "depends_on": [],
    },

    # ---------- Autonomy tools — CAT-0: procedures --------------------------

    "align_beamline": {
        "long_description": (
            "Run the full beamline alignment macro. Multi-minute: "
            "optimizes M1/M2, peaks mono pitch, aligns the mono slits, "
            "optimizes the B stage, zeros the pinhole, measures beam "
            "size. One-shot — refuses a re-run if the action_log shows "
            "it already succeeded this experiment."
        ),
        "python_func": "spec_cmd.call('align_beamline', [energy, xtal_chg, fine_x, fine_z], justification)",
        "spec_command": "align_the_beamline(<energy>, 0, <xtal_chg>, <fine_x>, <fine_z>)",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Writes action_log row before SPEC dispatch; blocks until SPEC prompt returns.",
        "depends_on": ["transition_phase"],
    },
    "align_xes_spectrometer": {
        "long_description": (
            "Align the 7-crystal HERFD analyzer via run_spec_align. "
            "One-shot per experiment. Crystals arg selects a subset "
            "(e.g. '1234' aligns only crystals 1–4)."
        ),
        "python_func": "spec_cmd.call('align_xes', [crystals, en_xes, en_mono], justification)",
        "spec_command": 'run_spec_align("<crystals>", <en_xes>, <en_mono>)',
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {crystals, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Gated to phase xes_alignment by the phase allow-list.",
        "depends_on": ["align_beamline", "transition_phase"],
    },
    "run_sample_alignment": {
        "long_description": (
            "Run auto_sample_align: Sz survey plus per-sample centering. "
            "Populates each sample's Sx/Sy/Sz in the plan."
        ),
        "python_func": "spec_cmd.call('auto_sample_align', [], justification)",
        "spec_command": "auto_sample_align",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Gated to phase sample_alignment.",
        "depends_on": ["align_xes_spectrometer"],
    },
    "run_collection": {
        "long_description": (
            "Multi-sample data-collection loop. Cycles through every "
            "enabled sample, producing one SPEC file per sample."
        ),
        "python_func": "spec_cmd.call('run_collection', [], justification)",
        "spec_command": "run_collection",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Gated to phase collection; emits SPEC data files into BL_SCAN_DIR.",
        "depends_on": ["run_sample_alignment"],
    },
    "select_element": {
        "long_description": (
            "Switch the beamline to the configured per-element geometry "
            "— sets energy, emission energy, Vortex ROI, and runs "
            "xes_setup."
        ),
        "python_func": "spec_cmd.call('select_element', [element], justification)",
        "spec_command": 'select_element("<element>")',
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {element, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Pulls the target geometry from the experiment plan.",
        "depends_on": ["get_plan"],
    },
    "peak_mono_pitch": {
        "long_description": (
            "LVDT-driven piezo optimization of the 2nd mono crystal "
            "pitch. Used as a fallback or pre-scan tune-up."
        ),
        "python_func": "spec_cmd.call('peak_mono_pitch', [], justification)",
        "spec_command": "peak_mono_pitch",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Short macro; typically runs in seconds.",
        "depends_on": [],
    },
    "calibrate_mono": {
        "long_description": (
            "Standard mono calibration: dscan energy ±15 eV over a "
            "reference foil, find the inflection, then call "
            "calibrate_mono + reset_gap. The tabulated edge energy must "
            "be within 5 eV of the current energy."
        ),
        "python_func": "spec_cmd.call('calibrate_mono', [tabulated_edge_ev], justification)",
        "spec_command": "calibrate_mono <tabulated_edge_ev>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {tabulated_ev, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Chained under the hood: dscan → find inflection → calibrate_mono → reset_gap.",
        "depends_on": ["run_motor_scan_relative"],
    },

    # ---------- Autonomy tools — CAT-1: motor control -----------------------

    "move_motor": {
        "long_description": (
            "Absolute motor move (SPEC's umv). Motor must be on the "
            "current phase's allow-list — e.g. during sample_alignment "
            "only Sx/Sy/Sz are movable."
        ),
        "python_func": "spec_cmd.call('umv', [motor, position], justification)",
        "spec_command": "umv <motor> <position>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {motor, target, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Synchronous — blocks until the motor reports done.",
        "depends_on": [],
    },
    "move_motor_relative": {
        "long_description": (
            "Relative motor move (SPEC's umvr) — shift a motor by a "
            "delta from its current position. Same phase allow-list as "
            "move_motor."
        ),
        "python_func": "spec_cmd.call('umvr', [motor, delta], justification)",
        "spec_command": "umvr <motor> <delta>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {motor, delta, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Synchronous motor move via the SPEC prompt.",
        "depends_on": ["read_motor_position"],
    },
    "read_motor_position": {
        "long_description": (
            "Read a single motor's position as a parsed float. Read-only "
            "— does not require a justification."
        ),
        "python_func": "spec_cmd.call('p_motor', [motor], justification='')",
        "spec_command": "wm <motor>",
        "mutates": False,
        "output": "JSON: {ok, kind, result: {value, raw}}",
        "source": "spec_session",
        "source_detail": "Read-only query; logs to query_log, not action_log.",
        "depends_on": [],
    },
    "read_all_positions": {
        "long_description": (
            "Read every motor's current position. Wraps SPEC's wa and "
            "parses the output into a {name → value} map."
        ),
        "python_func": "spec_cmd.call('wa', [], justification='')",
        "spec_command": "wa",
        "mutates": False,
        "output": "JSON: {ok, kind, result: {positions: {motor: value, ...}, raw}}",
        "source": "spec_session",
        "source_detail": "Read-only; logs to query_log.",
        "depends_on": [],
    },

    # ---------- Autonomy tools — CAT-2: scans -------------------------------

    "run_motor_scan": {
        "long_description": (
            "Absolute motor scan (ascan). Commonly used for alignment "
            "diagnostics where the motor absolute range matters."
        ),
        "python_func": "spec_cmd.call('ascan', [motor, start, end, npoints, count_time], justification)",
        "spec_command": "ascan <motor> <start> <end> <npoints> <count_time>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {motor, start, end, npoints, count_time, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Produces a new #S block in the current SPEC data file.",
        "depends_on": [],
    },
    "run_motor_scan_relative": {
        "long_description": (
            "Delta scan (dscan) centered on the motor's current "
            "position. Preferred for per-sample fine-tuning."
        ),
        "python_func": "spec_cmd.call('dscan', [motor, delta_start, delta_end, npoints, count_time], justification)",
        "spec_command": "dscan <motor> <delta_start> <delta_end> <npoints> <count_time>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {motor, delta_start, delta_end, npoints, count_time, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Produces a new #S block; leaves motor at end position (or peak/cen if followed up).",
        "depends_on": ["read_motor_position"],
    },
    "run_diagonal_scan": {
        "long_description": (
            "Two-motor diagonal scan (SPEC's d2scan) — both motors move "
            "in lockstep over the same delta range and number of points. "
            "Used during sample alignment to map a sample's footprint in "
            "the Sx/Sy plane (the d2scan in auto_sample_align's per-sample "
            "boundary detection). Default range is ±8 if delta_lo / "
            "delta_hi aren't provided. The dispatcher checks motor1 "
            "against the phase allow-list; the wrapper additionally "
            "validates motor2 before dispatch. NOTE: the `cen` "
            "scan-followup command does not work properly on a d2scan "
            "(2D scan) — do not rely on post_scan_move with mode='cen' "
            "after this scan; compute the center yourself and move "
            "explicitly instead."
        ),
        "python_func": (
            "spec_cmd.call('d2scan', "
            "[motor1, delta_lo, delta_hi, motor2, delta_lo, delta_hi, "
            "npoints, count_time], justification)"
        ),
        "spec_command": (
            "d2scan <motor1> <delta_lo> <delta_hi> "
            "<motor2> <delta_lo> <delta_hi> <npoints> <count_time>"
        ),
        "mutates": True,
        "output": (
            "JSON: {ok, kind, action_id, "
            "result: {motor1, delta_lo1, delta_hi1, motor2, delta_lo2, "
            "delta_hi2, npoints, count_time, raw, elapsed_s}, elapsed_s}"
        ),
        "source": "spec_session",
        "source_detail": "d2scan defined in standard.mac:1126 via _angle_scan_prep.",
        "depends_on": ["read_motor_position"],
    },
    "fit_emission_peak": {
        "long_description": (
            "Fit the most recent (or specified) emission scan with the "
            "lab's Pseudo-Voigt+skew model and return the suggested "
            "emission energy in eV. Wraps the SPEC `get_HERFD_energy` "
            "macro, which shells out to "
            "/usr/local/projects/HERFD_energy/get_HERFD_energy.py and "
            "prints `Suggested new emission value is <eV>`. Does NOT "
            "move the spectrometer — the agent decides whether/how to "
            "apply the value (e.g. via a follow-up emiss move)."
        ),
        "python_func": (
            "spec_cmd.call('get_HERFD_energy', [scan_number?], justification)"
        ),
        "spec_command": "get_HERFD_energy [<scan_number>]",
        "mutates": True,
        "output": (
            "JSON: {ok, kind, action_id, "
            "result: {emission_ev, raw, elapsed_s}, elapsed_s}"
        ),
        "source": "spec_session",
        "source_detail": (
            "Defined in get_HERFD_energy.mac:2; reads the active DATAFILE "
            "and the current PLOT_SEL counter, then runs the external "
            "fitter."
        ),
        "depends_on": ["run_motor_scan_relative"],
    },
    "run_xas": {
        "long_description": (
            "Calls the SPEC run_xas macro, which dispatches to <El>_xas "
            "for the element set by the prior select_element. "
            "Args: cntSec (null→1 s), nbrScan (null→1), emission "
            "(0 → emiss motor unchanged), nbrFilter (<0 → filter motor unchanged)."
        ),
        "python_func": "spec_cmd.call('run_xas', [count_time, n_reps, emission_ev, filter], justification)",
        "spec_command": "run_xas <cntSec> <nbrScan> <emission> <nbrFilter>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {count_time, n_reps, emission_ev, filter, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Element-side dispatch lives in run_xas.mac; each rep writes its own #S block.",
        "depends_on": ["get_beam_status", "select_element"],
    },
    "run_emiss_scan": {
        "long_description": (
            "Element-specific emission-energy scan (<element>_cee). "
            "Requires an emission_ev; filter is an optional 0-255 bitmask."
        ),
        "python_func": "spec_cmd.call('emiss_scan', [element, count_time, n_reps, emission_ev, filter], justification)",
        "spec_command": "<element>_cee <count_time> <n_reps> <emission_ev> <filter>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {element, count_time, n_reps, emission_ev, filter, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Similar guards to run_xas.",
        "depends_on": ["get_beam_status", "select_element"],
    },

    # ---------- Autonomy tools — CAT-3: beamline configuration --------------

    "mv_energy": {
        "long_description": (
            "Move incident energy. Does NOT enable tracking — if you "
            "want the ID gap to follow the mono, call `tracking 1` "
            "(not currently exposed as a tool) before invoking this, "
            "or use run_align_shortcut / a dedicated macro."
        ),
        "python_func": "spec_cmd.call('mv_energy', [energy_ev], justification)",
        "spec_command": "umv energy <energy_ev>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {target_ev, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Plain absolute-move on the energy motor; may block on gap ownership if tracking is already on.",
        "depends_on": ["request_gap_ownership"],
    },
    "shutter": {
        "long_description": (
            "Fast-shutter control. fsopen/fsclose toggle the shutter; "
            "fson/fsoff enable/disable automatic shuttering; optional "
            "delay_s for timed opens."
        ),
        "python_func": "spec_cmd.call('shutter', [command, delay_s?], justification)",
        "spec_command": "<fsopen|fsclose|fson|fsoff> [<delay_s>]",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {command, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Immediate — no scan context required.",
        "depends_on": [],
    },
    "set_filter": {
        "long_description": (
            "Set the filter motor to a 0-255 bitmask (each bit is one "
            "filter pad). Used to attenuate the beam before high-flux "
            "scans."
        ),
        "python_func": "spec_cmd.call('mv', ['filter', bitmask], justification)",
        "spec_command": "mv filter <bitmask>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {motor, target, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Internally a motor move.",
        "depends_on": [],
    },
    "safely_remove_filters": {
        "long_description": (
            "Ramp filters out via the XRS-safe macro — avoids the "
            "sample-damage risk of pulling all attenuators at once."
        ),
        "python_func": "spec_cmd.call('safely_remove_filters', [], justification)",
        "spec_command": "safely_remove_filters",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Multi-second; issues a stepped set of filter moves.",
        "depends_on": [],
    },
    "set_gain": {
        "long_description": (
            "Set the SRS current amplifier gain on I0/I1/I2. Accepts a "
            "string setting (e.g. '50 nA/V')."
        ),
        "python_func": "spec_cmd.call('set_i0_gain' | 'set_i1_gain' | 'set_i2_gain', [gain_setting], justification)",
        "spec_command": "set_i0_gain | set_i1_gain | set_i2_gain <setting>",
        "spec_commands": ["set_i0_gain", "set_i1_gain", "set_i2_gain"],
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {gain, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "The macro chosen depends on the 'which' arg (i0|i1|i2).",
        "depends_on": [],
    },
    "set_vortex_roi": {
        "long_description": (
            "Set the Vortex ROI. mode='auto': bounds ±200 eV around the emission "
            "line for channel (1=vortDT, 3=vortDT2 for a second emission peak). "
            "mode='explicit': set channel + lo_ev/hi_ev in eV directly."
        ),
        "python_func": "spec_cmd.call('set_vortex_roi', [args...], justification)",
        "spec_command": "vortex_roi auto <channel>  |  vortex_roi <channel> <lo_ev> <hi_ev>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {args, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Shapes the fluorescence window around the expected emission line.",
        "depends_on": [],
    },
    "open_data_file": {
        "long_description": (
            "Start a new SPEC data file (newfile). Used per-sample so "
            "each sample's data lives in its own .dat."
        ),
        "python_func": "spec_cmd.call('newfile', [filename], justification)",
        "spec_command": "newfile <filename>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {filename, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Subsequent scans write into this file until a new one is opened.",
        "depends_on": [],
    },

    "plotselect": {
        "long_description": (
            "Select which counter SPEC uses for plotting during scans. "
            "Use I1 for alignment optimization (downstream signal), "
            "vortDT for fluorescence, I0 for upstream flux monitoring. "
            "Does not affect data collection — only the live plot display."
        ),
        "python_func": "spec_cmd.call('plotselect', [counter], justification)",
        "spec_command": "plotselect <counter>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {counter, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Instantaneous SPEC command; no scan context required.",
        "depends_on": [],
    },

    # ---------- Autonomy tools — CAT-4: alignment fallbacks -----------------

    "run_align_shortcut": {
        "long_description": (
            "Run one of the named diagnostic shortcuts (vvv, hhh, m1m1, "
            "m2m2, ggg, bzbz, bxbx, dmm, beamx, beamz, cm1m1, cm2m2, "
            "beamx_fine, beamz_fine). Each is a single dscan + "
            "post-analysis."
        ),
        "python_func": "spec_cmd.call('run_shortcut', [name], justification)",
        "spec_command": "<shortcut_name>  (one of vvv|hhh|m1m1|m2m2|ggg|bzbz|bxbx|dmm|beamx|beamz|cm1m1|cm2m2)",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {name, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Rejected if 'name' is not in the hard allow-list.",
        "depends_on": [],
    },
    "post_scan_move": {
        "long_description": (
            "After a scan, move the motor to the detected feature: "
            "'cen' (center) or 'peak'. Run after a dscan/ascan, once "
            "the scan has been plotted and inspected (per agent-"
            "instructions §5), to land on the best point."
        ),
        "python_func": "spec_cmd.call('cen' | 'peak', [], justification)",
        "spec_command": "cen | peak",
        "spec_commands": ["cen", "peak"],
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Uses SPEC's built-in CEN/PEAK detection on the last scan.",
        "depends_on": ["run_motor_scan", "run_motor_scan_relative"],
    },

    # ---------- Autonomy tools — CAT-5: beam-diagnostic tool ----------------

    "mv_pinhole": {
        "long_description": (
            "Move the sample stage so the diagnostic-tool pinhole is in "
            "the beam. Sx/Sy/Sz/Sr are driven to the pinhole pose, plus "
            "any active pinhole_offset."
        ),
        "python_func": "spec_cmd.call('mvpinhole', [], justification)",
        "spec_command": "mvpinhole",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac via rdef; pose updates whenever update_pinhole_pos is called.",
        "depends_on": [],
    },
    "mv_plastic": {
        "long_description": (
            "Move the sample stage so the diagnostic-tool plastic "
            "scatterer is in the beam. Used to generate elastic scatter "
            "for XES spectrometer alignment."
        ),
        "python_func": "spec_cmd.call('mvplastic', [], justification)",
        "spec_command": "mvplastic",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac via rdef.",
        "depends_on": [],
    },
    "mv_knife_clear": {
        "long_description": (
            "Move the sample stage so the knife-edge blades are clear of "
            "the beam. Fast move, but the diagnostic body may still "
            "partially clip the beam to I1 — prefer mv_knife_out before "
            "trusting I1 for upstream-optic alignment."
        ),
        "python_func": "spec_cmd.call('mvknifeclear', [], justification)",
        "spec_command": "mvknifeclear",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac via rdef.",
        "depends_on": [],
    },
    "mv_knife_out": {
        "long_description": (
            "Move the sample stage so the entire diagnostic tool is "
            "fully out of the beam path. Slower than mv_knife_clear "
            "(large Sr rotation) but unambiguous: nothing diagnostic-"
            "related is in the beam."
        ),
        "python_func": "spec_cmd.call('mvknifewayout', [], justification)",
        "spec_command": "mvknifewayout",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac via rdef; sets Sr=80 so the full diagnostic body clears the beam.",
        "depends_on": [],
    },
    "measure_beam_size": {
        "long_description": (
            "Knife-edge scan to measure horizontal and vertical beam "
            "FWHM. Multi-minute. Removes filters, ensures DATAFILE="
            "alignment, runs centered knife-edge scans on Sx and Sz, "
            "stores results in the global beamsize[] array. Each axis "
            "supports 'big' (large mm-scale beam) or 'small' (~50um "
            "focused) modes — wrong mode produces artifacts."
        ),
        "python_func": "spec_cmd.call('measure_beam_size', [mode_x, mode_z], justification)",
        "spec_command": "measure_beam_size <mode_x> <mode_z>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {mode_x, mode_z, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac:250; chains center_knife_edge_x/z + beamx/beamz scans.",
        "depends_on": [],
    },
    "zero_pinhole": {
        "long_description": (
            "Center the beam on the diagnostic-tool pinhole, then zero "
            "(or apply the configured pinhole_offset to) Tz/Sz/Bz/Tx/Sx/"
            "Bx. Multi-minute. Refuses to run if the table is not in "
            "its usual position (Tz < 15.5 with no offset configured)."
        ),
        "python_func": "spec_cmd.call('zero_pinhole', [], justification)",
        "spec_command": "zero_pinhole",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac:467; chains find_pinhole + center_on_pinhole + table/sample/B-stage zeroing.",
        "depends_on": [],
    },
    "small_beam": {
        "long_description": (
            "Set the KB-mirror benders to the small-beam (~50um focused) "
            "preset. Moves m1ubend/m1dbend/m2ubend/m2dbend to the "
            "configured small-beam values and tags both beamsize_mode "
            "axes as 'small'."
        ),
        "python_func": "spec_cmd.call('smallbeam', [], justification)",
        "spec_command": "smallbeam",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, mode: 'small', elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac:68.",
        "depends_on": [],
    },
    "big_beam": {
        "long_description": (
            "Set the KB-mirror benders to the big-beam (mm-scale, "
            "standard) preset. Moves m1ubend/m1dbend/m2ubend/"
            "m2dbend to the configured big-beam values and tags both "
            "beamsize_mode axes as 'big'."
        ),
        "python_func": "spec_cmd.call('bigbeam', [], justification)",
        "spec_command": "bigbeam",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, mode: 'big', elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac:78.",
        "depends_on": [],
    },
    "xtal_align": {
        "long_description": (
            "Recalibrate the crystal-motor encoder zero. Runs a dscan "
            "over the crystal motor, peaks on the diffraction feature, "
            "then redefines the current encoder reading to the original "
            "(pre-scan) value. The motor stays in place; its zero is "
            "now anchored on the peak. Used after a crystal swap or "
            "when the crystal feature has drifted."
        ),
        "python_func": "spec_cmd.call('xtalalign', [], justification)",
        "spec_command": "xtalalign",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac:87; valid_dscan crystal + peak + set crystal <old>.",
        "depends_on": [],
    },
    "reset_gap": {
        "long_description": (
            "Recalibrate the undulator gap encoder zero. Runs ggg (gap "
            "dscan), peaks on the flux maximum, then redefines the gap "
            "encoder so the original (pre-scan) reading is preserved on "
            "the new peak. Run ONCE at the end of an energy-calibration "
            "sequence -- iterating reset_gap during calibration fights "
            "the calibrate_mono loop."
        ),
        "python_func": "spec_cmd.call('reset_gap', [], justification)",
        "spec_command": "reset_gap",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac:95; ggg + peak + set gap <old>.",
        "depends_on": [],
    },
    "set_m2_stripe": {
        "long_description": (
            "Move M2 (m2vert) to the appropriate stripe for the supplied "
            "incident energy. Branches: <4500 eV -> Rh (with a warning), "
            "4500-6200 eV -> Si (m2vert=9.69), >=6200 eV -> Rh "
            "(m2vert=-3.5). Use whenever the incident energy crosses a "
            "stripe boundary."
        ),
        "python_func": "spec_cmd.call('m2_stripe', [energy_ev], justification)",
        "spec_command": "m2_stripe",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {energy_ev, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in beam_diagnostics.mac:103; dispatches to m2_Rh or m2_Si (umv m2vert).",
        "depends_on": [],
    },
    "get_anchor": {
        "long_description": (
            "Read the current tracking-anchor state from the SPEC "
            "session: energy + m1vert + Tz at anchor time, the "
            "constituent m1vert1/m1vert2/Tz1/Tz2 motor positions, the "
            "crystal id, and the SPEAR steering offset stored at "
            "anchor time. Also reports whether SPEAR has visibly "
            "drifted (monvtra moved since anchoring) and whether the "
            "crystal set has changed (which makes the anchor invalid "
            "for the current geometry). Read-only — no justification "
            "required."
        ),
        "python_func": "spec_cmd.call('get_anchor', [], justification='')",
        "spec_command": "get_anchor",
        "mutates": False,
        "output": (
            "JSON: {ok, kind, result: {energy, m1vert, m1vert1, m1vert2, "
            "Tz, Tz1, Tz2, crystal, spear_steering, spear_drift, "
            "crystal_changed, raw}}"
        ),
        "source": "spec_session",
        "source_detail": "Defined in tracking.mac:249.",
        "depends_on": [],
    },
    "set_anchor": {
        "long_description": (
            "Capture mono/m1vert/m1vert1/m1vert2/Tz/Tz1/Tz2 (plus "
            "monvtra for SPEAR steering) as the tracking-anchor "
            "reference, and persist to anchor.cfg + a timestamped "
            "backup. The anchor is the fixed beam-position pivot that "
            "energy-tracking uses to keep the focused beam on the "
            "sample as the mono Bragg angle changes. Call once the "
            "beam is aligned at a known reference energy."
        ),
        "python_func": "spec_cmd.call('set_anchor', [], justification)",
        "spec_command": "set_anchor",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in tracking.mac:268; calls save_anchor → /usr/local/lib/spec.d/anchor.cfg + ANCHOR_DIR backup.",
        "depends_on": [],
    },
    "tracking": {
        "long_description": (
            "Toggle energy-tracking on (1) or off (0). With tracking "
            "enabled, every energy move also drives m1vert and Tz so "
            "the focused beam stays at the anchor position as the mono "
            "Bragg angle changes. Requires set_anchor first -- without "
            "an anchor, tracking has no reference and the beam will "
            "drift. Disable before procedures that need m1vert/Tz "
            "free of automatic motion (e.g. KB-mirror alignment)."
        ),
        "python_func": "spec_cmd.call('tracking', ['0'|'1'], justification)",
        "spec_command": "tracking <0|1>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {enabled, raw, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Defined in tracking.mac:139; flips global _TRACKING which gates the _track() hook on energy moves.",
        "depends_on": ["set_anchor"],
    },

    # ---------- Autonomy tools — CAT-6: beam monitoring ---------------------

    "get_beam_size": {
        "long_description": (
            "Return the last-measured horizontal and vertical beam FWHM "
            "(mm) and the current beam-size mode (big/small/unknown) for "
            "each axis. Read-only — reads SPEC globals set by "
            "measure_beam_size / beamx / beamz."
        ),
        "python_func": "spec_cmd.call('wbeamsize', [], justification='')",
        "spec_command": "wbeamsize",
        "mutates": False,
        "output": "JSON: {ok, kind, result: {horizontal_fwhm_mm, vertical_fwhm_mm, horizontal_mode, vertical_mode, raw}}",
        "source": "spec_session",
        "source_detail": "Read-only. Defined in beam_diagnostics.mac; prints beamsize[] and beamsize_mode[] globals.",
        "depends_on": [],
    },
    "get_beam_status": {
        "long_description": (
            "Compact snapshot of whether the beam is usable: SPEAR ring "
            "current, BL15 shutter state, gap ownership, and a "
            "beam_good boolean."
        ),
        "python_func": "spec_cmd.call('beam_status', [], justification='')",
        "spec_command": "p beam_status()",
        "mutates": False,
        "output": "JSON: {ok, kind, result: {spear_current_ma, beamline_state, gap_owned, beam_good, reason, raw}}",
        "source": "spec_session",
        "source_detail": "Read-only. The SPEC side is a custom function (spec.d/check_beam.mac) that prints an associative array of SPEAR/BL15/gap state.",
        "depends_on": [],
    },
    "get_counts": {
        "long_description": (
            "Count for the specified time (default 0.5 s) and return all "
            "counter values as a {name: value} map."
        ),
        "python_func": "spec_cmd.call('ct', [count_time], justification='')",
        "spec_command": "ct <count_time>",
        "mutates": False,
        "output": "JSON: {ok, kind, result: {counters: {name: value, ...}, raw}}",
        "source": "spec_session",
        "source_detail": "Read-only; logs to query_log.",
        "depends_on": [],
    },
    "get_counter": {
        "long_description": (
            "Count for the specified time (default 0.5 s) and return one "
            "specific counter's value. Runs ct and extracts the named counter."
        ),
        "python_func": "spec_cmd.call('ct', [count_time], justification='') → filter by counter name",
        "spec_command": "ct <count_time>",
        "mutates": False,
        "output": "JSON: {ok, kind, result: {value, counter, raw}}",
        "source": "spec_session",
        "source_detail": "Read-only; logs to query_log.",
        "depends_on": ["get_counts"],
    },
    "request_gap_ownership": {
        "long_description": (
            "Blocking gaprequest — returns when SPEAR grants BL15 "
            "ownership of the ID gap, or when it times out."
        ),
        "python_func": "spec_cmd.call('gaprequest', [], justification)",
        "spec_command": "gaprequest",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id, result: {raw, granted, elapsed_s}, elapsed_s}",
        "source": "spec_session",
        "source_detail": "Required before mv_energy when SPEAR is the gap owner.",
        "depends_on": [],
    },

    # ---------- Autonomy tools — CAT-7: run state ---------------------------

    "get_element": {
        "long_description": (
            "Return the value of the SPEC variable ELEMENT — the "
            "currently active element for the experiment."
        ),
        "python_func": "spec_cmd.call('p_element', [], justification='')",
        "spec_command": "p ELEMENT",
        "mutates": False,
        "output": "JSON: {ok, kind, result: {element, raw}}",
        "source": "spec_session",
        "source_detail": "Read-only. Prints the ELEMENT global variable.",
        "depends_on": [],
    },
    "get_scan_number": {
        "long_description": (
            "Return SPEC's current SCAN_N counter. Use get_current_"
            "datafile separately if you also need the active file name."
        ),
        "python_func": "spec_cmd.call('scan_n', [], justification='')",
        "spec_command": "p SCAN_N",
        "mutates": False,
        "output": "JSON: {ok, kind, result: {value, raw}}",
        "source": "spec_session",
        "source_detail": "Read-only.",
        "depends_on": [],
    },
    "get_current_datafile": {
        "long_description": (
            "Returns the active SPEC data file path via the DATAFILE global."
        ),
        "python_func": "spec_cmd.call('p_datafile', [], justification='')",
        "spec_command": "p DATAFILE",
        "mutates": False,
        "output": "JSON: {ok, kind, result: {raw}}",
        "source": "spec_session",
        "source_detail": "Read-only.",
        "depends_on": [],
    },
    "get_plotselected_counter": {
        "long_description": (
            "Return the currently plot-selected counter mnemonic — the "
            "counter peak/cen will operate on after a scan, set by the "
            "most recent plotselect call. Resolves SPEC's DET global "
            "via cnt_mne(DET). Use after select_element or plotselect "
            "to confirm SPEC matches the counter the experiment config "
            "expects for this element."
        ),
        "python_func": "spec_cmd.call('plotselected', [], justification='')",
        "spec_command": "p cnt_mne(DET)",
        "mutates": False,
        "output": "JSON: {ok, kind, result: {counter, raw}}",
        "source": "spec_session",
        "source_detail": "Read-only.",
        "depends_on": [],
    },
    "abort_current_scan": {
        "long_description": (
            "Send Ctrl-C to SPEC to abort whatever is running. Only "
            "call this after confirming a real problem — aborts are "
            "expensive and may leave hardware in a half-state."
        ),
        "python_func": "spec_cmd.call('abort', [], justification)",
        "spec_command": "<Ctrl-C>",
        "mutates": True,
        "output": "JSON: {ok, kind, action_id}",
        "source": "spec_session",
        "source_detail": "Writes to action_log before sending the interrupt.",
        "depends_on": [],
    },

    # ---------- Autonomy tools — CAT-8: orchestration (no SPEC) -------------

    "recent_actions": {
        "long_description": (
            "Most recent action_log entries for the current experiment "
            "— every SPEC-mutating tool call appears here. Also used "
            "by the phase-transition gate to verify prior success."
        ),
        "python_func": "action_log.db.recent_actions(limit, experiment_id)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON array: [{id, timestamp, phase, command, justification, success}, ...]",
        "source": "autonomy_db",
        "source_detail": "Every spec_cmd.call() writes an action_log row.",
        "depends_on": [],
    },

    # ---------- Sandbox evaluation -----------------------------------------

    "evaluate_spec_macro": {
        "long_description": (
            "Run a SPEC macro in a disposable, network-isolated Docker "
            "container and return the execution log. Use to validate "
            "macros you authored or edited, or to reproduce a SPEC error "
            "in isolation. Each call is a cold start with no state from "
            "prior runs. The container has read-only access to production "
            "beamline macros but no network and no host devices — it "
            "cannot affect the live beamline. Always read the log even on "
            "ok=True; SPEC sometimes exits 0 despite warnings."
        ),
        "python_func": "spec_eval.evaluate_spec_macro(macro, preload, timeout_s)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {ok, exit_code, timed_out, log, duration_s, run_id, error}",
        "source": "tool_chain",
        "source_detail": (
            "POSTs to the spec-eval API (default http://127.0.0.1:5005) "
            "which runs the macro in a disposable Docker container with "
            "sim-mode SPEC. Override URL via SPEC_EVAL_URL env var."
        ),
        "depends_on": [],
    },

    # ---------- Observation ---------------------------------------------------

    "capture_sample_image": {
        "long_description": (
            "Capture a low-resolution JPEG snapshot from the RPi-Cam "
            "sample camera. Returns image metadata as text and the raw "
            "JPEG as an inline base64 image. Also logs the JPEG to "
            "DATA_DIR/camera_log/ for audit. Useful for visual "
            "inspection of sample position, beam spot location, or "
            "cryostat window state."
        ),
        "python_func": (
            "requests.get(f'http://{SAMPLE_CAM_HOST}:{SAMPLE_CAM_PORT}"
            "/snapshot.jpg', params={'resolution': 'low', 'quality': q})"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {ok, resolution, quality, size_bytes} + inline JPEG image",
        "source": "camera",
        "source_detail": (
            "HTTP GET to the RPi-Cam snapshot endpoint on the beamline "
            "network (default 192.168.150.93:8080). Returns the latest "
            "live video frame at 1600x1200. Override via SAMPLE_CAM_HOST "
            "and SAMPLE_CAM_PORT env vars."
        ),
        "depends_on": [],
    },
    "get_reference_image": {
        "long_description": (
            "Return a reference image for a known sample environment or "
            "diagnostic tool. The image is read from version-controlled "
            "package data and returned inline as base64 JPEG. Compare "
            "the result with a live capture_sample_image snapshot to "
            "confirm what is currently mounted on the sample stage."
        ),
        "python_func": (
            "Path(reference_images / manifest[kind]['file']).read_bytes()"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {ok, kind, description, size_bytes} + inline JPEG image",
        "source": "filesystem",
        "source_detail": (
            "Reads from beamtimehero_cli/reference_images/ — "
            "version-controlled JPEG files registered in manifest.json."
        ),
        "depends_on": [],
    },

    # ---------- Cross-file XAS comparison (LCF / registration / diffs) ----

    "compare_xas_to_references": {
        "long_description": (
            "XANES linear-combination fitting: non-negative least-squares "
            "fit of a target spectrum against measured reference spectra, "
            "returning speciation fractions with fit quality and a "
            "target/fit/residual plot. Per-file E0s are reported so a "
            "calibration offset between files is visible before the "
            "fractions are trusted. Accepts SPEC files, collector groups, "
            "and merged two-column ASCII spectra."
        ),
        "python_func": (
            "tool_catalog.tools_core._load_spectrum_entries(...) + "
            "science.xas.compare.compare_to_references(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {components[{name,fraction}], fit_r2, residual_rms, reference_e0s_ev, ...} + fit plot",
        "source": "spec_datafile",
        "source_detail": (
            "Loads every spectrum via spec_data.scans.get_normalized_scan_arrays "
            "(SPEC cache, collector ASCII, or merged two-column ASCII)."
        ),
        "depends_on": ["list_files", "align_spectra"],
    },
    "align_spectra": {
        "long_description": (
            "Cross-file energy registration check: fit each spectrum's E0 "
            "(derivative max), report the shift onto a common target E0, "
            "and return a before/after overlay. Shifts beyond ±10 eV are "
            "refused as glitch-latched. Report-only — nothing is written."
        ),
        "python_func": (
            "science.xas.compare.align_spectra(...) over "
            "spec_data.scans.get_normalized_scan_arrays(...) loads"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: per-file {e0_before, shift_applied, e0_after, refused} + two-panel overlay plot",
        "source": "spec_datafile",
        "source_detail": (
            "Loads every spectrum via spec_data.scans.get_normalized_scan_arrays "
            "(SPEC cache, collector ASCII, or merged two-column ASCII)."
        ),
        "depends_on": ["list_scans", "list_files"],
    },
    "difference_spectrum": {
        "long_description": (
            "A − B difference spectrum on a common interpolated grid, "
            "E0-aligned by default so a calibration offset does not "
            "masquerade as chemistry. Reports max |Δ| and its energy plus "
            "RMS Δ, with a two-panel overlay/difference plot."
        ),
        "python_func": (
            "science.xas.compare.difference_spectrum(...) over "
            "spec_data.scans.get_normalized_scan_arrays(...) loads"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {max_abs_delta, energy_of_max_abs_delta_ev, rms_delta, alignment, ...} + two-panel plot",
        "source": "spec_datafile",
        "source_detail": (
            "Loads both spectra via spec_data.scans.get_normalized_scan_arrays "
            "(SPEC cache, collector ASCII, or merged two-column ASCII)."
        ),
        "depends_on": ["align_spectra"],
    },

    # ---------- XANES / HERFD: the atomized descriptor + interpretation leaves

    "identify_edge": {
        "long_description": (
            "Answer 'what edge is this' before anything else runs: infer the "
            "absorber element and edge from the scan's energy window against "
            "the tabulated edge energies, then classify the interpretation "
            "family (3d/4d/5d K, Ln/An L3, 5d L3, An M4/M5) that decides which "
            "oxidation-state basis is even applicable. Labels only — no "
            "absolute-energy claim is made, since that needs a session "
            "calibration."
        ),
        "python_func": (
            "science.xas.policy.resolve_edge(...) over "
            "spec_data.scans.get_normalized_scan_arrays(...); family from "
            "science.tables.edges.classify_edge_family"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {element, edge, edge_energy_ev, family, detected|explicit, energy_window_ev, ambiguity}",
        "source": "spec_datafile",
        "source_detail": (
            "Scan energy range via spec_data.scans.get_normalized_scan_arrays; "
            "edge table from science.tables.edges (xraydb-backed)."
        ),
        "depends_on": ["list_scans"],
    },
    "find_edge_e0": {
        "long_description": (
            "Locate the edge position of the averaged spectrum under two fixed "
            "definitions: the Savitzky-Golay derivative maximum (primary, with "
            "an uncertainty) and the half-step crossing (cross-check only). "
            "Single responsibility — it finds the edge and nothing else. The "
            "value is only as absolute as the session calibration; without one "
            "it is a relative marker."
        ),
        "python_func": "science.xas.e0.find_e0(...) over spec_data.scans.get_normalized_scan_arrays(...)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {e0_ev, e0_unc_ev, e0_half_step_ev, definition, grid_step_ev, element, edge}",
        "source": "spec_datafile",
        "source_detail": "Averaged, I0-divided reps from spec_data.scans.get_normalized_scan_arrays.",
        "depends_on": ["list_scans", "identify_edge"],
    },
    "normalize_xas_intensity": {
        "long_description": (
            "Normalize the averaged spectrum and report only how it was done — "
            "method, window, scale factor, and the reason if a method degraded. "
            "'area' (the HERFD default, Bugarin & Glatzel) is preferred for "
            "intensity comparisons; 'mback' is the physics-based background; "
            "'edge_step' is the upstream flat-anchored variant. Provenance-only "
            "by design, so a downstream intensity number can always be traced "
            "to the normalization that produced it."
        ),
        "python_func": (
            "science.xas.normalize.{area_normalize,mback_normalize,edge_step_provenance}(...) "
            "over spec_data.scans.get_normalized_scan_arrays(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {method, citation, window_ev, scale, degraded, reason, element, edge}",
        "source": "spec_datafile",
        "source_detail": "Averaged, I0-divided reps from spec_data.scans.get_normalized_scan_arrays.",
        "depends_on": ["find_edge_e0"],
    },
    "fit_xas_pre_edge": {
        "long_description": (
            "Wilke-style pre-edge fit of the averaged spectrum: a rising-edge "
            "atan baseline plus one to three pseudo-Voigts, returning centroid, "
            "integrated intensity, component count and BIC/fit quality with "
            "uncertainties. For 3d/4d/5d K-edges with a tabulated core-hole "
            "width the re-broadened variant is returned too — that, not the "
            "sharp HERFD fit, is the valid input to a conventional-XANES "
            "calibration such as Wilke 2001."
        ),
        "python_func": (
            "science.xas.fits.fit_pre_edge(...) + science.xas.e0.rebroaden(...) over "
            "spec_data.scans.get_normalized_scan_arrays(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {centroid_ev, integrated_intensity, n_components, bic, fit_ok, rebroadened{...}, provenance}",
        "source": "spec_datafile",
        "source_detail": (
            "Averaged, I0-divided reps from spec_data.scans.get_normalized_scan_arrays; "
            "core-hole widths from science.tables.edge_shifts."
        ),
        "depends_on": ["find_edge_e0", "normalize_xas_intensity"],
    },
    "fit_xas_white_line": {
        "long_description": (
            "White-line fit of the averaged spectrum: energy, height and area "
            "of the main line (selected by height) plus every fitted component. "
            "Component count defaults from the edge family — three for Ln/An L3 "
            "and An M edges, where the Ce(IV) doublet and U(VI) satellites are "
            "real structure rather than noise, one otherwise."
        ),
        "python_func": (
            "science.xas.fits.fit_white_line(...) with the count from "
            "science.xas.policy.white_line_components_for(family)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {white_line{energy_ev, height, area}, components[...], fit_ok, provenance}",
        "source": "spec_datafile",
        "source_detail": "Averaged, I0-divided reps from spec_data.scans.get_normalized_scan_arrays.",
        "depends_on": ["find_edge_e0", "normalize_xas_intensity"],
    },
    "extract_xas_descriptors": {
        "long_description": (
            "The full descriptor bundle in one call — E0 under both "
            "definitions, the white-line fit, the Wilke-style pre-edge fit, "
            "per-scan descriptor trends, and the data-quality flags — plus an "
            "annotated figure that plots exactly the numbers the verdicts will "
            "use, so the interpretation can be audited at a glance. This is the "
            "artifact the interpret_* tools consume; pass it back to them to "
            "avoid recomputing."
        ),
        "python_func": (
            "science.xas.descriptors.extract_descriptors(...) + "
            "science.plots.xas.annotated_descriptor_figure(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {e0, white_line, pre_edge, per_scan_trends, quality_flags, provenance} + annotated figure",
        "source": "spec_datafile",
        "source_detail": "Averaged, I0-divided reps from spec_data.scans.get_normalized_scan_arrays.",
        "depends_on": ["identify_edge"],
    },
    "assess_xas_quality": {
        "long_description": (
            "Data-quality gate for the averaged spectrum: monochromator-glitch "
            "spike count, detector-saturation (flat-top white line) check, and "
            "an honest self-absorption risk statement. The XAS analogue of "
            "assess_xrs_quality. Self-absorption cannot be ruled out from the "
            "data alone for fluorescence-detected HERFD at unknown "
            "concentration, so intensity verdicts stay degraded until "
            "assume_dilute is asserted by the operator."
        ),
        "python_func": (
            "science.reduce.artifacts.{detect_glitches,detect_saturation,"
            "self_absorption_assessment}(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {n_glitches, saturated, n_pinned, self_absorption_risk, quality_flags, verdict}",
        "source": "spec_datafile",
        "source_detail": "Averaged, I0-divided reps from spec_data.scans.get_normalized_scan_arrays.",
        "depends_on": ["identify_edge"],
    },
    "detect_per_scan_drift": {
        "long_description": (
            "Beam-damage and photoreduction test: per-scan trends in E0, "
            "white-line height and energy, and pre-edge intensity, each with a "
            "monotonic-drift verdict (Kendall tau p<0.05 and |Theil-Sen total "
            "change| above twice the residual MAD). Catches the steady "
            "one-directional walk that a first-half/second-half split averages "
            "away. Needs at least four scans to say anything."
        ),
        "python_func": (
            "science.xas.descriptors.per_scan_descriptor_trends(...) over "
            "per-scan science.xas.{e0,fits} fits"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: per-descriptor {theil_sen_slope, kendall_tau, p_value, total_change, drifting}",
        "source": "spec_datafile",
        "source_detail": "One fit per scan from spec_data.scans.get_normalized_scan_arrays.",
        "depends_on": ["identify_edge"],
    },
    "interpret_oxidation_state": {
        "long_description": (
            "Oxidation-state verdict on the basis the edge family actually "
            "supports: 3d K-edges via the pre-edge centroid on the Wilke 2001 "
            "CII axis (applied to the re-broadened spectrum) plus the "
            "calibrated edge shift; Ce L3 via the Ce(IV) final-state doublet; "
            "U M4 via the peak-position/satellite method. Refuses an absolute "
            "state without a session calibration and reports a relative "
            "comparison instead, and degrades confidence when a quality flag "
            "or a calibration-domain mismatch stands."
        ),
        "python_func": "science.xas.interpret.interpret_oxidation_state(descriptors, calibration)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {verdict, basis, confidence, evidence, caveats, flags, calibration_context}",
        "source": "spec_datafile",
        "source_detail": (
            "Descriptors from the file (or a precomputed descriptors artifact, "
            "which skips the reload); calibration from calibration_store."
        ),
        "depends_on": ["extract_xas_descriptors", "get_energy_calibration"],
    },
    "interpret_coordination_geometry": {
        "long_description": (
            "Coordination and site-symmetry verdict for 3d K-edges from "
            "pre-edge intensity and component count on the Wilke 2001 "
            "centroid-versus-intensity envelope: a weak pre-edge reads as "
            "centrosymmetric/octahedral, a strong one as "
            "non-centrosymmetric/tetrahedral, and the intermediate band as "
            "5-coordinate, distorted, or mixed. Uses the re-broadened pre-edge, "
            "since the envelope is drawn from conventional-broadening data."
        ),
        "python_func": "science.xas.interpret.interpret_coordination_geometry(descriptors, calibration)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {verdict, basis, confidence, evidence, caveats, flags}",
        "source": "spec_datafile",
        "source_detail": (
            "Descriptors from the file (or a precomputed descriptors artifact); "
            "intensity brackets from science.tables.edge_shifts."
        ),
        "depends_on": ["extract_xas_descriptors"],
    },
    "summarize_sample_chemistry": {
        "long_description": (
            "Capstone chemical interpretation of a sample: composes "
            "extract_xas_descriptors, interpret_oxidation_state, "
            "interpret_coordination_geometry and detect_per_scan_drift, and "
            "returns one narration paragraph plus the annotated descriptor "
            "figure. Includes the drift check on purpose — a photoreduction "
            "trend invalidates the oxidation verdict it would otherwise sit "
            "next to."
        ),
        "python_func": "science.xas.interpret.summarize_chemistry(descriptors, calibration)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {narration, oxidation, geometry, drift, quality, descriptors} + annotated figure",
        "source": "spec_datafile",
        "source_detail": (
            "Descriptors from the file (or a precomputed descriptors artifact); "
            "calibration from calibration_store."
        ),
        "depends_on": ["extract_xas_descriptors", "get_energy_calibration"],
    },
    "get_energy_calibration": {
        "long_description": (
            "Report the current session energy calibration: offset in eV, the "
            "reference element and edge it came from, its age, and the drift "
            "across all recorded calibrations. Returns calibrated=false when "
            "none exists, which is the signal that the interpretation tools "
            "will run relative-only and refuse absolute oxidation states."
        ),
        "python_func": "calibration_store.current_calibration()",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {calibrated, offset_ev, reference_element, reference_edge, recorded_at, age_s, drift_ev}",
        "source": "filesystem",
        "source_detail": (
            "Session calibration JSON written by calibration_store into its "
            "configured directory — session state, not scan data."
        ),
        "depends_on": [],
    },
    "record_energy_calibration": {
        "long_description": (
            "Register a session energy calibration from a measured reference "
            "foil or compound scan: computes the reference's E0 under the fixed "
            "definition (Savitzky-Golay smoothed derivative maximum), stores "
            "the offset to the assigned reference energy with a timestamp, and "
            "unlocks the absolute-energy claims the interpretation tools "
            "otherwise refuse. Run it at session start and after any mono "
            "recalibration."
        ),
        "python_func": (
            "science.xas.e0.find_e0(...) over spec_data.scans.get_normalized_scan_arrays(...), "
            "then calibration_store.record_calibration(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {offset_ev, measured_e0_ev, assigned_reference_ev, reference_element, reference_edge, recorded_at}",
        "source": "spec_datafile",
        "source_detail": (
            "Reference scan from spec_data.scans.get_normalized_scan_arrays; the "
            "resulting offset is written to the calibration_store JSON."
        ),
        "depends_on": ["list_scans", "identify_edge"],
    },

    # ---------- Scan statistics: convergence, heterogeneity, and their plots

    "analyze_feature_evolution": {
        "long_description": (
            "Per-rep scalar trace and convergence verdict for a feature the "
            "agent defines itself: it passes the numeric eV bounds and the "
            "statistic that captures the feature (white-line peak, pre-edge "
            "shoulder, the dip between two oscillations), and gets back the "
            "running mean, running SEM and a verdict. Energy-window agnostic "
            "on purpose — the caller owns the choice of window, so the tool "
            "does not have to know what technique it is looking at."
        ),
        "python_func": (
            "science.statistics.features.{extract_window_scalar,"
            "analyze_feature_evolution}(...) over "
            "spec_data.scans.get_normalized_scan_arrays(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {per_rep_values, running_mean, running_sem, final_sem_frac, final_drift_frac, verdict}",
        "source": "spec_datafile",
        "source_detail": (
            "Per-rep columns from spec_data.scans.get_normalized_scan_arrays, "
            "with the counter and normalization mode the caller specified."
        ),
        "depends_on": ["list_scans"],
    },
    "analyze_per_spot": {
        "long_description": (
            "Run the convergence and efficiency analysis separately for each "
            "sample spot in the file (grouped by the recorded Sx/Sy/Sz), then "
            "report a between-spot versus within-spot heterogeneity "
            "F-statistic. F near 1 means the spots agree and combining them is "
            "safe; F much greater than 1 means they disagree beyond shot noise, "
            "so the combined average is a population mean over a heterogeneous "
            "sample rather than a better measurement of one spot."
        ),
        "python_func": (
            "spec_data.scans.group_scans_by_spot(...) then per group "
            "science.statistics.{features,efficiency} + "
            "features.heterogeneity_f_statistic(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {spots[{position, n_scans, convergence, efficiency}], f_statistic, verdict}",
        "source": "spec_datafile",
        "source_detail": (
            "Sx/Sy/Sz motor positions from the SPEC scan headers plus per-rep "
            "arrays from spec_data.scans.get_normalized_scan_arrays."
        ),
        "depends_on": ["group_scans_by_spot"],
    },
    "group_scans_by_spot": {
        "long_description": (
            "Cluster a file's scans by sample spot using the recorded Sx/Sy/Sz "
            "motor positions — two scans are the same spot when all three "
            "agree within tol_mm. Worth running before any convergence "
            "analysis: reps collected at different spots make the cross-spot "
            "scatter look like poor convergence when it is really sample "
            "heterogeneity."
        ),
        "python_func": "spec_data.scans.group_scans_by_spot(file_name, tol_mm)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {tol_mm, spots[{sx, sy, sz, scan_numbers}]}",
        "source": "spec_datafile",
        "source_detail": "Motor positions from the SPEC scan headers (#P lines) via the metadata cache.",
        "depends_on": ["list_scans"],
    },
    "plot_scan_stack": {
        "long_description": (
            "Overlay every rep of one sample on a single axis, colour-"
            "progressed by rep order. The fastest visual read on what the reps "
            "are doing: symmetric scatter about a stable mean is converged, a "
            "steady one-directional walk means more reps or an evolving "
            "sample, and a monotonic collapse means the sample is being burned "
            "away."
        ),
        "python_func": "spec_data.plotting.plot_scan_stack(file_name, ...)",
        "spec_command": None,
        "mutates": False,
        "output": "base64 PNG + JSON summary {n_reps, counter, normalization, window_ev}",
        "source": "spec_datafile",
        "source_detail": (
            "Loads by file name, so the figure lives in spec_data/ rather than "
            "science/plots/ — see the plotting rule in science/README.md."
        ),
        "depends_on": ["list_scans"],
    },
    "plot_running_average": {
        "long_description": (
            "Plot the running average across reps as it evolves — one line per "
            "cumulative subset, colour-progressed by rep number — with the "
            "final ±SEM band. Shows whether the running mean is still moving "
            "rep-over-rep at the feature of interest, which is the question "
            "'have I collected enough' actually reduces to."
        ),
        "python_func": "spec_data.plotting.plot_running_average(file_name, ...)",
        "spec_command": None,
        "mutates": False,
        "output": "base64 PNG + JSON summary {n_reps, final_sem, window_ev}",
        "source": "spec_datafile",
        "source_detail": "Loads by file name via spec_data.scans; figure built in spec_data/plotting.py.",
        "depends_on": ["list_scans"],
    },
    "plot_first_half_vs_second_half": {
        "long_description": (
            "Compare the average of the first half of the reps to the second "
            "half with SEM bands, reporting max |delta|/SEM. Under 2 sigma the "
            "halves agree and the sample is stationary; over 3 sigma at any "
            "feature they disagree and more reps will not help, because the "
            "cause is drift, damage, or heterogeneity rather than noise. The "
            "strongest single-glance stationarity test available."
        ),
        "python_func": "spec_data.plotting.plot_first_half_vs_second_half(file_name, ...)",
        "spec_command": None,
        "mutates": False,
        "output": "base64 PNG + JSON summary {n_sigma_max, energy_of_max_ev, verdict}",
        "source": "spec_datafile",
        "source_detail": "Loads by file name via spec_data.scans; figure built in spec_data/plotting.py.",
        "depends_on": ["list_scans"],
    },
    "plot_feature_evolution": {
        "long_description": (
            "Plot one per-rep scalar — the chosen statistic over "
            "[e_min, e_max] — against rep number, with the running mean and a "
            "±SEM band. The visual companion to analyze_feature_evolution: use "
            "it to confirm a feature has actually flatlined, since a still-"
            "trending trace means it has not converged whatever the summary "
            "statistic says."
        ),
        "python_func": "spec_data.plotting.plot_feature_evolution(file_name, ...)",
        "spec_command": None,
        "mutates": False,
        "output": "base64 PNG + JSON summary {per_rep_values, running_mean, running_sem}",
        "source": "spec_datafile",
        "source_detail": "Loads by file name via spec_data.scans; figure built in spec_data/plotting.py.",
        "depends_on": ["analyze_feature_evolution"],
    },

    # ---------- EXAFS: k-space extraction and Fourier products ---------------

    "list_collector_scans": {
        "long_description": (
            "List the scan groups in a directory of SSRL 'EXAFS Data Collector' "
            "ASCII files — the DAQ format of SSRL XAS stations such as BL 4-3, "
            "one of several formats across SSRL's stations, and the only one "
            "this tool reads. One row per group (sample stem plus scan number) "
            "with its sweep numbers. Run it first to discover valid group names "
            "for the EXAFS tools."
        ),
        "python_func": "spec_data.ssrl_backend.SSRLAsciiBackend(collector_dir).list_groups()",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {data_dir, groups[{name, scan_number, sweeps[...]}]}",
        "source": "filesystem",
        "source_detail": (
            "Collector ASCII files under collector_dir, or SSRL_COLLECTOR_DIR "
            "when the argument is omitted."
        ),
        "depends_on": [],
    },
    "extract_chi": {
        "long_description": (
            "Extract EXAFS chi(k) from repeated scans: merge the reps (short "
            "or aborted sweeps dropped, glitches masked), find E0, apply "
            "Athena-style pre/post-edge polynomial normalization, and remove "
            "the post-edge background with a quick-look AUTOBK spline whose "
            "knot budget comes from R_bkg. Returns the chi(k) arrays as an "
            "artifact the Fourier tools accept directly, so the pipeline is not "
            "re-run per call."
        ),
        "python_func": (
            "spec_data.exafs_data.load_mu(...) -> science.xas.normalize.pre_post_normalize(...) "
            "-> science.xas.e0.find_e0(...) -> science.exafs.background.autobk_lite(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {e0_ev, edge_step, k, chi, provenance} + chi(k) extraction plot",
        "source": "spec_datafile",
        "source_detail": (
            "spec_data.exafs_data.load_mu routes to the SSRL collector backend "
            "when collector_dir or SSRL_COLLECTOR_DIR is set, otherwise to the "
            "SPEC file chain."
        ),
        "depends_on": ["list_scans", "list_collector_scans"],
    },
    "fourier_transform_chi": {
        "long_description": (
            "Fourier transform chi(k) into R space in the Ifeffit convention "
            "with a Hanning window, returning |chi(R)| and the first-shell "
            "apparent distance. The R axis is phase-uncorrected: peaks sit "
            "roughly 0.3-0.5 A below true bond lengths, and every axis label "
            "says so, because a figure must not invite that misreading. Accepts "
            "the chi artifact from extract_chi, or the same load arguments to "
            "run the extraction first."
        ),
        "python_func": (
            "science.exafs.fourier.xftf(...) + first_shell_peak(...), over the chi "
            "artifact or a fresh extract_chi pipeline"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {r, chir_mag, first_shell_r_ang, provenance{convention, window, kweight, r_axis}} + |chi(R)| plot",
        "source": "tool_chain",
        "source_detail": (
            "Consumes the chi artifact from extract_chi; falls back to the full "
            "load-and-extract path when given file arguments instead."
        ),
        "depends_on": ["extract_chi"],
    },
    "exafs_products": {
        "long_description": (
            "Capstone EXAFS reduction in one call: merge reps, normalize, "
            "extract chi(k), Fourier transform, and report |chi(R)| with the "
            "first-shell apparent distance, returning both the extraction and "
            "R-space plots. Composes extract_chi and fourier_transform_chi and "
            "takes their arguments through; a precomputed chi artifact skips "
            "the extraction stage."
        ),
        "python_func": (
            "extract_chi pipeline -> science.exafs.fourier.xftf(...) + "
            "first_shell_peak(...) -> science.plots.exafs.{plot_chi_extraction,plot_chir}"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {e0_ev, edge_step, k, chi, r, chir_mag, first_shell_r_ang, provenance} + two plots",
        "source": "spec_datafile",
        "source_detail": (
            "Same load path as extract_chi (SPEC chain or SSRL collector "
            "backend), or a supplied chi artifact."
        ),
        "depends_on": ["extract_chi"],
    },
    "overlay_chi_spectra": {
        "long_description": (
            "Extract and overlay chi(k)*k^w for several scan groups on one "
            "plot — the k-space comparison view for an operando or potential "
            "series, or a sample against its standards. Every group runs the "
            "same extract_chi pipeline with shared counter, R_bkg and k-weight "
            "settings, so a difference in the overlay is a difference in the "
            "data rather than in the processing."
        ),
        "python_func": (
            "extract_chi pipeline per group -> science.plots.exafs.plot_chi_overlay(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {n_overlaid, reports[{file_name, e0_ev, edge_step, ...}]} + k-space overlay plot",
        "source": "spec_datafile",
        "source_detail": (
            "One load per group through spec_data.exafs_data.load_mu (SPEC chain "
            "or SSRL collector backend)."
        ),
        "depends_on": ["list_collector_scans", "extract_chi"],
    },

    # ---------- X-ray Raman (XRS): the energy-loss axis ---------------------
    # Kept apart from the XAS tools because the XAS defaults are not merely
    # suboptimal here, they are wrong by construction: the feature is a bump on
    # a sloping Compton profile, not a step, so there is no edge to normalize
    # to and no E0 to align on. See docs/xrs-analysis-branch-plan.md.

    "calibrate_energy_loss": {
        "long_description": (
            "Fit the elastic (Rayleigh) line of an elastic scan — an 'ascan "
            "mono' with the analyzer fixed — to set the zero of energy loss and "
            "measure the instrumental energy resolution from its FWHM, then "
            "record it for the file. Run this first for any XRS work: it is the "
            "loss-axis anchor that replaces find_e0, and nothing downstream has "
            "an absolute axis without it."
        ),
        "python_func": (
            "spec_data.xrs_data.calibrate_energy_loss(...) -> "
            "science.xrs.calibrate.fit_elastic_line(...) -> "
            "science.plots.xrs.plot_elastic_fit(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {elastic_center_ev, fwhm_ev, fit_ok, method, recorded} + elastic-fit plot",
        "source": "spec_datafile",
        "source_detail": (
            "Elastic scan via spec_data.xrs_data.load_scan_signal; the fitted "
            "centre is written to the per-file elastic calibration store."
        ),
        "depends_on": ["list_scans"],
    },
    "build_loss_axis": {
        "long_description": (
            "Convert one scan's incident-energy axis to energy loss (omega = "
            "incident minus elastic centre) and plot signal/I0 against loss. "
            "Uses the recorded elastic calibration unless an explicit centre is "
            "given; with neither, the axis stays mono energy and says so in a "
            "flag rather than pretending. The single-scan companion to "
            "average_xrs_scans."
        ),
        "python_func": (
            "spec_data.xrs_data.reduce_xrs(...) -> "
            "science.xrs.calibrate.to_energy_loss(...) -> "
            "science.plots.xrs.plot_loss_spectrum(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {loss, intensity, elastic_center_ev, elastic_center_source, axis, flags} + loss-spectrum plot",
        "source": "spec_datafile",
        "source_detail": (
            "One scan via spec_data.xrs_data; elastic centre from the recorded "
            "calibration or the explicit argument."
        ),
        "depends_on": ["calibrate_energy_loss"],
    },
    "average_xrs_scans": {
        "long_description": (
            "Average repeated XRS scans on the energy-loss axis: load each rep "
            "on the chosen counter, divide by I0, re-reference to the elastic "
            "line, interpolate onto a common loss grid, and average with a "
            "per-point SEM. The XRS analogue of average_scans, and "
            "deliberately not the same: it does not edge-step-normalize and "
            "does not align on an E0, neither of which exists here."
        ),
        "python_func": (
            "spec_data.xrs_data.reduce_xrs(...) -> "
            "science.xrs.reduce.align_and_average(...) -> "
            "science.plots.xrs.plot_loss_spectrum(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {loss, mean, sem, n_reps, counter, counter_warning, elastic_center_ev} + loss-spectrum plot",
        "source": "spec_datafile",
        "source_detail": (
            "Reps via spec_data.xrs_data.reduce_xrs; the counter is auto-picked "
            "and warned about when omitted."
        ),
        "depends_on": ["calibrate_energy_loss"],
    },
    "subtract_compton_background": {
        "long_description": (
            "Average the reps, then fit and subtract the Compton/valence "
            "background under the edge — the XRS replacement for XAS "
            "pre/post-edge normalization. The background is fitted to flank "
            "points strictly outside the [edge_lo, edge_hi] loss window, so the "
            "feature itself never influences the baseline that is subtracted "
            "from it."
        ),
        "python_func": (
            "spec_data.xrs_data.reduce_xrs(...) -> "
            "science.xrs.reduce.subtract_compton_background(...) -> "
            "science.plots.xrs.plot_background_subtraction(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {loss, subtracted, background, model, flank_windows_ev, fit_ok} + subtraction plot",
        "source": "spec_datafile",
        "source_detail": "Reps via spec_data.xrs_data.reduce_xrs; model default from science.xrs.policy.",
        "depends_on": ["average_xrs_scans"],
    },
    "normalize_xrs": {
        "long_description": (
            "Full XRS reduction to a comparable edge: average the reps, "
            "subtract the Compton background, and area-normalize over the edge "
            "window. This is what produces the final per-atom-comparable XRS "
            "spectrum. Area normalization only — an f-sum/absolute scale is a "
            "documented future extension, not something this silently "
            "approximates."
        ),
        "python_func": (
            "spec_data.xrs_data.reduce_xrs(...) -> "
            "science.xrs.reduce.subtract_compton_background(...) -> "
            "science.xrs.reduce.area_normalize(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {loss, normalized, area, model, normalization, fit_ok} + subtraction plot",
        "source": "spec_datafile",
        "source_detail": "Reps via spec_data.xrs_data.reduce_xrs.",
        "depends_on": ["subtract_compton_background"],
    },
    "overlay_xrs_spectra": {
        "long_description": (
            "Overlay reduced XRS spectra from several files — samples, states "
            "of charge, q-bins — on the energy-loss axis with identical "
            "processing: each file averaged, Compton-subtracted when an edge "
            "window is given, and area-normalized before plotting. The XRS "
            "analogue of plot_averaged_scans_overlay."
        ),
        "python_func": (
            "spec_data.xrs_data.reduce_xrs(...) per file -> "
            "science.xrs.reduce.{subtract_compton_background,area_normalize}(...) -> "
            "science.plots.xrs.plot_overlay(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {n_overlaid, reports[{file_name, counter, elastic_center_ev, area}]} + overlay plot",
        "source": "spec_datafile",
        "source_detail": "One reduction per file through spec_data.xrs_data.reduce_xrs.",
        "depends_on": ["calibrate_energy_loss"],
    },
    "extract_xrs_descriptors": {
        "long_description": (
            "Extract the measurable descriptors of a reduced XRS edge: edge "
            "onset (the loss-axis inflection), pre-edge peak position and area, "
            "white line, integrated edge area, and feature SNR. Averages the "
            "reps and Compton-subtracts first when an edge window is given. "
            "Returns the numbers plus an annotated plot showing where each one "
            "was measured."
        ),
        "python_func": (
            "spec_data.xrs_data.reduce_xrs(...) -> "
            "science.xrs.descriptors.extract_xrs_descriptors(...) -> "
            "science.plots.xrs.plot_xrs_descriptors(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {onset, pre_edge, white_line, integrated_area, feature_snr, windows_ev} + annotated plot",
        "source": "spec_datafile",
        "source_detail": (
            "Reps via spec_data.xrs_data.reduce_xrs; edge assignment from "
            "science.tables.xrs_edges."
        ),
        "depends_on": ["normalize_xrs"],
    },
    "assess_xrs_quality": {
        "long_description": (
            "Quality gate for a reduced XRS edge: SNR measured on the edge "
            "feature rather than the Compton-dominated whole spectrum, the "
            "elastic-line energy resolution, and a verdict of publication, "
            "usable, marginal, or noise-limited. Measuring SNR on the feature "
            "matters here — whole-spectrum SNR in XRS is dominated by the "
            "Compton profile and flatters the data."
        ),
        "python_func": (
            "science.xrs.descriptors.extract_xrs_descriptors(...) -> "
            "science.xrs.interpret.assess_xrs_quality(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {feature_snr, resolution_fwhm_ev, verdict, caveats, flags}",
        "source": "spec_datafile",
        "source_detail": "Reps via spec_data.xrs_data.reduce_xrs; resolution from the elastic calibration.",
        "depends_on": ["calibrate_energy_loss", "extract_xrs_descriptors"],
    },
    "interpret_xrs_oxidation_state": {
        "long_description": (
            "Oxidation-state and covalency read from a reduced XRS edge. "
            "Reports the edge-onset shift — absolute only with an elastic plus "
            "reference calibration, otherwise explicitly relative — and, for an "
            "O K-edge, the pre-edge covalency / oxygen-redox indicator. For a "
            "3d metal L-edge it returns guidance on the L3/L2 branching ratio "
            "rather than a number it cannot justify."
        ),
        "python_func": "science.xrs.interpret.interpret_xrs_oxidation_state(descriptors, calibration)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {verdict, basis, confidence, onset_shift_ev, absolute|relative, evidence, caveats}",
        "source": "spec_datafile",
        "source_detail": (
            "Descriptors from the reduced edge; edge family from "
            "science.tables.xrs_edges."
        ),
        "depends_on": ["extract_xrs_descriptors"],
    },
    "compare_xrs_to_references": {
        "long_description": (
            "Non-negative linear-combination fit of a reduced XRS spectrum "
            "against reference spectra, returning phase or valence fractions. "
            "References may be other files or explicit arrays. Valid against "
            "XANES references only in the low-q dipole regime, where the XRS "
            "transition matrix element matches the absorption one — outside it "
            "the comparison is not physically justified."
        ),
        "python_func": (
            "spec_data.xrs_data.reduce_xrs(...) per spectrum -> "
            "science.xrs.interpret.compare_xrs_to_references(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {components[{name, fraction}], fit_r2, residual_rms, regime, caveats} + fit plot",
        "source": "spec_datafile",
        "source_detail": (
            "Target and every file-backed reference reduced identically through "
            "spec_data.xrs_data.reduce_xrs."
        ),
        "depends_on": ["normalize_xrs"],
    },
    "interpret_q_dependence": {
        "long_description": (
            "Classify a feature's momentum-transfer behaviour: dipole (low q, "
            "XANES-like) versus monopole/quadrupole (intensity rising with q). "
            "Takes either points you already have, or scan groups from one file "
            "plus an edge and feature window, in which case the tool does the "
            "reduction itself. This is the discriminator XAS cannot provide at "
            "all, and the reason XRS is worth the count rate."
        ),
        "python_func": (
            "science.xrs.descriptors.integrated_area(...) per q group -> "
            "science.xrs.interpret.interpret_q_dependence(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {points[{q, value, regime}], trend, verdict, caveats, provenance}",
        "source": "tool_chain",
        "source_detail": (
            "Consumes area-normalized feature intensities from tag_crystal_q / "
            "extract_xrs_descriptors, or reduces scan groups itself when given "
            "file arguments."
        ),
        "depends_on": ["tag_crystal_q", "extract_xrs_descriptors"],
    },
    "summarize_xrs_chemistry": {
        "long_description": (
            "Capstone XRS interpretation: the oxidation and covalency verdict "
            "plus the quality gate, one narration paragraph, and the annotated "
            "descriptor plot. Averages the reps, Compton-subtracts over the "
            "edge window, extracts descriptors and interprets. The XRS "
            "analogue of summarize_sample_chemistry."
        ),
        "python_func": "science.xrs.interpret.summarize_xrs_chemistry(descriptors, calibration)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {narration, oxidation, quality, descriptors} + annotated plot",
        "source": "spec_datafile",
        "source_detail": "Reps via spec_data.xrs_data.reduce_xrs; edge family from science.tables.xrs_edges.",
        "depends_on": ["extract_xrs_descriptors"],
    },
    "sum_crystals": {
        "long_description": (
            "Energy-align several analyzer-crystal / SDD-ROI channels of one "
            "scan onto a common loss grid — each channel carries its own "
            "calibration — reject the outliers (low SNR, or a shape deviating "
            "from the channel median), and sum. The crystals Bragg-focus onto "
            "the SDD and the ROI gates the signal, so they are one "
            "spectrometer and summing them is the intended use, not an "
            "averaging shortcut."
        ),
        "python_func": (
            "spec_data.xrs_data.load_scan_signal(...) per channel -> "
            "science.xrs.calibrate.to_energy_loss(...) -> "
            "science.xrs.reduce.sum_crystals(...) -> "
            "science.plots.xrs.plot_crystal_sum(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {loss, summed, kept[...], rejected[...], n_channels} + crystal-sum plot",
        "source": "spec_datafile",
        "source_detail": "One counter column per channel via spec_data.xrs_data.load_scan_signal.",
        "depends_on": ["align_crystals"],
    },
    "align_crystals": {
        "long_description": (
            "Report the per-crystal alignment and outlier-rejection decisions "
            "without summing: for each channel, its SNR, how far its shape "
            "deviates from the channel-median spectrum, and whether it would be "
            "kept. Use it to inspect the analyzer array before sum_crystals "
            "commits to a set — a dead or misaligned crystal is much easier to "
            "see here than in the sum."
        ),
        "python_func": (
            "spec_data.xrs_data.load_scan_signal(...) per channel -> "
            "science.xrs.calibrate.{to_energy_loss,common_loss_grid}(...) -> "
            "science.xrs.reduce.reject_outlier_channels(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {channels[{counter, snr, dev_mad, keep}], grid_ev, n_kept, n_rejected}",
        "source": "spec_datafile",
        "source_detail": "One counter column per channel via spec_data.xrs_data.load_scan_signal.",
        "depends_on": ["calibrate_energy_loss"],
    },
    "tag_crystal_q": {
        "long_description": (
            "Compute the momentum transfer q for each analyzer crystal from "
            "the incident energy and the crystal scattering angles, "
            "q = (4*pi/lambda)*sin(theta), and label each with its regime. Low "
            "q approximates the dipole limit and reads like XANES; high q turns "
            "on monopole and quadrupole transitions. Use it to group crystals "
            "into q-bins before q-resolved analysis."
        ),
        "python_func": (
            "science.xrs.calibrate.q_from_two_theta(...) + "
            "science.xrs.policy.q_regime(...)"
        ),
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {crystals[{counter, two_theta_deg, q_inv_ang, regime}]}",
        "source": "tool_chain",
        "source_detail": (
            "Pure geometry — takes the incident energy and angles as arguments "
            "and reads no data source of its own."
        ),
        "depends_on": [],
    },

    # ---------- Deployment backends: S3DF Postgres and staff Slack ----------

    "execute_readonly_sql": {
        "long_description": (
            "Run a read-only SELECT against the S3DF scan-metadata Postgres, "
            "for the deployments where bldata_converter populates it instead of "
            "reading SPEC files directly. Write keywords are rejected rather "
            "than escaped, and the result set is truncated at max_rows so a "
            "careless query cannot flood an agent's context."
        ),
        "python_func": "spec_data.postgres_backend.PostgresBackend.execute_readonly_sql(query, max_rows)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {columns[...], rows[[...]], row_count, truncated}",
        "source": "postgres",
        "source_detail": (
            "BL15-2_scan_metadata on the S3DF Postgres, via the lazily imported "
            "psycopg2 backend (the 'postgres' extra)."
        ),
        "depends_on": [],
    },
    "list_channels": {
        "long_description": (
            "List the public Slack channels the bot has been invited to. Run it "
            "first to resolve a human channel name to the channel_id the other "
            "Slack tools require."
        ),
        "python_func": "notify.slack.list_channels()",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {channels[{id, name, is_member}]}",
        "source": "slack",
        "source_detail": (
            "Slack Web API via the lazily imported slack-sdk (the 'slack' "
            "extra); needs a bot token in the environment."
        ),
        "depends_on": [],
    },
    "read_channel_messages": {
        "long_description": (
            "Read recent messages from a Slack channel so an agent can pick up "
            "staff instructions or context left outside the tool loop. "
            "Read-only; the oldest cursor bounds how far back it reaches."
        ),
        "python_func": "notify.slack.read_channel_messages(channel_id, limit, oldest)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {messages[{ts, user, text, thread_ts, reply_count}]}",
        "source": "slack",
        "source_detail": "Slack Web API via the lazily imported slack-sdk (the 'slack' extra).",
        "depends_on": ["list_channels"],
    },
    "read_thread_replies": {
        "long_description": (
            "Read every reply in a Slack thread, parent message included — the "
            "follow-up to read_channel_messages when a channel message has a "
            "reply_count and the actual decision is inside the thread."
        ),
        "python_func": "notify.slack.read_thread_replies(channel_id, thread_ts)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {messages[{ts, user, text}]}",
        "source": "slack",
        "source_detail": "Slack Web API via the lazily imported slack-sdk (the 'slack' extra).",
        "depends_on": ["read_channel_messages"],
    },
    "post_slack_message": {
        "long_description": (
            "Post a message to a Slack channel, or reply in a thread by passing "
            "thread_ts. The one outward-facing tool in the catalog: it is the "
            "route by which an agent reaches a human, so the message is sent as "
            "written."
        ),
        "python_func": "notify.slack.post_message(channel_id, text, thread_ts)",
        "spec_command": None,
        "mutates": False,
        "output": "JSON: {ok, channel, ts}",
        "source": "slack",
        "source_detail": "Slack Web API via the lazily imported slack-sdk (the 'slack' extra).",
        "depends_on": ["list_channels"],
    },
}


# Every key a complete entry carries. ``spec_commands`` is deliberately
# absent: it is optional, and only the two run-time-dispatching handlers
# have it. Consumers that register their own tools are validated against
# this exact set, so a half-filled entry is rejected at registration
# rather than rendering as blank fields on the catalog page — or, worse,
# silently mis-classifying the tool because ``mutates`` is missing.
LINEAGE_REQUIRED_KEYS: tuple[str, ...] = (
    "long_description",
    "python_func",
    "spec_command",
    "mutates",
    "output",
    "source",
    "source_detail",
    "depends_on",
)

# Keys allowed but not required.
LINEAGE_OPTIONAL_KEYS: tuple[str, ...] = ("spec_commands",)

_LINEAGE_SOURCES = frozenset({
    "spec_datafile", "spec_session", "spec_logfile", "spec_config",
    "autonomy_db", "filesystem", "tool_chain", "postgres", "camera",
    "slack",
})


def validate_lineage_entry(name: str, entry: dict) -> list[str]:
    """Return every problem with one lineage entry, as readable sentences.

    A list rather than a raised exception so ``register_lineage`` can
    report all of a consumer's entries at once — fixing a registration
    one ``ValueError`` at a time is the slow way to find out you got the
    shape wrong in twenty places.
    """
    problems: list[str] = []
    if not isinstance(entry, dict):
        return [f"{name}: entry is {type(entry).__name__}, expected a dict"]

    for key in LINEAGE_REQUIRED_KEYS:
        if key not in entry:
            problems.append(f"{name}: missing key {key!r}")

    if "mutates" in entry and not isinstance(entry["mutates"], bool):
        problems.append(
            f"{name}: 'mutates' is {type(entry['mutates']).__name__}, "
            "expected a bool — categorize() reads it to place the tool on "
            "spec-write, and a truthy string would place a read tool there"
        )

    for key in ("long_description", "python_func", "output", "source_detail"):
        value = entry.get(key)
        if key in entry and not (isinstance(value, str) and value.strip()):
            problems.append(f"{name}: {key!r} must be a non-empty string")

    if "source" in entry and entry["source"] not in _LINEAGE_SOURCES:
        problems.append(
            f"{name}: source {entry['source']!r} is not one of "
            f"{sorted(_LINEAGE_SOURCES)} — add it to the enum in this "
            "module's docstring first"
        )

    spec_command = entry.get("spec_command")
    if "spec_command" in entry and spec_command is not None and not isinstance(spec_command, str):
        problems.append(f"{name}: 'spec_command' must be a string or None")

    if "depends_on" in entry and not isinstance(entry["depends_on"], list):
        problems.append(f"{name}: 'depends_on' must be a list (possibly empty)")

    if "spec_commands" in entry:
        value = entry["spec_commands"]
        if not (isinstance(value, list) and value
                and all(isinstance(c, str) and c for c in value)):
            problems.append(
                f"{name}: 'spec_commands' must be a non-empty list of "
                "registered command-name strings"
            )

    unknown = set(entry) - set(LINEAGE_REQUIRED_KEYS) - set(LINEAGE_OPTIONAL_KEYS)
    if unknown:
        problems.append(
            f"{name}: unknown keys {sorted(unknown)} — the catalog page "
            "renders none of them, so they are silently dropped"
        )
    return problems


def register_lineage(mapping: dict[str, dict]) -> None:
    """Merge out-of-tree lineage entries into ``TOOL_LINEAGE`` in place.

    Validates every entry first and raises a single ``ValueError``
    listing every problem, so nothing is half-merged.

    The update is in place on purpose: ``categorize.py`` binds this dict
    object at import, and so does every consumer that reads it. Rebinding
    the module attribute instead is what forced consumers to monkey-patch
    ``categorize.TOOL_LINEAGE`` to be seen at all.
    """
    if not mapping:
        return
    problems: list[str] = []
    for name, entry in mapping.items():
        problems.extend(validate_lineage_entry(name, entry))
    if problems:
        raise ValueError(
            "invalid lineage entries ({}):\n  {}".format(
                len(problems), "\n  ".join(problems)
            )
        )
    TOOL_LINEAGE.update(mapping)


def extract_inputs(tool_def: dict) -> list[dict]:
    """Flatten a tool's JSONSchema parameters into a UI-friendly list."""
    fn = tool_def.get("function", {})
    params = fn.get("parameters", {}) or {}
    props = params.get("properties", {}) or {}
    required = set(params.get("required", []) or [])
    out: list[dict] = []
    for name, spec in props.items():
        entry = {
            "name": name,
            "type": spec.get("type", ""),
            "required": name in required,
            "description": spec.get("description", ""),
        }
        if "enum" in spec:
            entry["enum"] = spec["enum"]
        if "default" in spec:
            entry["default"] = spec["default"]
        out.append(entry)
    return out

def build_detailed_tool(tool_def: dict, category: str) -> dict:
    """Merge a tool definition with its lineage entry for the UI."""
    fn = tool_def.get("function", {})
    name = fn.get("name", "")
    lineage = TOOL_LINEAGE.get(name, {})
    return {
        "name": name,
        "category": category,
        "description": fn.get("description", ""),
        "long_description": lineage.get("long_description", ""),
        "python_func": lineage.get("python_func", ""),
        "spec_command": lineage.get("spec_command"),
        "sends_spec_command": lineage.get("spec_command") is not None,
        "mutates": bool(lineage.get("mutates")),
        "output": lineage.get("output", ""),
        "source": lineage.get("source", ""),
        "source_detail": lineage.get("source_detail", ""),
        "depends_on": lineage.get("depends_on", []),
        "inputs": extract_inputs(tool_def),
    }
