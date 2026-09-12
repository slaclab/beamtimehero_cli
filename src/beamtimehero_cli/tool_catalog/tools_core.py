"""Core beamline tool handlers — wraps audited_call and data/log readers.

Each tool is exposed via tool_catalog.definitions. SPEC-mutating tools
delegate to `audited_call()`, which looks up phase + experiment from
`beamtimehero_cli.runtime_state`, writes an action_log row before
dispatch, then calls into the `spec_cmd` primitive. Read-only tools
touch only the local filesystem.

Every SPEC-mutating tool requires a non-empty `justification` argument;
the wrapper refuses to run without it.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import numpy as np

from beamtimehero_cli import config as bl_config
from beamtimehero_cli import runtime_state
from beamtimehero_cli.action_log.db import recent_actions
from beamtimehero_cli.audited_call import audited_call
from beamtimehero_cli.spec_data import scans as scan_data
from beamtimehero_cli.spec_data import plotting
from beamtimehero_cli.science.plots.scan import fig_to_base64
from beamtimehero_cli.spec_logs import log_reader
from beamtimehero_cli.science.exafs import policy as ex_policy
from beamtimehero_cli.science.xas import policy as xas_policy
from beamtimehero_cli.science.xrs import policy as xrs_policy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_json(result: dict | list | str) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2, default=str)

def _refuse_rerun_if_already_done(command: str, human_name: str) -> Optional[str]:
    """Gate long-running macros so the agent can never trigger them
    twice. If the action_log already shows a successful run for the
    current experiment, return the refusal JSON string; otherwise
    return None and the caller should proceed.

    Why: these macros take minutes and physically re-align hardware.
    A phase-gate failure (e.g. a stale in-memory flag) used to make
    the agent 'helpfully' retry. Never again. The user can reset the
    run via the dashboard Reset button if they want to redo it.
    """
    try:
        from beamtimehero_cli.action_log.db import recent_actions
    except Exception:
        return None
    experiment_id = runtime_state.get_experiment_id()
    if not experiment_id:
        return None
    try:
        actions = recent_actions(limit=100, experiment_id=experiment_id)
    except Exception:
        return None
    prior = next(
        (a for a in actions if a.get("command") == command and a.get("success") == 1),
        None,
    )
    if prior is None:
        return None
    return json.dumps({
        "ok": False,
        "already_done": True,
        "prior_action_id": prior.get("id"),
        "error": (
            f"{human_name} already succeeded for this experiment "
            f"(action {prior.get('id')}). This macro is one-shot. "
            "The operator can force a re-run via the dashboard Reset button."
        ),
    })

# ===========================================================================
# CAT-0 · High-level procedural macros
# ===========================================================================

def t_align_beamline(args: dict) -> tuple[str, list[str]]:
    refusal = _refuse_rerun_if_already_done("align_beamline", "align_beamline")
    if refusal is not None:
        return refusal, []
    justification = (args.get("justification") or "").strip()
    a = [
        str(args.get("energy", 0)),
        str(args.get("xtal_chg", 0)),
        str(args.get("fine_x", 0)),
        str(args.get("fine_z", 0)),
    ]
    res = audited_call("align_beamline", a, justification=justification)
    return _as_json(res), []

def t_align_xes(args: dict) -> tuple[str, list[str]]:
    refusal = _refuse_rerun_if_already_done("align_xes", "align_xes_spectrometer")
    if refusal is not None:
        return refusal, []
    j = (args.get("justification") or "").strip()
    crystals = str(args.get("crystals", "1234567"))
    a = [crystals, str(args.get("en_xes", 0)), str(args.get("en_mono", 0))]
    res = audited_call("align_xes", a, justification=j)
    return _as_json(res), []

def t_auto_sample_align(args: dict) -> tuple[str, list[str]]:
    refusal = _refuse_rerun_if_already_done("auto_sample_align", "auto_sample_align")
    if refusal is not None:
        return refusal, []
    j = (args.get("justification") or "").strip()
    res = audited_call("auto_sample_align", [], justification=j)
    return _as_json(res), []

def t_run_collection(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("run_collection", [], justification=j)
    return _as_json(res), []

def t_select_element(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("select_element", [str(args["element"])], justification=j)
    return _as_json(res), []

def t_peak_mono_pitch(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("peak_mono_pitch", [], justification=j)
    return _as_json(res), []

def t_calibrate_mono(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("calibrate_mono", [str(args["tabulated_edge_ev"])], justification=j)
    return _as_json(res), []

# ===========================================================================
# CAT-1 · Motor control
# ===========================================================================

def t_move_motor(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("umv", [str(args["motor"]), str(args["position"])], justification=j)
    return _as_json(res), []

def t_move_motor_relative(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("umvr", [str(args["motor"]), str(args["delta"])], justification=j)
    return _as_json(res), []

def t_read_motor_position(args: dict) -> tuple[str, list[str]]:
    res = audited_call("p_motor", [str(args["motor"])], justification="")
    return _as_json(res), []

def t_wa(args: dict) -> tuple[str, list[str]]:
    res = audited_call("wa", [], justification="")
    return _as_json(res), []

# ===========================================================================
# CAT-2 · Scan execution
# ===========================================================================

def t_run_motor_scan(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    a = [
        str(args["motor"]),
        str(args["start"]),
        str(args["end"]),
        str(args["npoints"]),
        str(args["count_time"]),
    ]
    res = audited_call("ascan", a, justification=j)
    return _as_json(res), []

def t_run_motor_scan_relative(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    a = [
        str(args["motor"]),
        str(args["delta_start"]),
        str(args["delta_end"]),
        str(args["npoints"]),
        str(args["count_time"]),
    ]
    res = audited_call("dscan", a, justification=j)
    return _as_json(res), []

def t_run_diagonal_scan(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    motor1 = str(args["motor1"])
    motor2 = str(args["motor2"])
    delta = args.get("delta")
    delta_lo_explicit = "delta_lo" in args
    delta_hi_explicit = "delta_hi" in args
    if delta is not None and (delta_lo_explicit or delta_hi_explicit):
        return json.dumps({
            "ok": False,
            "error": "pass either `delta` (symmetric) or `delta_lo`+`delta_hi`, not both",
        }), []
    if delta is not None:
        delta_lo = -float(delta)
        delta_hi = +float(delta)
    else:
        delta_lo = args.get("delta_lo", -8)
        delta_hi = args.get("delta_hi", 8)
    a = [
        motor1, str(delta_lo), str(delta_hi),
        motor2, str(delta_lo), str(delta_hi),
        str(args["npoints"]), str(args["count_time"]),
    ]
    res = audited_call("d2scan", a, justification=j)
    return _as_json(res), []

def t_fit_emission_peak(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    a: list[str] = []
    sn = args.get("scan_number")
    if sn is not None:
        a.append(str(int(sn)))
    res = audited_call("get_HERFD_energy", a, justification=j)
    return _as_json(res), []

def t_run_xas(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    element = args.get("element")
    if element:
        sel_res = audited_call("select_element", [str(element)], justification=j)
        if not sel_res.get("ok", True):
            return _as_json(sel_res), []
    cnt_sec = args.get("count_time")
    nbr_scan = args.get("n_reps")
    emission = args.get("emission_ev")
    nbr_filter = args.get("filter")
    a = [
        str(1.0 if cnt_sec is None else cnt_sec),
        str(1 if nbr_scan is None else nbr_scan),
        str(0 if emission is None else emission),
        str(-1 if nbr_filter is None else nbr_filter),
    ]
    res = audited_call("run_xas", a, justification=j)
    return _as_json(res), []

def t_run_emiss_scan(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    a = [
        str(args["element"]),
        str(args["count_time"]),
        str(args["n_reps"]),
        str(args["emission_ev"]),
        str(args.get("filter", 0)),
    ]
    res = audited_call("emiss_scan", a, justification=j)
    return _as_json(res), []

# ===========================================================================
# CAT-3 · Beamline configuration
# ===========================================================================

def t_mv_energy(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("mv_energy", [str(args["energy_ev"])], justification=j)
    return _as_json(res), []

def t_shutter(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    a = [str(args["command"])]
    if "delay_s" in args:
        a.append(str(args["delay_s"]))
    res = audited_call("shutter", a, justification=j)
    return _as_json(res), []

def t_set_filter(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("mv", ["filter", str(args["bitmask"])], justification=j)
    return _as_json(res), []

def t_safely_remove_filters(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("safely_remove_filters", [], justification=j)
    return _as_json(res), []

def t_set_gain(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    which = args["which"]
    cmd = {"i0": "set_i0_gain", "i1": "set_i1_gain", "i2": "set_i2_gain"}.get(which)
    if not cmd:
        return json.dumps({"ok": False, "error": f"invalid gain channel: {which}"}), []
    res = audited_call(cmd, [str(args["gain_setting"])], justification=j)
    return _as_json(res), []

def t_set_vortex_roi(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    mode = args.get("mode", "auto")
    if mode == "auto":
        a = ["auto", str(args.get("channel", 1))]
    else:
        a = [str(args["channel"]), str(args["lo_ev"]), str(args["hi_ev"])]
    res = audited_call("set_vortex_roi", a, justification=j)
    return _as_json(res), []

def t_open_data_file(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("newfile", [str(args["filename"])], justification=j)
    return _as_json(res), []

def t_plotselect(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("plotselect", [str(args["counter"])], justification=j)
    return _as_json(res), []

# ===========================================================================
# CAT-4 · Alignment fallbacks
# ===========================================================================

def t_run_align_shortcut(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    name = args["name"]
    allowed = {
        "vvv", "hhh", "m1m1", "m2m2", "ggg", "bzbz", "bxbx",
        "dmm", "beamx", "beamz", "cm1m1", "cm2m2", "beamx_fine", "beamz_fine",
    }
    if name not in allowed:
        return json.dumps({"ok": False, "error": f"shortcut '{name}' not allowed"}), []
    res = audited_call("run_shortcut", [name], justification=j)
    return _as_json(res), []

def t_post_scan_move(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    mode = args["mode"]
    if mode not in ("cen", "peak"):
        return json.dumps({"ok": False, "error": "mode must be 'cen' or 'peak'"}), []
    res = audited_call(mode, [], justification=j)
    return _as_json(res), []

# ===========================================================================
# CAT-5 · Beam-diagnostic tool (sample-position diagnostic, alignment)
# ===========================================================================

def t_mv_pinhole(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("mvpinhole", [], justification=j)
    return _as_json(res), []

def t_mv_plastic(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("mvplastic", [], justification=j)
    return _as_json(res), []

def t_mv_knife_clear(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("mvknifeclear", [], justification=j)
    return _as_json(res), []

def t_mv_knife_out(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("mvknifewayout", [], justification=j)
    return _as_json(res), []

def t_measure_beam_size(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    mode_x = "1" if bool(args.get("small_x", False)) else "0"
    mode_z = "1" if bool(args.get("small_z", False)) else "0"
    res = audited_call("measure_beam_size", [mode_x, mode_z], justification=j)
    return _as_json(res), []

def t_zero_pinhole(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("zero_pinhole", [], justification=j)
    return _as_json(res), []

def t_small_beam(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("smallbeam", [], justification=j)
    return _as_json(res), []

def t_big_beam(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("bigbeam", [], justification=j)
    return _as_json(res), []

def t_xtal_align(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("xtalalign", [], justification=j)
    return _as_json(res), []

def t_reset_gap(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("reset_gap", [], justification=j)
    return _as_json(res), []

def t_set_m2_stripe(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    if "energy_ev" not in args:
        return json.dumps({"ok": False, "error": "'energy_ev' (number) is required"}), []
    res = audited_call("m2_stripe", [str(args["energy_ev"])], justification=j)
    return _as_json(res), []

def t_get_anchor(args: dict) -> tuple[str, list[str]]:
    res = audited_call("get_anchor", [], justification="")
    return _as_json(res), []

def t_set_anchor(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("set_anchor", [], justification=j)
    return _as_json(res), []

def t_tracking(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    if "enabled" not in args:
        return json.dumps({"ok": False, "error": "'enabled' (boolean) is required"}), []
    flag = "1" if bool(args["enabled"]) else "0"
    res = audited_call("tracking", [flag], justification=j)
    return _as_json(res), []

# ===========================================================================
# CAT-6 · Beam monitoring
# ===========================================================================

def t_get_beam_size(args: dict) -> tuple[str, list[str]]:
    res = audited_call("wbeamsize", [], justification="")
    return _as_json(res), []

def t_get_beam_status(args: dict) -> tuple[str, list[str]]:
    res = audited_call("beam_status", [], justification="")
    return _as_json(res), []

def t_get_counts(args: dict) -> tuple[str, list[str]]:
    t = args.get("count_time", 1)
    res = audited_call("ct", [str(t)], justification="")
    return _as_json(res), []

def t_get_counter(args: dict) -> tuple[str, list[str]]:
    t = args.get("count_time", 1)
    res = audited_call("ct", [str(t)], justification="")
    if res.get("ok") and "counters" in res.get("result", {}):
        name = args["counter"]
        counters = res["result"]["counters"]
        if name in counters:
            res["result"] = {"value": counters[name], "counter": name, "raw": res["result"].get("raw", "")}
        else:
            available = list(counters.keys())
            res = {"ok": False, "error": f"Counter '{name}' not found. Available: {available}"}
    return _as_json(res), []

def t_request_gap_ownership(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("gaprequest", [], justification=j)
    return _as_json(res), []

# ===========================================================================
# CAT-7 · Run state
# ===========================================================================

def t_get_element(args: dict) -> tuple[str, list[str]]:
    res = audited_call("p_element", [], justification="")
    return _as_json(res), []

def t_get_scan_number(args: dict) -> tuple[str, list[str]]:
    res = audited_call("scan_n", [], justification="")
    return _as_json(res), []

def t_get_current_datafile(args: dict) -> tuple[str, list[str]]:
    res = audited_call("p_datafile", [], justification="")
    return _as_json(res), []

def t_get_plotselected_counter(args: dict) -> tuple[str, list[str]]:
    res = audited_call("plotselected", [], justification="")
    return _as_json(res), []

def t_abort_current_scan(args: dict) -> tuple[str, list[str]]:
    j = (args.get("justification") or "").strip()
    res = audited_call("abort", [], justification=j)
    return _as_json(res), []

def t_recent_actions(args: dict) -> tuple[str, list[str]]:
    experiment_id = runtime_state.get_experiment_id()
    return _as_json(recent_actions(limit=int(args.get("limit", 20)),
                                   experiment_id=experiment_id)), []

# ---- Data / analysis / plotting handlers (formerly executor.py if/elif) ----

def _analyze_with(
    file_name,
    analyzer,
    e_min=None,
    e_max=None,
    scan_numbers=None,
    include_raw_counts: bool = False,
    counter=None,
    normalization: str = "edge_step",
):
    """Shared shape for convergence and efficiency: load normalized arrays
    (optionally windowed to [e_min, e_max] and/or restricted to scan_numbers),
    run analyzer, attach context.

    If include_raw_counts is True, also load the raw active-counter rate stack
    over the SAME energy window and pass it to the analyzer as
    raw_counts_per_point. The analyzer must accept that kwarg.

    ``counter``/``normalization`` are threaded to ``get_normalized_scan_arrays``
    so callers can override the auto-picked counter and pick a non-edge-step
    normalization for XRS. See ``ref counter-selection``.
    """
    try:
        combined, file_name, counter, used_scans = scan_data.get_normalized_scan_arrays(
            file_name, e_min=e_min, e_max=e_max, scan_numbers=scan_numbers,
            counter=counter, normalization=normalization,
        )
    except ValueError as e:
        return {"error": str(e)}
    if len(used_scans) < 2:
        return {"error": f"Need at least 2 scans, found {len(used_scans)}."}
    counter_warning = combined.attrs.get("counter_warning")

    # Drop rows with NaN in any scan to keep a common grid
    combined_clean = combined.dropna()
    scan_data_2d = combined_clean.values.T.tolist()

    kwargs = {}
    if include_raw_counts:
        try:
            raw_combined, _, _, raw_used = scan_data.get_raw_counter_arrays(
                file_name, scan_numbers=used_scans, counter=counter,
            )
            # Align raw counts to the same energy grid as the windowed normalized stack
            raw_aligned = raw_combined.reindex(combined_clean.index)
            count_times = raw_combined.attrs.get("count_times", [1.0] * len(raw_used))
            # Convert rate -> per-rep total counts at each point: rate * count_time
            raw_total = raw_aligned.values * np.array(count_times)[np.newaxis, :]
            kwargs["raw_counts_per_point"] = raw_total.T.tolist()
        except Exception as e:
            logger.warning("Could not load raw counts for Poisson floor: %s", e)

    result = analyzer(scan_data_2d, **kwargs) if kwargs else analyzer(scan_data_2d)
    if "error" in result:
        return result
    result["file_name"] = file_name
    result["active_counter"] = counter
    result["normalization"] = combined.attrs.get("normalization", normalization)
    result["scan_numbers"] = used_scans
    if counter_warning:
        result["counter_warning"] = counter_warning
    if e_min is not None and e_max is not None:
        result["energy_window"] = [e_min, e_max]
    return result

def t_get_latest_scan(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    entries = scan_data.list_processed_scans(limit=1)
    if not entries:
        return "No processed scans found.", images_b64
    entry = entries[0]
    trimmed = {
        k: entry[k]
        for k in ("file_name", "scan_number", "scan_command", "date_time", "num_points")
        if k in entry
    }
    return json.dumps(trimmed, indent=2), images_b64

def t_list_scans(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    result = scan_data.list_processed_scans(limit=arguments.get("limit", 20))
    if not result and not bl_config.SCAN_DIR_CONFIGURED:
        # An empty list would read as "this beamtime has no scans". Say what
        # is actually true: nothing is configured to read from. There is no
        # bundled sample data to fall back to.
        return json.dumps({
            "scans": [],
            "error": (
                f"No scan directory configured: BL_SCAN_DIR={bl_config.BL_SCAN_DIR} "
                "is not an existing directory containing a dated (YYYY-mm_*) "
                "run directory. Set BL_SCAN_DIR to your scan file root."
            ),
            **bl_config.data_dir_status(),
        }, indent=2), images_b64
    return json.dumps(result, indent=2), images_b64

def t_read_scan(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    file_name = arguments.get("file_name", "")
    scan_number = arguments.get("scan_number", 1)
    meta = scan_data.get_scan_metadata(file_name, scan_number)
    if not meta:
        return "Scan not found.", images_b64
    df, reason = scan_data.read_processed_scan_ex(file_name, scan_number)
    if df is not None:
        meta["data"] = scan_data.df_to_llm_text(df)
    else:
        # Not-found vs corrupt are different situations for the agent:
        # metadata exists, so say WHY the data itself is unavailable.
        meta["data_error"] = (
            f"Scan metadata found but scan data could not be read "
            f"({reason or 'unknown'})."
        )
    return json.dumps(meta, indent=2), images_b64

def t_get_latest_log_entries(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    result = log_reader.get_latest_log_entries(lines=arguments.get("lines", 100))
    return (
        json.dumps(result, indent=2) if result else "No log files found.",
        images_b64,
    )

def t_search_logs(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    result = log_reader.search_logs(
        arguments.get("query", ""),
        max_results=arguments.get("max_results", 50),
    )
    return json.dumps(result, indent=2), images_b64

def t_list_logs(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    result = log_reader.list_logs(limit=arguments.get("limit", 20))
    return json.dumps(result, indent=2), images_b64

def t_get_active_counter(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    result = scan_data.get_active_counter(
        arguments.get("file_name", ""),
        arguments.get("scan_number", 1),
    )
    return (
        json.dumps(result, indent=2) if result else "Scan not found.",
        images_b64,
    )

def t_get_scan_deadtime(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    result = scan_data.get_scan_deadtime(
        arguments.get("file_name", ""),
        arguments.get("scan_number", 1),
    )
    return (
        json.dumps(result, indent=2, default=str)
        if result
        else "Scan not found or no dead time data available.",
        images_b64,
    )

def t_normalize_scan(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    result = scan_data.edge_step_normalize_scan(
        arguments.get("file_name", ""),
        arguments.get("scan_number", 1),
        counter=arguments.get("counter"),
        normalize_by=arguments.get("normalize_by", "I0"),
    )
    return (
        json.dumps(result, indent=2) if result else "Scan not found.",
        images_b64,
    )

def t_average_scans(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    file_name = arguments.get("file_name")
    e_min = arguments.get("e_min")
    e_max = arguments.get("e_max")
    weighting = arguments.get("weighting", "equal")
    counter = arguments.get("counter")
    normalization = arguments.get("normalization", "edge_step")
    if file_name:
        result = scan_data.average_energy_scans(
            file_name=file_name, e_min=e_min, e_max=e_max, weighting=weighting,
            counter=counter, normalization=normalization,
        )
    else:
        result = scan_data.average_latest_energy_scans(
            e_min=e_min, e_max=e_max, weighting=weighting,
            counter=counter, normalization=normalization,
        )
    return json.dumps(result, indent=2), images_b64

def t_analyze_convergence(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.science.fitting.similarity import analyze_scan_quality
    e_min = arguments.get("e_min")
    e_max = arguments.get("e_max")
    if e_min is None or e_max is None:
        return json.dumps({"error": "e_min and e_max are required"}), []
    result = _analyze_with(
        arguments.get("file_name"),
        analyze_scan_quality,
        e_min=e_min,
        e_max=e_max,
        counter=arguments.get("counter"),
        normalization=arguments.get("normalization", "edge_step"),
    )
    return json.dumps(result, indent=2, default=str), images_b64

def t_analyze_efficiency(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.science.statistics.efficiency import analyze_scan_efficiency
    e_min = arguments.get("e_min")
    e_max = arguments.get("e_max")
    if e_min is None or e_max is None:
        return json.dumps({"error": "e_min and e_max are required"}), []
    result = _analyze_with(
        arguments.get("file_name"),
        analyze_scan_efficiency,
        e_min=e_min,
        e_max=e_max,
        include_raw_counts=bool(arguments.get("include_poisson_floor", True)),
        counter=arguments.get("counter"),
        normalization=arguments.get("normalization", "edge_step"),
    )
    return json.dumps(result, indent=2, default=str), images_b64

def t_analyze_feature_evolution(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.science.statistics.features import (
        analyze_feature_evolution,
    )
    file_name = arguments.get("file_name")
    e_min = arguments.get("e_min")
    e_max = arguments.get("e_max")
    statistic = arguments.get("statistic", "max")
    sem_target = float(arguments.get("sem_threshold_frac", 0.01))
    drift_target = float(arguments.get("drift_threshold_frac", 0.01))
    if e_min is None or e_max is None:
        return (
            json.dumps({
                "error": "analyze_feature_evolution requires e_min and e_max (numeric eV bounds)."
            }, indent=2),
            images_b64,
        )
    try:
        combined, file_name, counter, used_scans = (
            scan_data.get_normalized_scan_arrays(
                file_name,
                counter=arguments.get("counter"),
                normalization=arguments.get("normalization", "edge_step"),
            )
        )
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), images_b64
    counter_warning = combined.attrs.get("counter_warning")
    combined = combined.dropna()
    energy = combined.index.values.tolist()
    scan_2d = combined.values.T.tolist()
    result = analyze_feature_evolution(
        scan_2d, energy, e_min, e_max, statistic=statistic,
        sem_threshold_frac=sem_target, drift_threshold_frac=drift_target,
    )
    if isinstance(result, dict):
        result.setdefault("file_name", file_name)
        result.setdefault("active_counter", counter)
        result.setdefault("scan_numbers", used_scans)
        if counter_warning:
            result.setdefault("counter_warning", counter_warning)
    return json.dumps(result, indent=2, default=str), images_b64

def t_group_scans_by_spot(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    file_name = arguments.get("file_name")
    tol_mm = float(arguments.get("tol_mm", 0.05))
    if not file_name:
        return (
            json.dumps({"error": "file_name is required."}, indent=2),
            images_b64,
        )
    result = scan_data.group_scans_by_spot(file_name, tol_mm=tol_mm)
    return json.dumps(result, indent=2, default=str), images_b64

def t_analyze_per_spot(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.science.statistics.efficiency import (
        analyze_scan_efficiency,
    )
    from beamtimehero_cli.science.statistics.features import (
        heterogeneity_f_statistic,
    )
    file_name = arguments.get("file_name")
    e_min = arguments.get("e_min")
    e_max = arguments.get("e_max")
    tol_mm = float(arguments.get("tol_mm", 0.05))
    if not file_name:
        return (
            json.dumps({"error": "file_name is required."}, indent=2),
            images_b64,
        )
    if e_min is None or e_max is None:
        return (
            json.dumps({
                "error": "analyze_per_spot requires e_min and e_max (numeric eV bounds for the feature window)."
            }, indent=2),
            images_b64,
        )
    grouping = scan_data.group_scans_by_spot(file_name, tol_mm=tol_mm)
    if "error" in grouping:
        return json.dumps(grouping, indent=2), images_b64

    per_spot_results = []
    per_spot_arrays = []
    for spot in grouping["spots"]:
        if spot["spot_id"] == -1 or spot["n_scans"] < 2:
            continue
        try:
            combined, _, counter, used = scan_data.get_normalized_scan_arrays(
                file_name,
                e_min=e_min,
                e_max=e_max,
                scan_numbers=spot["scan_numbers"],
                counter=arguments.get("counter"),
                normalization=arguments.get("normalization", "edge_step"),
            )
        except ValueError as e:
            per_spot_results.append({
                "spot_id": spot["spot_id"],
                "error": str(e),
            })
            continue
        clean = combined.dropna()
        arr_2d = clean.values.T.tolist()
        per_spot_arrays.append(arr_2d)
        eff = analyze_scan_efficiency(arr_2d)
        per_spot_results.append({
            "spot_id": spot["spot_id"],
            "center": spot["center"],
            "scan_numbers": spot["scan_numbers"],
            "n_scans": spot["n_scans"],
            "verdict": eff.get("verdict"),
            "cv_mean_pct": eff.get("cv_mean_pct"),
            "final_convergence": eff.get("convergence", {}).get(
                "cumulative_convergence", [None]
            )[-1],
        })

    heterogeneity = None
    if len(per_spot_arrays) >= 2:
        # Trim each spot's stack to the minimum n_points across spots
        min_pts = min(len(a[0]) for a in per_spot_arrays)
        trimmed = [[row[:min_pts] for row in a] for a in per_spot_arrays]
        heterogeneity = heterogeneity_f_statistic(trimmed)

    return (
        json.dumps({
            "file_name": file_name,
            "energy_window": [e_min, e_max] if (e_min is not None and e_max is not None) else None,
            "tol_mm": tol_mm,
            "n_spots_analyzed": len(per_spot_results),
            "per_spot": per_spot_results,
            "heterogeneity": heterogeneity,
        }, indent=2, default=str),
        images_b64,
    )

def t_plot_averaged_scans(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    file_names = arguments.get("file_names", [])
    if not file_names:
        return "Error: file_names array must not be empty.", images_b64
    fig, summary = plotting.plot_averaged_scans_overlay(
        file_names,
        counter=arguments.get("counter"),
        normalization=arguments.get("normalization", "edge_step"),
    )
    if fig:
        images_b64.append(fig_to_base64(fig))
        import matplotlib.pyplot as plt
        plt.close(fig)
    return summary, images_b64

def t_plot_scan(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    fig, summary = plotting.plot_scan(
        arguments.get("file_name", ""),
        arguments.get("scan_number", 1),
        counter=arguments.get("counter"),
        normalize_by=arguments.get("normalize_by"),
    )
    if fig:
        images_b64.append(fig_to_base64(fig))
        import matplotlib.pyplot as plt
        plt.close(fig)
    return summary, images_b64

def t_plot_scan_stack(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    fig, summary = plotting.plot_scan_stack(
        arguments.get("file_name", ""),
        e_min=arguments.get("e_min"),
        e_max=arguments.get("e_max"),
        counter=arguments.get("counter"),
        normalization=arguments.get("normalization", "edge_step"),
    )
    if fig:
        images_b64.append(fig_to_base64(fig))
        import matplotlib.pyplot as plt
        plt.close(fig)
    return summary, images_b64

def t_plot_first_half_vs_second_half(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    fig, summary = plotting.plot_first_half_vs_second_half(
        arguments.get("file_name", ""),
        e_min=arguments.get("e_min"),
        e_max=arguments.get("e_max"),
        counter=arguments.get("counter"),
        normalization=arguments.get("normalization", "edge_step"),
    )
    if fig:
        images_b64.append(fig_to_base64(fig))
        import matplotlib.pyplot as plt
        plt.close(fig)
    return summary, images_b64

def t_plot_running_average(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    fig, summary = plotting.plot_running_average(
        arguments.get("file_name", ""),
        e_min=arguments.get("e_min"),
        e_max=arguments.get("e_max"),
        counter=arguments.get("counter"),
        normalization=arguments.get("normalization", "edge_step"),
    )
    if fig:
        images_b64.append(fig_to_base64(fig))
        import matplotlib.pyplot as plt
        plt.close(fig)
    return summary, images_b64

def t_plot_feature_evolution(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    fig, summary = plotting.plot_feature_evolution(
        arguments.get("file_name", ""),
        e_min=arguments.get("e_min"),
        e_max=arguments.get("e_max"),
        statistic=arguments.get("statistic", "max"),
    )
    if fig:
        images_b64.append(fig_to_base64(fig))
        import matplotlib.pyplot as plt
        plt.close(fig)
    return summary, images_b64

def t_plot_data(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.spec_data.plotting import plt

    x = arguments.get("x", [])
    series = [arguments.get("y", [])]
    for key in ("y2", "y3", "y4"):
        s = arguments.get(key)
        if s:
            series.append(s)

    if not x or not series[0]:
        return "Error: x and y arrays must not be empty.", images_b64

    for i, y_vals in enumerate(series):
        if len(y_vals) != len(x):
            return (
                f"Error: series {i+1} has {len(y_vals)} points but x has {len(x)}.",
                images_b64,
            )

    labels = arguments.get("labels", [])
    xlabel = arguments.get("xlabel", "")
    ylabel = arguments.get("ylabel", "")
    title = arguments.get("title", "")

    fig, ax = plt.subplots(figsize=(10, 6))
    for i, y_vals in enumerate(series):
        label = labels[i] if i < len(labels) else None
        ax.plot(x, y_vals, linewidth=1.2, label=label)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, fontsize=11)
    if labels:
        ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()

    images_b64.append(fig_to_base64(fig))
    plt.close(fig)

    summary = f"Plot generated: {title or 'untitled'} ({len(x)} points, {len(series)} series)"
    return summary, images_b64

def t_list_files(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.spec_data import local_data
    result = local_data.list_files(pattern=arguments.get("pattern", "*"))
    if not result:
        return "No files found in scan directory.", images_b64
    return json.dumps(result, indent=2), images_b64

def t_read_file(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.spec_data import local_data
    content = local_data.read_file(arguments.get("path", ""))
    return content, images_b64

def t_write_summary(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.spec_data import local_data
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"beamtimehero_conversation_summary_{ts}.txt"
    rel_path = local_data.write_file(filename, arguments.get("content", ""))
    return f"Summary saved: {rel_path}", images_b64

def t_write_macro(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.spec_data import local_data
    from datetime import datetime
    original = arguments.get("original_name", "macro")
    # Strip .mac extension if present to build new name
    base = original.rsplit(".mac", 1)[0] if original.endswith(".mac") else original
    ts = datetime.now().strftime("%Y-%m-%d")
    filename = f"{base}_heroic_{ts}.mac"
    rel_path = local_data.write_file(filename, arguments.get("content", ""))
    return f"Edited macro saved: {rel_path}", images_b64

def t_save_plan(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    import re as _re
    from beamtimehero_cli.config import PLANS_DIR
    filename = (arguments.get("filename") or "").strip()
    content = arguments.get("content") or ""
    overwrite = bool(arguments.get("overwrite", False))
    if not _re.match(r"^[A-Za-z0-9_\-.]+\.md$", filename) or filename.startswith("."):
        return json.dumps({
            "ok": False,
            "error": (
                "filename must match ^[A-Za-z0-9_\\-.]+\\.md$ and not start with "
                "'.' (no path separators, traversal, or hidden files)"
            ),
        }), images_b64
    target = (PLANS_DIR / filename).resolve()
    try:
        target.relative_to(PLANS_DIR.resolve())
    except ValueError:
        return json.dumps({
            "ok": False,
            "error": f"resolved path escapes PLANS_DIR: {target}",
        }), images_b64
    existed = target.exists()
    if existed and not overwrite:
        return json.dumps({
            "ok": False,
            "error": f"file exists: {filename}; pass overwrite=true to replace",
        }), images_b64
    target.write_text(content, encoding="utf-8")
    return json.dumps({
        "ok": True,
        "path": str(target),
        "bytes": len(content.encode("utf-8")),
        "overwrote": existed,
    }, indent=2), images_b64

def t_get_motor_config(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.spec_data.spec_config import get_motor_config
    return get_motor_config(), images_b64

def t_get_counter_config(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.spec_data.spec_config import get_counter_config
    return get_counter_config(), images_b64

def t_evaluate_spec_macro(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.spec_eval import evaluate_spec_macro
    result = evaluate_spec_macro(
        macro=arguments.get("macro", ""),
        preload=arguments.get("preload"),
        timeout_s=arguments.get("timeout_s", 30),
    )
    return json.dumps(result, indent=2), images_b64


# ---------------------------------------------------------------------------
# CAT-6 · Observation
# ---------------------------------------------------------------------------

def t_capture_sample_image(arguments: dict) -> tuple[str, list[str]]:
    import base64

    import requests

    from beamtimehero_cli.config import (
        SAMPLE_CAM_DEFAULT_QUALITY,
        SAMPLE_CAM_HOST,
        SAMPLE_CAM_PORT,
        SPEC_MOCK,
    )

    if SPEC_MOCK:
        return _as_json({"ok": True, "mock": True,
                         "note": "Camera not available in mock mode"}), []

    quality = max(1, min(100, int(arguments.get("quality", SAMPLE_CAM_DEFAULT_QUALITY))))
    url = f"http://{SAMPLE_CAM_HOST}:{SAMPLE_CAM_PORT}/snapshot.jpg"
    try:
        resp = requests.get(
            url,
            params={"resolution": "low", "quality": str(quality)},
            timeout=10,
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
    except requests.ConnectionError:
        return _as_json({"ok": False, "error": "Camera unavailable — connection refused"}), []
    except requests.Timeout:
        return _as_json({"ok": False, "error": "Camera request timed out"}), []
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "unknown"
        body = ""
        if e.response is not None:
            body = e.response.text[:200]
        return _as_json({"ok": False, "error": f"Camera HTTP {code}: {body}"}), []

    try:
        from datetime import datetime
        from beamtimehero_cli.config import DATA_DIR
        log_dir = DATA_DIR / "camera_log"
        log_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (log_dir / f"snapshot_{ts}.jpg").write_bytes(resp.content)
    except Exception:
        pass

    img_b64 = base64.b64encode(resp.content).decode("ascii")
    return _as_json({
        "ok": True,
        "resolution": "low",
        "quality": quality,
        "size_bytes": len(resp.content),
    }), [img_b64]


def t_get_reference_image(arguments: dict) -> tuple[str, list[str]]:
    import base64

    from beamtimehero_cli.tool_catalog.definitions import (
        REFERENCE_IMAGE_MANIFEST,
        _REFERENCE_IMAGES_DIR,
    )

    kind = (arguments.get("kind") or "").strip()
    available = sorted(REFERENCE_IMAGE_MANIFEST.keys())
    if kind not in REFERENCE_IMAGE_MANIFEST:
        return _as_json({
            "ok": False,
            "error": f"Unknown reference image kind: {kind!r}",
            "available": available,
        }), []

    entry = REFERENCE_IMAGE_MANIFEST[kind]
    path = _REFERENCE_IMAGES_DIR / entry["file"]
    if not path.is_file():
        return _as_json({
            "ok": False,
            "error": f"Reference image file missing: {entry['file']}",
        }), []

    img_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return _as_json({
        "ok": True,
        "kind": kind,
        "description": entry.get("description", ""),
        "size_bytes": path.stat().st_size,
    }), [img_b64]


# ===========================================================================
# CAT-10 · Scientific interpretation (HERFD XANES)
# ===========================================================================

def _interpretation_inputs(arguments: dict):
    """Load the averaged normalized spectrum + rep stack for interpretation.

    Returns ``(energy, mu, reps, edge_info, meta)``; raises ValueError on
    any data problem so handlers share one error path.
    """
    from beamtimehero_cli.science.reduce.reps import average_reps

    combined, file_name, counter, used_scans = scan_data.get_normalized_scan_arrays(
        arguments.get("file_name"), scan_numbers=arguments.get("scan_numbers"),
    )
    combined = combined.dropna()
    xas_policy.check_overlap(len(combined) if not combined.empty else 0)
    energy = combined.index.values.astype(float)
    reps = combined.values.T  # (n_scans, n_points)
    avg, _std, _weights = average_reps(combined)
    mu = avg.values.astype(float)

    edge_info = xas_policy.resolve_edge(
        energy, mu, element=arguments.get("element"), edge=arguments.get("edge"))

    meta = {
        "file_name": file_name,
        "active_counter": counter,
        "scan_numbers": used_scans,
        "n_scans": len(used_scans),
    }
    return energy, mu, reps, edge_info, meta


def _extract_for_interpretation(arguments: dict):
    """Shared descriptor pipeline for all CAT-10 handlers."""
    from beamtimehero_cli.science.xas import descriptors as interp_desc

    energy, mu, reps, edge_info, meta = _interpretation_inputs(arguments)
    wl_comps = int(arguments.get("white_line_components") or 0)
    if wl_comps <= 0:
        wl_comps = xas_policy.white_line_components_for(edge_info["family"])
    kwargs = {}
    pe_lo, pe_hi = arguments.get("pre_edge_e_min"), arguments.get("pre_edge_e_max")
    if pe_lo is not None and pe_hi is not None:
        kwargs["pre_edge_window_rel"] = xas_policy.window_rel_from_absolute(
            energy, mu, pe_lo, pe_hi)
    descriptors, arrays = interp_desc.extract_descriptors(
        energy, mu, reps=reps, edge_info=edge_info,
        normalization=arguments.get("normalization", "area"),
        assume_dilute=arguments.get("assume_dilute"),
        white_line_components=wl_comps,
        **kwargs,
    )
    return descriptors, arrays, meta


# Meta keys extract_xas_descriptors prepends to its descriptor bundle. When a
# capstone is handed that bundle back via ``descriptors=``, they are peeled off
# so the verdict output keeps the same file/scan provenance it would carry on
# the recompute path.
_INTERP_META_KEYS = ("file_name", "active_counter", "scan_numbers", "n_scans")


def _resolve_descriptors(arguments: dict):
    """Descriptor bundle for a capstone: reuse a precomputed one or extract.

    When ``arguments['descriptors']`` holds the JSON object that
    ``extract_xas_descriptors`` returns, that artifact is used verbatim and the
    load+normalize+fit pipeline is SKIPPED (the de-duplication) — ``arrays`` is
    then ``None`` since the numeric curves are not carried in the JSON. Absent
    it, behaves exactly as before: run ``_extract_for_interpretation``. Returns
    ``(descriptors, arrays, meta)``; raises ValueError only on the extract path.
    """
    precomputed = arguments.get("descriptors")
    if isinstance(precomputed, dict) and precomputed:
        descriptors = dict(precomputed)
        meta = {k: descriptors[k] for k in _INTERP_META_KEYS if k in descriptors}
        return descriptors, None, meta
    return _extract_for_interpretation(arguments)


def t_record_energy_calibration(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli.science.reduce.reps import average_reps
    from beamtimehero_cli import calibration_store
    from beamtimehero_cli.science.xas import descriptors as interp_desc
    from beamtimehero_cli.science.tables import edges as interp_edges

    element = arguments.get("element")
    edge = arguments.get("edge")
    if not element or not edge:
        return json.dumps({"error": "element and edge are required."}, indent=2), images_b64
    try:
        edge_meta = interp_edges.get_edge_info(element, edge)
        combined, file_name, counter, used_scans = scan_data.get_normalized_scan_arrays(
            arguments.get("file_name"), scan_numbers=arguments.get("scan_numbers"),
        )
        combined = combined.dropna()
        energy = combined.index.values.astype(float)
        avg, _std, _weights = average_reps(combined)
        e0 = interp_desc.find_e0(energy, avg.values.astype(float))
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), images_b64

    assigned = arguments.get("assigned_reference_ev")
    if assigned is None:
        assigned = edge_meta["tabulated_energy_ev"]
        ref_source = (
            edge_meta["tabulated_energy_source"]
            + " (foil-first-inflection convention)"
        )
    else:
        ref_source = "caller-assigned reference energy"
    record = calibration_store.record_calibration(
        element=element, edge=edge,
        measured_e0_ev=e0["e0_ev"], measured_e0_unc_ev=e0["e0_unc_ev"],
        assigned_reference_ev=float(assigned), reference_source=ref_source,
        file_name=file_name, scan_numbers=used_scans,
        e0_definition=e0["e0_definition"],
        notes=arguments.get("notes", ""),
    )
    result = {
        "recorded": True,
        "record": record,
        "active_counter": counter,
        "session": calibration_store.current_calibration(),
    }
    return json.dumps(result, indent=2, default=str), images_b64


def t_get_energy_calibration(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli import calibration_store
    return json.dumps(calibration_store.current_calibration(), indent=2, default=str), []


def t_extract_xas_descriptors(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    try:
        descriptors, arrays, meta = _extract_for_interpretation(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), images_b64
    from beamtimehero_cli.science.plots import xas as xas_plots
    edge_info = descriptors.get("edge") or {}
    fig = xas_plots.annotated_descriptor_figure(
        descriptors, arrays,
        title=f"{meta['file_name']} — {edge_info.get('element')} {edge_info.get('edge')}",
    )
    images_b64.append(fig_to_base64(fig))
    import matplotlib.pyplot as plt
    plt.close(fig)
    return json.dumps({**meta, **descriptors}, indent=2, default=str), images_b64


def _t_interpret(arguments: dict, engine_fn) -> tuple[str, list[str]]:
    from beamtimehero_cli import calibration_store
    try:
        descriptors, _arrays, meta = _resolve_descriptors(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    verdict = engine_fn(descriptors, calibration_store.current_calibration())
    verdict.update(meta)
    return json.dumps(verdict, indent=2, default=str), []


def t_interpret_oxidation_state(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xas import interpret as interp_engine
    return _t_interpret(arguments, interp_engine.interpret_oxidation_state)


def t_interpret_coordination_geometry(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xas import interpret as interp_engine
    return _t_interpret(arguments, interp_engine.interpret_coordination_geometry)


def t_summarize_sample_chemistry(arguments: dict) -> tuple[str, list[str]]:
    images_b64: list[str] = []
    from beamtimehero_cli import calibration_store
    from beamtimehero_cli.science.xas import interpret as interp_engine
    from beamtimehero_cli.science.plots import xas as xas_plots
    try:
        descriptors, arrays, meta = _resolve_descriptors(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), images_b64
    summary = interp_engine.summarize_chemistry(
        descriptors, calibration_store.current_calibration(),
    )
    # The annotated plot needs the numeric curves; those exist only on the
    # recompute path. A precomputed `descriptors` artifact carries the verdict
    # inputs but not the arrays, so the plot is simply omitted then.
    if arrays is not None:
        edge_info = descriptors.get("edge") or {}
        fig = xas_plots.annotated_descriptor_figure(
            descriptors, arrays,
            title=f"{meta.get('file_name')} — {edge_info.get('element')} {edge_info.get('edge')} chemistry",
        )
        images_b64.append(fig_to_base64(fig))
        import matplotlib.pyplot as plt
        plt.close(fig)
    summary.update(meta)
    summary["descriptors"] = descriptors
    return json.dumps(summary, indent=2, default=str), images_b64


# ---------------------------------------------------------------------------
# CAT-10 · Atomic descriptor tools
#
# Each wraps ONE interpretation pure function on top of the shared
# `_interpretation_inputs` load/average/edge-resolution contract — the same
# contract the capstones use. They are the small, single-responsibility
# building blocks that `extract_xas_descriptors` (the source-of-truth bundle)
# and the interpret_* capstones compose; exposing them individually mirrors the
# XRS branch and lets an agent recompute exactly one piece without re-running
# the whole pipeline.
# ---------------------------------------------------------------------------

def _e0_and_normalize(energy, mu, edge_info: dict, arguments: dict):
    """Find E0 and apply the requested normalization to ``mu``.

    Mirrors the E0 + normalization step of ``extract_descriptors`` so a
    standalone fit tool and the capstone agree on their inputs. Returns
    ``(mu_normalized, e0_info, normalization_provenance)``.
    """
    from beamtimehero_cli.science.xas import descriptors as interp_desc
    from beamtimehero_cli.science.xas import normalize as interp_norm

    e0_info = interp_desc.find_e0(energy, mu)
    e0 = e0_info["e0_ev"]
    mode = arguments.get("normalization", "area")
    if mode == "area":
        mu_n, prov = interp_norm.area_normalize(energy, mu, e0)
    elif mode == "mback":
        einfo = edge_info or {}
        mu_n, prov = interp_norm.mback_normalize(
            energy, mu, e0, einfo.get("element"), einfo.get("edge"),
        )
    else:
        mu_n, prov = mu, interp_norm.edge_step_provenance()
    return mu_n, e0_info, prov


def _edge_context(edge_info: dict) -> dict:
    """Compact element/edge/family stamp for atomic-tool outputs."""
    edge_info = edge_info or {}
    return {k: edge_info.get(k) for k in ("element", "edge", "family")}


def t_identify_edge(arguments: dict) -> tuple[str, list[str]]:
    """What edge is this: element/edge auto-detect + family classification."""
    try:
        energy, _mu, _reps, edge_info, meta = _interpretation_inputs(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    out = {
        **meta,
        "energy_window_ev": [round(float(energy.min()), 3), round(float(energy.max()), 3)],
        "edge": edge_info,
    }
    return json.dumps(out, indent=2, default=str), []


def t_find_edge_e0(arguments: dict) -> tuple[str, list[str]]:
    """Edge position E0 (derivative-max + half-step + uncertainty)."""
    from beamtimehero_cli.science.xas import descriptors as interp_desc
    try:
        energy, mu, _reps, edge_info, meta = _interpretation_inputs(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    e0 = interp_desc.find_e0(energy, mu)
    return json.dumps({**meta, "edge": _edge_context(edge_info), "e0": e0},
                      indent=2, default=str), []


def t_normalize_xas_intensity(arguments: dict) -> tuple[str, list[str]]:
    """Area / MBACK / edge-step normalization of the averaged spectrum."""
    try:
        energy, mu, _reps, edge_info, meta = _interpretation_inputs(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    _mu_n, e0_info, prov = _e0_and_normalize(energy, mu, edge_info, arguments)
    out = {
        **meta,
        "edge": _edge_context(edge_info),
        "e0_ev": e0_info["e0_ev"],
        "normalization": arguments.get("normalization", "area"),
        "provenance": prov,
    }
    return json.dumps(out, indent=2, default=str), []


def t_fit_xas_pre_edge(arguments: dict) -> tuple[str, list[str]]:
    """Wilke-style pre-edge fit (centroid / area / components / BIC).

    Includes the core-hole re-broadened variant when the edge family carries a
    tabulated core width (the input a conventional-XANES calibration needs).
    """
    from beamtimehero_cli.science.xas import descriptors as interp_desc
    try:
        energy, mu, _reps, edge_info, meta = _interpretation_inputs(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    mu_n, e0_info, prov = _e0_and_normalize(energy, mu, edge_info, arguments)
    e0 = e0_info["e0_ev"]

    kwargs = {}
    pe_lo, pe_hi = arguments.get("pre_edge_e_min"), arguments.get("pre_edge_e_max")
    if pe_lo is not None and pe_hi is not None:
        kwargs["window_rel"] = xas_policy.window_rel_from_absolute(
            energy, mu, pe_lo, pe_hi)
    pre_edge = interp_desc.fit_pre_edge(energy, mu_n, e0, **kwargs)
    pre_edge.pop("_arrays", None)

    family = (edge_info or {}).get("family")
    core_width = (edge_info or {}).get("core_hole_width_ev")
    pre_edge_rebroadened = None
    if family in ("3d_K", "4d_K", "5d_K") and core_width and pre_edge.get("fit_ok"):
        mu_broad = interp_desc.rebroaden(energy, mu_n, core_width)
        pre_edge_rebroadened = interp_desc.fit_pre_edge(energy, mu_broad, e0, **kwargs)
        pre_edge_rebroadened.pop("_arrays", None)
        if pre_edge_rebroadened.get("fit_ok"):
            pre_edge_rebroadened["provenance"]["calibration_domain"] = "herfd_rebroadened"
            pre_edge_rebroadened["provenance"]["rebroadened_fwhm_ev"] = core_width

    out = {
        **meta,
        "edge": _edge_context(edge_info),
        "e0_ev": e0,
        "normalization": prov,
        "pre_edge": pre_edge,
        "pre_edge_rebroadened": pre_edge_rebroadened,
    }
    return json.dumps(out, indent=2, default=str), []


def t_fit_xas_white_line(arguments: dict) -> tuple[str, list[str]]:
    """White-line fit: energy / height / area (+ multi-peak structure)."""
    from beamtimehero_cli.science.xas import descriptors as interp_desc
    try:
        energy, mu, _reps, edge_info, meta = _interpretation_inputs(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    mu_n, e0_info, prov = _e0_and_normalize(energy, mu, edge_info, arguments)
    e0 = e0_info["e0_ev"]

    wl_comps = int(arguments.get("white_line_components") or 0)
    if wl_comps <= 0:
        wl_comps = xas_policy.white_line_components_for((edge_info or {}).get("family"))
    white_line = interp_desc.fit_white_line(energy, mu_n, e0, max_components=wl_comps)
    white_line.pop("_arrays", None)
    out = {
        **meta,
        "edge": _edge_context(edge_info),
        "e0_ev": e0,
        "normalization": prov,
        "white_line": white_line,
    }
    return json.dumps(out, indent=2, default=str), []


def t_assess_xas_quality(arguments: dict) -> tuple[str, list[str]]:
    """Glitch / saturation / self-absorption flags for the averaged spectrum.

    The XAS analogue of assess_xrs_quality: composes the quality.* checks that
    extract_xas_descriptors runs internally, exposed on their own.
    """
    from beamtimehero_cli.science.reduce import artifacts as interp_quality
    try:
        energy, mu, _reps, edge_info, meta = _interpretation_inputs(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    glitch_mask = interp_quality.detect_glitches(energy, mu)
    n_glitch = int(glitch_mask.sum())
    saturation = interp_quality.detect_saturation(mu)
    self_abs = interp_quality.self_absorption_assessment(arguments.get("assume_dilute"))

    flags: list[str] = []
    if n_glitch:
        flags.append("glitches_detected")
    if saturation["saturated"]:
        flags.append("saturation_suspected")
    if self_abs["risk"] == "unknown":
        flags.append("self_absorption_risk")

    out = {
        **meta,
        "edge": _edge_context(edge_info),
        "glitches": {"n_glitch_points": n_glitch, "detected": bool(n_glitch)},
        "saturation": saturation,
        "self_absorption": self_abs,
        "flags": flags,
    }
    return json.dumps(out, indent=2, default=str), []


def t_detect_per_scan_drift(arguments: dict) -> tuple[str, list[str]]:
    """Beam-damage / photoreduction test: monotonic per-scan descriptor trends."""
    from beamtimehero_cli.science.xas import descriptors as interp_desc
    try:
        energy, mu, reps, edge_info, meta = _interpretation_inputs(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    mu_n, e0_info, _prov = _e0_and_normalize(energy, mu, edge_info, arguments)
    e0 = e0_info["e0_ev"]
    wl = interp_desc.fit_white_line(energy, mu_n, e0, max_components=1)
    wl_energy = wl.get("white_line_energy_ev") if wl.get("fit_ok") else None
    window = (e0 + xas_policy.PRE_EDGE_WINDOW_REL[0],
              e0 + xas_policy.PRE_EDGE_WINDOW_REL[1])
    trends = interp_desc.per_scan_descriptor_trends(energy, reps, e0, wl_energy, window)
    return json.dumps({**meta, "edge": _edge_context(edge_info), **trends},
                      indent=2, default=str), []


# ---------------------------------------------------------------------------
# CAT-10 · Cross-file XAS comparison (LCF, energy registration, differences)
#
# Every spectrum loads through scans.get_normalized_scan_arrays, so SPEC
# files, collector groups AND already-merged two-column ASCII files (e.g.
# MERGE/*.dat) are all accepted.
# ---------------------------------------------------------------------------

def _load_spectrum_entries(entries, counter=None, normalization="edge_step",
                           kind="spectra"):
    """Load ``[{file_name, scan_numbers?, label?}, ...]`` into spectrum dicts.

    Each result: ``{label, file_name, energy, mu, counter, normalization,
    scan_numbers, e0_ev}`` — reps averaged (equal weights), E0 from the
    derivative maximum (None when it cannot be fit). Raises ValueError
    naming the failing entry so handlers share one error path.
    """
    from beamtimehero_cli.science.reduce.reps import average_reps
    from beamtimehero_cli.science.xas.e0 import find_e0

    out = []
    for i, entry in enumerate(entries):
        entry = entry or {}
        file_name = entry.get("file_name")
        if not file_name:
            raise ValueError(f"{kind}[{i}]: file_name is required.")
        try:
            combined, file_name, ctr, used = scan_data.get_normalized_scan_arrays(
                file_name, scan_numbers=entry.get("scan_numbers"),
                counter=counter, normalization=normalization,
            )
        except ValueError as e:
            raise ValueError(f"'{file_name}': {e}") from None
        combined = combined.dropna()
        xas_policy.check_reference_points(len(combined), file_name)
        energy = combined.index.values.astype(float)
        if combined.shape[1] == 1:
            mu = combined.iloc[:, 0].values.astype(float)
        else:
            avg, _std, _weights = average_reps(combined)
            mu = avg.values.astype(float)
        try:
            e0 = round(float(find_e0(energy, mu)["e0_ev"]), 4)
        except Exception:  # noqa: BLE001 — E0 is advisory here
            e0 = None
        out.append({
            "label": entry.get("label") or file_name,
            "file_name": file_name,
            "energy": energy,
            "mu": mu,
            "counter": combined.attrs.get("counter", ctr),
            "normalization": combined.attrs.get("normalization", normalization),
            "scan_numbers": used,
            "e0_ev": e0,
        })
    return out


# References measured on visibly different energy calibrations make LCF
# fractions meaningless; ~1 eV of E0 spread is well beyond mono drift.
_LCF_E0_SPREAD_WARN_EV = 1.0

_LCF_CALIBRATION_CAVEAT = (
    "LCF is only meaningful on a common energy calibration — compare the "
    "per-file E0s reported here and run align_spectra first if they disagree."
)


def t_compare_xas_to_references(arguments: dict) -> tuple[str, list[str]]:
    """XANES LCF: nnls fit of a target spectrum to measured references."""
    from beamtimehero_cli.science.xas.compare import compare_to_references

    counter = arguments.get("counter")
    normalization = arguments.get("normalization", "edge_step")
    try:
        target = _load_spectrum_entries(
            [{"file_name": arguments.get("file_name"),
              "scan_numbers": arguments.get("scan_numbers"),
              "label": "target"}],
            counter=counter, normalization=normalization, kind="target",
        )[0]
        refs = _load_spectrum_entries(
            arguments.get("references") or [],
            counter=counter, normalization=normalization, kind="references",
        )
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    if not refs:
        return json.dumps(
            {"error": "references must hold at least one {file_name, ...} entry."},
            indent=2), []

    result = compare_to_references(
        target["energy"], target["mu"],
        [{"name": r["label"], "axis": r["energy"], "intensity": r["mu"]}
         for r in refs],
    )
    if "error" in result:
        return json.dumps(result, indent=2), []

    # Reconstruct the fitted curve from the raw nnls weights for the plot
    # and an interpretable residual RMS (lcf reports only the nnls norm).
    energy = target["energy"]
    cols = np.vstack([
        np.interp(energy, r["energy"], r["mu"], left=np.nan, right=np.nan)
        for r in refs
    ]).T
    weights = np.array([c["raw_weight"] for c in result["components"]], dtype=float)
    fit = cols @ weights
    residual = target["mu"] - fit
    good = np.isfinite(residual)
    result["residual_rms"] = (
        round(float(np.sqrt(np.mean(np.square(residual[good])))), 6)
        if good.any() else None
    )

    result["target"] = {
        "file_name": target["file_name"],
        "scan_numbers": target["scan_numbers"],
        "counter": target["counter"],
        "normalization": target["normalization"],
        "e0_ev": target["e0_ev"],
    }
    result["reference_e0s_ev"] = {r["label"]: r["e0_ev"] for r in refs}
    ref_e0s = [r["e0_ev"] for r in refs if r["e0_ev"] is not None]
    spread = round(max(ref_e0s) - min(ref_e0s), 4) if len(ref_e0s) > 1 else 0.0
    result["reference_e0_spread_ev"] = spread
    result["calibration_caveat"] = _LCF_CALIBRATION_CAVEAT
    if spread > _LCF_E0_SPREAD_WARN_EV:
        result["calibration_warning"] = (
            f"Reference E0s spread over {spread:.2f} eV (> {_LCF_E0_SPREAD_WARN_EV:g} eV) "
            "— the references are not on a common energy calibration and the "
            "fractions are unreliable. Check registration with align_spectra."
        )

    images_b64: list[str] = []
    from beamtimehero_cli.science.plots import xas as xas_plots
    fig, _plot_summary = xas_plots.plot_lcf_fit(
        energy[good], target["mu"][good], fit[good], residual[good],
        result["components"],
        title=f"{target['file_name']} — XANES LCF ({len(refs)} references)",
    )
    images_b64.append(fig_to_base64(fig))
    _close(fig)
    return json.dumps(result, indent=2, default=str), images_b64


def t_align_spectra(arguments: dict) -> tuple[str, list[str]]:
    """Cross-file E0 registration: report per-file shifts + aligned overlay."""
    from beamtimehero_cli.science.xas import compare as xas_math

    entries = arguments.get("spectra") or []
    if len(entries) < 2:
        return json.dumps(
            {"error": "spectra must hold at least 2 {file_name, ...} entries."},
            indent=2), []
    try:
        specs = _load_spectrum_entries(
            entries, counter=arguments.get("counter"),
            normalization=arguments.get("normalization", "edge_step"),
        )
        records = xas_math.align_spectra(
            [(s["energy"], s["mu"]) for s in specs],
            target_e0=arguments.get("target_e0"),
        )
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []

    labels = [s["label"] for s in specs]
    out = {
        "target_e0_ev": records[0]["target_e0"],
        "target_source": records[0]["target_source"],
        "max_shift_ev": xas_math.MAX_ALIGN_SHIFT_EV,
        "spectra": [
            {
                "label": s["label"],
                "file_name": s["file_name"],
                "scan_numbers": s["scan_numbers"],
                "counter": s["counter"],
                "e0_before": r["e0_before"],
                "shift_applied": r["shift_applied"],
                "e0_after": r["e0_after"],
                "refused": r["refused"],
                **({"note": r["note"]} if r["note"] else {}),
            }
            for s, r in zip(specs, records)
        ],
        "note": (
            "Shifts are REPORTED, not persisted — no files were modified. "
            "Quote the shift_applied values when comparing absolute energies "
            "across these files in reports."
        ),
    }
    from beamtimehero_cli.science.plots import xas as xas_plots
    fig, _plot_summary = xas_plots.plot_alignment_overlay(records, labels)
    images_b64 = [fig_to_base64(fig)]
    _close(fig)
    return json.dumps(out, indent=2, default=str), images_b64


def t_difference_spectrum(arguments: dict) -> tuple[str, list[str]]:
    """A − B difference spectrum on a common grid, E0-aligned by default."""
    from beamtimehero_cli.science.xas import compare as xas_math

    counter = arguments.get("counter")
    normalization = arguments.get("normalization", "edge_step")
    try:
        spec_a, spec_b = _load_spectrum_entries(
            [{"file_name": arguments.get("file_name_a"),
              "scan_numbers": arguments.get("scan_numbers_a")},
             {"file_name": arguments.get("file_name_b"),
              "scan_numbers": arguments.get("scan_numbers_b")}],
            counter=counter, normalization=normalization,
        )
        result = xas_math.difference_spectrum(
            spec_a["energy"], spec_a["mu"], spec_b["energy"], spec_b["mu"],
            align=bool(arguments.get("align", True)),
        )
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []

    label_a = arguments.get("label_a") or spec_a["file_name"]
    label_b = arguments.get("label_b") or spec_b["file_name"]
    out = {
        "a": {"file_name": spec_a["file_name"], "scan_numbers": spec_a["scan_numbers"],
              "counter": spec_a["counter"], "e0_ev": spec_a["e0_ev"]},
        "b": {"file_name": spec_b["file_name"], "scan_numbers": spec_b["scan_numbers"],
              "counter": spec_b["counter"], "e0_ev": spec_b["e0_ev"]},
        "aligned": result["aligned"],
        "alignment": result["alignment"],
        **result["stats"],
    }
    from beamtimehero_cli.science.plots import xas as xas_plots
    fig, _plot_summary = xas_plots.plot_difference_spectrum(
        result, label_a=label_a, label_b=label_b,
    )
    images_b64 = [fig_to_base64(fig)]
    _close(fig)
    return json.dumps(out, indent=2, default=str), images_b64


# ---------------------------------------------------------------------------
# XRS (X-ray Raman) processing tools — energy-loss axis, NOT edge-step
# ---------------------------------------------------------------------------

def _close(fig):
    import matplotlib.pyplot as plt
    plt.close(fig)


def _xrs_spectrum_summary(loss, values) -> dict:
    """Compact JSON-safe summary of an XRS loss spectrum (arrays are too big
    to dump; the plot carries the visual)."""
    loss = np.asarray(loss, dtype=float)
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    if not finite.any():
        return {"n_points": int(len(loss)), "error": "all-NaN spectrum"}
    i_peak = int(np.nanargmax(values))
    return {
        "n_points": int(len(loss)),
        "loss_min_ev": round(float(loss[finite].min()), 3),
        "loss_max_ev": round(float(loss[finite].max()), 3),
        "peak_value": round(float(values[i_peak]), 6),
        "peak_loss_ev": round(float(loss[i_peak]), 3),
    }


def t_calibrate_energy_loss(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.spec_data import xrs_data
    from beamtimehero_cli.science.plots import xrs as xrs_plots
    images_b64: list[str] = []
    file_name = arguments.get("file_name", "")
    scan_number = arguments.get("scan_number")
    counter = arguments.get("counter")
    if not file_name or scan_number is None:
        return json.dumps({"error": "file_name and scan_number (the elastic ascan mono) are required."}), []
    try:
        result = xrs_data.calibrate_energy_loss(file_name, int(scan_number), counter=counter)
        energy, signal = xrs_data.load_scan_signal(file_name, int(scan_number), result["counter"])
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    fig, _summary = xrs_plots.plot_elastic_fit(
        energy, signal, result, file_name, int(scan_number), result["counter"])
    images_b64.append(fig_to_base64(fig))
    _close(fig)
    return json.dumps(result, indent=2, default=str), images_b64


def t_build_loss_axis(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.spec_data import xrs_data
    from beamtimehero_cli.science.plots import xrs as xrs_plots
    images_b64: list[str] = []
    file_name = arguments.get("file_name", "")
    scan_number = arguments.get("scan_number")
    if not file_name or scan_number is None:
        return json.dumps({"error": "file_name and scan_number are required."}), []
    try:
        result = xrs_data.reduce_xrs(
            file_name=file_name, counter=arguments.get("counter"),
            scan_numbers=[int(scan_number)],
            elastic_center_ev=arguments.get("elastic_center_ev"),
        )
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    fig = xrs_plots.plot_loss_spectrum(
        result["loss"], result["mean"], None,
        f"{file_name} #{scan_number} — {result['counter']} on {result['axis']}")
    images_b64.append(fig_to_base64(fig))
    _close(fig)
    out = {k: result[k] for k in ("file_name", "counter", "elastic_center_ev",
                                   "elastic_center_source", "axis", "counter_warning")
           if result.get(k) is not None}
    out["spectrum"] = _xrs_spectrum_summary(result["loss"], result["mean"])
    return json.dumps(out, indent=2, default=str), images_b64


def _reduce_for_tool(arguments):
    """Shared: reduce_xrs from tool arguments (average reps on the loss axis)."""
    from beamtimehero_cli.spec_data import xrs_data
    return xrs_data.reduce_xrs(
        file_name=arguments.get("file_name"),
        counter=arguments.get("counter"),
        scan_numbers=arguments.get("scan_numbers"),
        elastic_center_ev=arguments.get("elastic_center_ev"),
    )


def t_average_xrs_scans(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xrs import reduce as xrs
    from beamtimehero_cli.science.plots import xrs as xrs_plots
    images_b64: list[str] = []
    try:
        r = _reduce_for_tool(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    loss, mean, sem = r["loss"], r["mean"], r["sem"]
    ylabel = "signal / I0"
    normalization = arguments.get("normalization", "none")
    if normalization == "area":
        norm = xrs.area_normalize(loss, mean, arguments.get("e_min"), arguments.get("e_max"))
        mean = norm["normalized"]
        sem = sem / norm["area"] if norm["area"] else sem
        ylabel = "area-normalized signal"
    fig = xrs_plots.plot_loss_spectrum(
        loss, mean, sem,
        f"{r['file_name']} — averaged XRS ({r['n_reps']} reps, {r['counter']})", ylabel)
    images_b64.append(fig_to_base64(fig))
    _close(fig)
    out = {
        "file_name": r["file_name"], "counter": r["counter"],
        "n_reps": r["n_reps"], "scan_numbers": r["scan_numbers"],
        "elastic_center_ev": r["elastic_center_ev"],
        "elastic_center_source": r["elastic_center_source"],
        "axis": r["axis"], "normalization": normalization,
        "spectrum": _xrs_spectrum_summary(loss, mean),
    }
    if r.get("counter_warning"):
        out["counter_warning"] = r["counter_warning"]
    if r["elastic_center_ev"] is None:
        out["note"] = ("No elastic calibration — run calibrate_energy_loss on the "
                       "elastic (ascan mono) scan so the axis is true energy loss.")
    return json.dumps(out, indent=2, default=str), images_b64


def t_subtract_compton_background(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xrs import reduce as xrs
    from beamtimehero_cli.science.plots import xrs as xrs_plots
    images_b64: list[str] = []
    edge_lo, edge_hi = arguments.get("edge_lo"), arguments.get("edge_hi")
    if edge_lo is None or edge_hi is None:
        return json.dumps({"error": "edge_lo and edge_hi (energy-loss bounds of the edge feature) are required."}), []
    try:
        r = _reduce_for_tool(arguments)
        bg = xrs.subtract_compton_background(
            r["loss"], r["mean"], float(edge_lo), float(edge_hi),
            model=arguments.get("model", xrs_policy.DEFAULT_BACKGROUND_MODEL))
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    subtracted = bg["subtracted"]
    normalization = arguments.get("normalization", "none")
    if normalization == "area":
        norm = xrs.area_normalize(r["loss"], subtracted, float(edge_lo), float(edge_hi))
        subtracted = norm["normalized"]
    fig = xrs_plots.plot_background_subtraction(
        r["loss"], r["mean"], bg["background"], subtracted, bg["edge_window"])
    images_b64.append(fig_to_base64(fig))
    _close(fig)
    out = {
        "file_name": r["file_name"], "counter": r["counter"], "n_reps": r["n_reps"],
        "elastic_center_ev": r["elastic_center_ev"], "axis": r["axis"],
        "background_model": bg["model"], "requested_model": bg["requested_model"],
        "edge_window": bg["edge_window"], "n_flank_points": bg["n_flank_points"],
        "normalization": normalization,
        "edge_spectrum": _xrs_spectrum_summary(r["loss"], subtracted),
        "provenance": bg["provenance"],
    }
    if r.get("counter_warning"):
        out["counter_warning"] = r["counter_warning"]
    return json.dumps(out, indent=2, default=str), images_b64


def t_normalize_xrs(arguments: dict) -> tuple[str, list[str]]:
    """Full reduce → Compton subtract → area/edge-jump normalize."""
    args = dict(arguments)
    args["normalization"] = arguments.get("normalization", "area")
    return t_subtract_compton_background(args)


def t_overlay_xrs_spectra(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xrs import reduce as xrs
    from beamtimehero_cli.spec_data import xrs_data
    from beamtimehero_cli.science.plots import xrs as xrs_plots
    images_b64: list[str] = []
    file_names = arguments.get("file_names", [])
    if not file_names:
        return json.dumps({"error": "file_names array must not be empty."}), []
    edge_lo, edge_hi = arguments.get("edge_lo"), arguments.get("edge_hi")
    model = arguments.get("model", xrs_policy.DEFAULT_BACKGROUND_MODEL)
    normalization = arguments.get("normalization", "area")
    spectra, reports = [], []
    for fn in file_names:
        try:
            r = xrs_data.reduce_xrs(
                file_name=fn, counter=arguments.get("counter"),
                elastic_center_ev=arguments.get("elastic_center_ev"))
            loss, inten = r["loss"], r["mean"]
            if edge_lo is not None and edge_hi is not None:
                bg = xrs.subtract_compton_background(loss, inten, float(edge_lo), float(edge_hi), model=model)
                inten = bg["subtracted"]
            if normalization == "area":
                inten = xrs.area_normalize(loss, inten, edge_lo, edge_hi)["normalized"]
            spectra.append((f"{fn} ({r['n_reps']} reps)", loss, inten))
            reports.append({"file_name": fn, "counter": r["counter"], "n_reps": r["n_reps"],
                            "counter_warning": r.get("counter_warning")})
        except ValueError as e:
            reports.append({"file_name": fn, "error": str(e)})
    if not spectra:
        return json.dumps({"error": "No spectra could be reduced.", "reports": reports}, indent=2), []
    ylabel = "area-normalized" if normalization == "area" else "signal / I0"
    fig = xrs_plots.plot_overlay(spectra, ylabel=ylabel)
    images_b64.append(fig_to_base64(fig))
    _close(fig)
    return json.dumps({"n_overlaid": len(spectra), "normalization": normalization,
                       "background_subtracted": edge_lo is not None,
                       "reports": reports}, indent=2, default=str), images_b64


def _load_crystal_channels(arguments):
    """Load per-crystal/ROI channel signals from one scan.
    Returns (loss_list, channel_list, counters, warning)."""
    from beamtimehero_cli.spec_data import xrs_data
    file_name = arguments.get("file_name", "")
    scan_number = arguments.get("scan_number")
    counters = arguments.get("counters")
    if not file_name or scan_number is None or not counters:
        raise ValueError("file_name, scan_number, and counters (list of channel names) are required.")
    center, _src = xrs_data._resolve_elastic_center(file_name, arguments.get("elastic_center_ev"))
    from beamtimehero_cli.science.xrs import calibrate as _xrs_cal
    loss_list, chan_list = [], []
    for c in counters:
        energy, signal = xrs_data.load_scan_signal(file_name, int(scan_number), c)
        loss_list.append(_xrs_cal.to_energy_loss(energy, center) if center is not None else energy)
        chan_list.append(signal)
    return loss_list, chan_list, counters, center


def t_sum_crystals(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xrs import reduce as xrs
    from beamtimehero_cli.science.plots import xrs as xrs_plots
    images_b64: list[str] = []
    try:
        loss_list, chan_list, counters, center = _load_crystal_channels(arguments)
        res = xrs.sum_crystals(loss_list, chan_list, reject=bool(arguments.get("reject", True)))
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    # interpolate channels onto the result grid for plotting
    on_grid = [np.interp(res["loss"], lo, ch, left=np.nan, right=np.nan)
               for lo, ch in zip(loss_list, chan_list)]
    keep = res["rejection"]["keep"]
    fig = xrs_plots.plot_crystal_sum(res["loss"], on_grid, res["summed"], keep, labels=counters)
    images_b64.append(fig_to_base64(fig))
    _close(fig)
    out = {
        "file_name": arguments.get("file_name"), "counters": counters,
        "elastic_center_ev": center,
        "n_channels_used": res["n_channels_used"], "n_channels_total": res["n_channels_total"],
        "rejected": [{"counter": counters[i], "reason": res["rejection"]["reasons"][i]}
                     for i in range(len(counters)) if not keep[i]],
        "summed_spectrum": _xrs_spectrum_summary(res["loss"], res["summed"]),
    }
    return json.dumps(out, indent=2, default=str), images_b64


def t_align_crystals(arguments: dict) -> tuple[str, list[str]]:
    """Report per-crystal alignment + outlier rejection WITHOUT summing."""
    from beamtimehero_cli.science.xrs import calibrate as _xrs_cal
    from beamtimehero_cli.science.xrs import reduce as xrs
    try:
        loss_list, chan_list, counters, center = _load_crystal_channels(arguments)
        grid = _xrs_cal.common_loss_grid(loss_list)
        on_grid = [np.interp(grid, lo, ch, left=np.nan, right=np.nan)
                   for lo, ch in zip(loss_list, chan_list)]
        rej = xrs.reject_outlier_channels(grid, on_grid)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    per = []
    for i, c in enumerate(counters):
        pc = rej["per_channel"][i] if i < len(rej["per_channel"]) else {}
        per.append({"counter": c, "kept": rej["keep"][i], "reason": rej["reasons"][i],
                    "snr": pc.get("snr"), "shape_deviation_sigma": pc.get("shape_deviation_sigma")})
    return json.dumps({
        "file_name": arguments.get("file_name"), "elastic_center_ev": center,
        "common_loss_range_ev": [round(float(grid.min()), 3), round(float(grid.max()), 3)],
        "n_channels": len(counters), "channels": per,
    }, indent=2, default=str), []


def t_tag_crystal_q(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xrs import calibrate as _xrs_cal
    incident = arguments.get("incident_energy_ev")
    two_thetas = arguments.get("two_thetas")
    if incident is None or not two_thetas:
        return json.dumps({"error": "incident_energy_ev and two_thetas (list, deg) are required."}), []
    counters = arguments.get("counters") or [f"ch{i}" for i in range(len(two_thetas))]
    try:
        qs = [_xrs_cal.q_from_two_theta(float(incident), float(tt)) for tt in two_thetas]
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    channels = [{"counter": counters[i] if i < len(counters) else f"ch{i}",
                 "two_theta_deg": float(two_thetas[i]), "q_inv_angstrom": round(qs[i], 4),
                 "regime": xrs_policy.q_regime(qs[i])}
                for i in range(len(two_thetas))]
    return json.dumps({
        "incident_energy_ev": float(incident),
        "q_range_inv_angstrom": [round(min(qs), 4), round(max(qs), 4)],
        "channels": channels,
        "note": ("Low q ≈ dipole (compare to XANES); high q turns on monopole/"
                 "quadrupole transitions. Group crystals by q for q-resolved analysis."),
    }, indent=2, default=str), []


# ---------------------------------------------------------------------------
# CAT-XRS · X-ray Raman scientific interpretation
# ---------------------------------------------------------------------------

def _xrs_edge_descriptors(arguments):
    """Reduce → (Compton subtract if edge window given) → extract XRS descriptors.

    Returns (descriptors, arrays, meta, resolution_fwhm_ev). Raises ValueError.
    """
    from beamtimehero_cli.science.xrs import reduce as xrs
    from beamtimehero_cli.science.xrs import descriptors as xd
    from beamtimehero_cli.science.tables import xrs_edges
    from beamtimehero_cli.spec_data import xrs_data

    r = xrs_data.reduce_xrs(
        file_name=arguments.get("file_name"), counter=arguments.get("counter"),
        scan_numbers=arguments.get("scan_numbers"),
        elastic_center_ev=arguments.get("elastic_center_ev"))
    loss, inten = r["loss"], r["mean"]

    edge_lo, edge_hi = arguments.get("edge_lo"), arguments.get("edge_hi")
    subtracted = False
    if edge_lo is not None and edge_hi is not None:
        bg = xrs.subtract_compton_background(loss, inten, float(edge_lo), float(edge_hi),
                                             model=arguments.get("model", xrs_policy.DEFAULT_BACKGROUND_MODEL))
        inten = bg["subtracted"]
        subtracted = True

    # element/edge: explicit or auto-suggest from loss window
    element, edge = arguments.get("element"), arguments.get("edge")
    edge_info = None
    if element and edge:
        edge_info = xrs_edges.get_xrs_edge_info(element, edge)
    elif r["axis"] == "energy_loss_ev":
        finite = np.isfinite(loss)
        onset = float(edge_lo) if edge_lo is not None else None
        sug = xrs_edges.suggest_xrs_edge(
            float(loss[finite].min()), float(loss[finite].max()), onset_ev=onset)
        if sug.get("found"):
            edge_info = sug["best"]
            if sug.get("ambiguous"):
                edge_info["ambiguous"] = True
                edge_info["competing_edges"] = sug.get("competing", [])
                edge_info["detection_note"] = sug["note"]

    ew = (float(edge_lo), float(edge_hi)) if subtracted else None
    descriptors, arrays = xd.extract_xrs_descriptors(loss, inten, edge_info=edge_info,
                                                     edge_window=ew)
    if not subtracted:
        descriptors["flags"].append("no_compton_subtraction — onset/pre-edge unreliable on the raw Compton background")
    rec = xrs_data.current_elastic(r["file_name"])
    resolution = rec.get("resolution_fwhm_ev") if rec else None
    meta = {
        "file_name": r["file_name"], "counter": r["counter"], "n_reps": r["n_reps"],
        "elastic_center_ev": r["elastic_center_ev"], "axis": r["axis"],
        "compton_subtracted": subtracted,
    }
    if r.get("counter_warning"):
        meta["counter_warning"] = r["counter_warning"]
    return descriptors, arrays, meta, resolution


def t_extract_xrs_descriptors(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.plots import xrs as xrs_plots
    try:
        descriptors, arrays, meta, _res = _xrs_edge_descriptors(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    fig = xrs_plots.plot_xrs_descriptors(
        arrays["loss"], arrays["intensity"], descriptors,
        title=f"{meta['file_name']} — XRS descriptors")
    img = fig_to_base64(fig); _close(fig)
    return json.dumps({**meta, **descriptors}, indent=2, default=str), [img]


def t_interpret_xrs_oxidation_state(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xrs import interpret as xrs_interpret
    try:
        descriptors, _arrays, meta, _res = _xrs_edge_descriptors(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    verdict = xrs_interpret.interpret_xrs_oxidation_state(descriptors, arguments.get("calibration"))
    verdict.update(meta)
    return json.dumps(verdict, indent=2, default=str), []


def t_assess_xrs_quality(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xrs import interpret as xrs_interpret
    try:
        descriptors, _arrays, meta, res = _xrs_edge_descriptors(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    out = xrs_interpret.assess_xrs_quality(descriptors, res)
    out.update(meta)
    return json.dumps(out, indent=2, default=str), []


def t_summarize_xrs_chemistry(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xrs import interpret as xrs_interpret
    from beamtimehero_cli.science.plots import xrs as xrs_plots
    try:
        descriptors, arrays, meta, res = _xrs_edge_descriptors(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    summary = xrs_interpret.summarize_xrs_chemistry(descriptors, arguments.get("calibration"), res)
    summary.update(meta)
    fig = xrs_plots.plot_xrs_descriptors(
        arrays["loss"], arrays["intensity"], descriptors,
        title=f"{meta['file_name']} — XRS chemistry")
    img = fig_to_base64(fig); _close(fig)
    return json.dumps(summary, indent=2, default=str), [img]


def t_interpret_q_dependence(arguments: dict) -> tuple[str, list[str]]:
    """Classify a feature's q-dependence. Accepts either explicit `points`
    ([{q, value}]) or `groups` ([{q, scan_numbers}]) reduced from one file."""
    from beamtimehero_cli.science.xrs import reduce as xrs
    from beamtimehero_cli.science.xrs import interpret as xrs_interpret
    from beamtimehero_cli.spec_data import xrs_data
    points = arguments.get("points")
    if not points:
        groups = arguments.get("groups") or []
        edge_lo, edge_hi = arguments.get("edge_lo"), arguments.get("edge_hi")
        feat_lo = arguments.get("feature_lo", edge_lo)
        feat_hi = arguments.get("feature_hi", edge_hi)
        if not groups or edge_lo is None or edge_hi is None:
            return json.dumps({"error": "Provide either points=[{q,value}] or groups=[{q,scan_numbers}] with edge_lo/edge_hi."}), []
        points = []
        for g in groups:
            try:
                r = xrs_data.reduce_xrs(file_name=arguments.get("file_name"),
                                        counter=arguments.get("counter"),
                                        scan_numbers=g.get("scan_numbers"),
                                        elastic_center_ev=arguments.get("elastic_center_ev"))
                bg = xrs.subtract_compton_background(r["loss"], r["mean"], float(edge_lo), float(edge_hi),
                                                     model=arguments.get("model", xrs_policy.DEFAULT_BACKGROUND_MODEL))
                norm = xrs.area_normalize(r["loss"], bg["subtracted"], float(edge_lo), float(edge_hi))
                from beamtimehero_cli.science.xrs import descriptors as xd
                value = xd.integrated_area(norm["loss"], norm["normalized"], float(feat_lo), float(feat_hi))
                points.append({"q": g.get("q"), "value": value})
            except ValueError as e:
                points.append({"q": g.get("q"), "value": None, "error": str(e)})
    verdict = xrs_interpret.interpret_q_dependence([p for p in points if p.get("value") is not None])
    verdict["points"] = points
    return json.dumps(verdict, indent=2, default=str), []


def t_compare_xrs_to_references(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.xrs import reduce as xrs
    from beamtimehero_cli.science.xrs import interpret as xrs_interpret
    from beamtimehero_cli.spec_data import xrs_data
    edge_lo, edge_hi = arguments.get("edge_lo"), arguments.get("edge_hi")

    def _reduce_norm(file_name=None, scan_numbers=None, counter=None):
        r = xrs_data.reduce_xrs(file_name=file_name, counter=counter, scan_numbers=scan_numbers,
                                elastic_center_ev=arguments.get("elastic_center_ev"))
        inten = r["mean"]
        if edge_lo is not None and edge_hi is not None:
            inten = xrs.subtract_compton_background(r["loss"], inten, float(edge_lo), float(edge_hi),
                                                    model=arguments.get("model", xrs_policy.DEFAULT_BACKGROUND_MODEL))["subtracted"]
            inten = xrs.area_normalize(r["loss"], inten, float(edge_lo), float(edge_hi))["normalized"]
        return r["loss"], inten

    try:
        loss, inten = _reduce_norm(arguments.get("file_name"), arguments.get("scan_numbers"),
                                   arguments.get("counter"))
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    refs = []
    for ref in arguments.get("references", []):
        try:
            if ref.get("file_name"):
                rl, ri = _reduce_norm(ref["file_name"], ref.get("scan_numbers"), ref.get("counter"))
                refs.append({"name": ref.get("name", ref["file_name"]), "loss": rl, "intensity": ri})
            elif ref.get("loss") and ref.get("intensity"):
                refs.append({"name": ref.get("name", f"ref{len(refs)}"),
                             "loss": ref["loss"], "intensity": ref["intensity"]})
        except ValueError:
            continue
    if not refs:
        return json.dumps({"error": "No usable references (need file_name or loss+intensity arrays)."}), []
    result = xrs_interpret.compare_xrs_to_references(loss, inten, refs)
    result["target_file"] = arguments.get("file_name")
    return json.dumps(result, indent=2, default=str), []


# ---------------------------------------------------------------------------
# CAT-EXAFS handlers — k-space processing (analysis/exafs.py; exafs branch)
# ---------------------------------------------------------------------------

def _jarr(values, decimals=5) -> list[float]:
    """Round an array for JSON tool output (artifact-sized, not plot-sized)."""
    import numpy as np
    return [round(float(v), decimals) for v in np.nan_to_num(np.asarray(values, dtype=float))]


def _extract_chi_core(arguments: dict) -> dict:
    """Shared load → normalize → background pipeline for the exafs tools.

    Returns a dict bundling the loader provenance, E0/edge-step, the
    normalized flattened spectrum, and the chi(k) arrays. Raises ValueError
    on any data problem (one shared error path, like _interpretation_inputs).
    """
    from beamtimehero_cli.science.exafs import background as _exafs_bkg
    from beamtimehero_cli.science.xas import descriptors as interp_desc
    from beamtimehero_cli.science.xas import normalize as interp_norm
    from beamtimehero_cli.spec_data import exafs_data

    r = exafs_data.load_mu(
        file_name=arguments.get("file_name"),
        scan_numbers=arguments.get("scan_numbers"),
        counter=arguments.get("counter"),
        collector_dir=arguments.get("collector_dir"),
    )
    energy, mu = r["energy"], r["mu"]
    e0 = arguments.get("e0")
    if e0 is None:
        e0_info = interp_desc.find_e0(energy, mu)
        e0 = e0_info["e0_ev"]
        e0_source = "derivative_max"
    else:
        e0 = float(e0)
        e0_source = "explicit"

    flat, norm_prov = interp_norm.pre_post_normalize(energy, mu, e0)
    if not norm_prov.get("applied"):
        raise ValueError(f"Normalization failed: {norm_prov.get('reason')}")
    edge_step = norm_prov["edge_step"]
    if edge_step < 0:
        raise ValueError(
            f"Negative edge step ({edge_step:.3g}) — this counter shows no "
            "absorption edge (wrong counter, inverted signal, or no-edge scan). "
            "Pass counter explicitly; see `ref counter-selection`."
        )

    bk = _exafs_bkg.autobk_lite(
        energy, mu, e0, edge_step=edge_step,
        rbkg=float(arguments.get("rbkg", ex_policy.DEFAULT_RBKG)),
        kweight=int(arguments.get("kweight", ex_policy.DEFAULT_KWEIGHT)),
    )
    return {
        **r,
        "e0": float(e0),
        "e0_source": e0_source,
        "edge_step": float(edge_step),
        "flat": flat,
        "norm_provenance": norm_prov,
        "k": bk["k"],
        "chi": bk["chi"],
        "bkg_provenance": bk["provenance"],
    }


def _exafs_meta(core: dict) -> dict:
    """Provenance block shared by every exafs tool's JSON output."""
    out = {
        "source": core["source"],
        "file_name": core["file_name"],
        "counter": core["counter"],
        "scan_numbers": core["scan_numbers"],
        "n_reps": core["n_reps"],
        "dropped_short_reps": core["dropped_short_reps"],
        "n_glitch_points": core["n_glitch_points"],
        "e0_ev": core["e0"],
        "e0_source": core["e0_source"],
        "edge_step": core["edge_step"],
        "kmax_inv_ang": float(core["k"].max()),
        "normalization": core["norm_provenance"],
        "background": core["bkg_provenance"],
    }
    if core.get("counter_warning"):
        out["counter_warning"] = core["counter_warning"]
    return out


def t_list_collector_scans(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.spec_data.ssrl_backend import SSRLAsciiBackend
    try:
        backend = SSRLAsciiBackend(arguments.get("collector_dir"))
        groups = backend.list_groups()
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    return json.dumps({
        "data_dir": str(backend.data_dir),
        "n_groups": len(groups),
        "groups": groups,
        "format": "SSRL EXAFS Data Collector 4.0 ASCII (one of many formats "
                  "across SSRL's stations; only this one is read here)",
        "note": (
            "Pass a group's file_name (and optionally sweep numbers as "
            "scan_numbers) to the exafs tools; the signal counter defaults "
            "to SCA_sum (summed Xspress3 fluorescence / I0)."
        ),
    }, indent=2, default=str), []


def t_extract_chi(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.plots import exafs as exafs_plots
    try:
        core = _extract_chi_core(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    kweight = int(arguments.get("kweight", ex_policy.DEFAULT_KWEIGHT))
    fig = exafs_plots.plot_chi_extraction(
        core["energy"], core["mu"], core["flat"], core["e0"],
        core["k"], core["chi"], kweight,
        f"{core['file_name']} — chi extraction ({core['n_reps']} reps, {core['counter']})")
    images = [fig_to_base64(fig)]
    _close(fig)
    out = _exafs_meta(core)
    out["chi_artifact"] = {"k": _jarr(core["k"]), "chi": _jarr(core["chi"])}
    out["note"] = (
        "Pass chi_artifact as the `chi` argument of fourier_transform_chi / "
        "exafs_products to skip re-extraction."
    )
    return json.dumps(out, indent=2, default=str), images


def _resolve_chi(arguments: dict) -> tuple[dict | None, dict]:
    """Chi arrays for the FT tools: precomputed artifact or full pipeline.

    Returns ``(core_or_None, {'k': ..., 'chi': ...})`` — core is None on the
    artifact path (mirrors _resolve_descriptors: artifact wins, recompute
    otherwise).
    """
    import numpy as np
    artifact = arguments.get("chi")
    if isinstance(artifact, dict) and artifact.get("k") and artifact.get("chi") is not None:
        return None, {
            "k": np.asarray(artifact["k"], dtype=float),
            "chi": np.asarray(artifact["chi"], dtype=float),
        }
    core = _extract_chi_core(arguments)
    return core, {"k": core["k"], "chi": core["chi"]}


def t_fourier_transform_chi(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.exafs import fourier as exafs
    from beamtimehero_cli.science.plots import exafs as exafs_plots
    try:
        core, chi = _resolve_chi(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    kweight = int(arguments.get("kweight", ex_policy.DEFAULT_KWEIGHT))
    kmin = float(arguments.get("kmin", ex_policy.DEFAULT_KMIN))
    kmax = arguments.get("kmax")
    ft = exafs.xftf(
        chi["k"], chi["chi"], kmin=kmin,
        kmax=float(kmax) if kmax is not None else None,
        kweight=kweight, dk=float(arguments.get("dk", ex_policy.DEFAULT_DK)),
    )
    peak = exafs.first_shell_peak(ft["r"], ft["chir_mag"])
    label = core["file_name"] if core else "chi artifact"
    fig = exafs_plots.plot_chir(
        ft["r"], ft["chir_mag"], ft["provenance"]["kmin_inv_ang"],
        ft["provenance"]["kmax_inv_ang"], kweight,
        f"{label} — |chi(R)| (phase-uncorrected)", peak=peak)
    images = [fig_to_base64(fig)]
    _close(fig)
    out = _exafs_meta(core) if core else {"source": "chi_artifact"}
    out.update({
        "ft": ft["provenance"],
        "first_shell": peak,
        "r": _jarr(ft["r"]),
        "chir_mag": _jarr(ft["chir_mag"]),
    })
    return json.dumps(out, indent=2, default=str), images


def t_exafs_products(arguments: dict) -> tuple[str, list[str]]:
    """Capstone: extraction plot + FT plot + the full JSON bundle."""
    from beamtimehero_cli.science.exafs import fourier as exafs
    from beamtimehero_cli.science.plots import exafs as exafs_plots
    try:
        core, chi = _resolve_chi(arguments)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2), []
    kweight = int(arguments.get("kweight", ex_policy.DEFAULT_KWEIGHT))
    images: list[str] = []
    if core is not None:
        fig = exafs_plots.plot_chi_extraction(
            core["energy"], core["mu"], core["flat"], core["e0"],
            core["k"], core["chi"], kweight,
            f"{core['file_name']} — chi extraction ({core['n_reps']} reps, {core['counter']})")
        images.append(fig_to_base64(fig))
        _close(fig)
    kmax = arguments.get("kmax")
    ft = exafs.xftf(
        chi["k"], chi["chi"], kmin=float(arguments.get("kmin", ex_policy.DEFAULT_KMIN)),
        kmax=float(kmax) if kmax is not None else None,
        kweight=kweight, dk=float(arguments.get("dk", ex_policy.DEFAULT_DK)),
    )
    peak = exafs.first_shell_peak(ft["r"], ft["chir_mag"])
    label = core["file_name"] if core else "chi artifact"
    fig = exafs_plots.plot_chir(
        ft["r"], ft["chir_mag"], ft["provenance"]["kmin_inv_ang"],
        ft["provenance"]["kmax_inv_ang"], kweight,
        f"{label} — |chi(R)| (phase-uncorrected)", peak=peak)
    images.append(fig_to_base64(fig))
    _close(fig)
    out = _exafs_meta(core) if core else {"source": "chi_artifact"}
    out.update({
        "chi_artifact": {"k": _jarr(chi["k"]), "chi": _jarr(chi["chi"])},
        "ft": ft["provenance"],
        "first_shell": peak,
        "r": _jarr(ft["r"]),
        "chir_mag": _jarr(ft["chir_mag"]),
    })
    return json.dumps(out, indent=2, default=str), images


def t_overlay_chi_spectra(arguments: dict) -> tuple[str, list[str]]:
    from beamtimehero_cli.science.plots import exafs as exafs_plots
    file_names = arguments.get("file_names", [])
    if not file_names:
        return json.dumps({"error": "file_names array must not be empty."}), []
    kweight = int(arguments.get("kweight", ex_policy.DEFAULT_KWEIGHT))
    spectra, reports = [], []
    for fn in file_names:
        try:
            core = _extract_chi_core({
                "file_name": fn,
                "counter": arguments.get("counter"),
                "collector_dir": arguments.get("collector_dir"),
                "rbkg": arguments.get("rbkg", ex_policy.DEFAULT_RBKG),
                "kweight": kweight,
            })
            spectra.append((f"{fn} ({core['n_reps']} reps)", core["k"], core["chi"]))
            reports.append({
                "file_name": fn, "n_reps": core["n_reps"], "e0_ev": core["e0"],
                "edge_step": core["edge_step"],
                "kmax_inv_ang": float(core["k"].max()),
                "counter_warning": core.get("counter_warning"),
            })
        except ValueError as e:
            reports.append({"file_name": fn, "error": str(e)})
    if not spectra:
        return json.dumps({"error": "No chi spectra could be extracted.",
                           "reports": reports}, indent=2), []
    fig = exafs_plots.plot_chi_overlay(
        spectra, kweight, f"chi(k)·k^{kweight} overlay ({len(spectra)} groups)")
    images = [fig_to_base64(fig)]
    _close(fig)
    return json.dumps({"n_overlaid": len(spectra), "reports": reports},
                      indent=2, default=str), images


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

# Flat name → handler map for the "default" implementation of each tool
# name. The (tree, name) keyed DISPATCH below augments this with branch-
# specific overrides (e.g. ("s3df", "list_scans") points at a different
# handler than ("spec-file", "list_scans") even though the leaf name and
# JSON schema are identical).
_HANDLERS: dict[str, callable] = {
    # CAT-0
    "align_beamline": t_align_beamline,
    "align_xes_spectrometer": t_align_xes,
    "run_sample_alignment": t_auto_sample_align,
    "run_collection": t_run_collection,
    "select_element": t_select_element,
    "peak_mono_pitch": t_peak_mono_pitch,
    "calibrate_mono": t_calibrate_mono,
    # CAT-1
    "move_motor": t_move_motor,
    "move_motor_relative": t_move_motor_relative,
    "read_motor_position": t_read_motor_position,
    "read_all_positions": t_wa,
    # CAT-2
    "run_motor_scan": t_run_motor_scan,
    "run_motor_scan_relative": t_run_motor_scan_relative,
    "run_diagonal_scan": t_run_diagonal_scan,
    "run_xas": t_run_xas,
    "run_emiss_scan": t_run_emiss_scan,
    "fit_emission_peak": t_fit_emission_peak,
    # CAT-3
    "mv_energy": t_mv_energy,
    "shutter": t_shutter,
    "set_filter": t_set_filter,
    "safely_remove_filters": t_safely_remove_filters,
    "set_gain": t_set_gain,
    "set_vortex_roi": t_set_vortex_roi,
    "open_data_file": t_open_data_file,
    "plotselect": t_plotselect,
    # CAT-4
    "run_align_shortcut": t_run_align_shortcut,
    "post_scan_move": t_post_scan_move,
    # CAT-5 (beam diagnostic)
    "mv_pinhole": t_mv_pinhole,
    "mv_plastic": t_mv_plastic,
    "mv_knife_clear": t_mv_knife_clear,
    "mv_knife_out": t_mv_knife_out,
    "measure_beam_size": t_measure_beam_size,
    "zero_pinhole": t_zero_pinhole,
    "small_beam": t_small_beam,
    "big_beam": t_big_beam,
    "xtal_align": t_xtal_align,
    "reset_gap": t_reset_gap,
    "set_m2_stripe": t_set_m2_stripe,
    "get_anchor": t_get_anchor,
    "set_anchor": t_set_anchor,
    "tracking": t_tracking,
    # CAT-6
    "get_beam_size": t_get_beam_size,
    "get_beam_status": t_get_beam_status,
    "get_counts": t_get_counts,
    "get_counter": t_get_counter,
    "request_gap_ownership": t_request_gap_ownership,
    "capture_sample_image": t_capture_sample_image,
    "get_reference_image": t_get_reference_image,
    # CAT-7
    "get_element": t_get_element,
    "get_scan_number": t_get_scan_number,
    "get_current_datafile": t_get_current_datafile,
    "get_plotselected_counter": t_get_plotselected_counter,
    "abort_current_scan": t_abort_current_scan,
    "recent_actions": t_recent_actions,
    # Data / analysis / plotting tools (formerly executor.py if/elif)
    "get_latest_scan": t_get_latest_scan,
    "list_scans": t_list_scans,
    "read_scan": t_read_scan,
    "get_latest_log_entries": t_get_latest_log_entries,
    "search_logs": t_search_logs,
    "list_logs": t_list_logs,
    "get_active_counter": t_get_active_counter,
    "get_scan_deadtime": t_get_scan_deadtime,
    "normalize_scan": t_normalize_scan,
    "average_scans": t_average_scans,
    "analyze_convergence": t_analyze_convergence,
    "analyze_efficiency": t_analyze_efficiency,
    "analyze_feature_evolution": t_analyze_feature_evolution,
    "group_scans_by_spot": t_group_scans_by_spot,
    "analyze_per_spot": t_analyze_per_spot,
    "plot_averaged_scans": t_plot_averaged_scans,
    "plot_scan": t_plot_scan,
    "plot_scan_stack": t_plot_scan_stack,
    "plot_first_half_vs_second_half": t_plot_first_half_vs_second_half,
    "plot_running_average": t_plot_running_average,
    "plot_feature_evolution": t_plot_feature_evolution,
    "plot_data": t_plot_data,
    "list_files": t_list_files,
    "read_file": t_read_file,
    "write_summary": t_write_summary,
    "write_macro": t_write_macro,
    "save_plan": t_save_plan,
    "get_motor_config": t_get_motor_config,
    "get_counter_config": t_get_counter_config,
    "evaluate_spec_macro": t_evaluate_spec_macro,
    # CAT-10 · Scientific interpretation
    "record_energy_calibration": t_record_energy_calibration,
    "get_energy_calibration": t_get_energy_calibration,
    "extract_xas_descriptors": t_extract_xas_descriptors,
    "interpret_oxidation_state": t_interpret_oxidation_state,
    "interpret_coordination_geometry": t_interpret_coordination_geometry,
    "summarize_sample_chemistry": t_summarize_sample_chemistry,
    # CAT-10 · Atomic descriptor tools (each wraps one pure function)
    "identify_edge": t_identify_edge,
    "find_edge_e0": t_find_edge_e0,
    "normalize_xas_intensity": t_normalize_xas_intensity,
    "fit_xas_pre_edge": t_fit_xas_pre_edge,
    "fit_xas_white_line": t_fit_xas_white_line,
    "assess_xas_quality": t_assess_xas_quality,
    "detect_per_scan_drift": t_detect_per_scan_drift,
    # CAT-10 · Cross-file XAS comparison (merged two-col files accepted)
    "compare_xas_to_references": t_compare_xas_to_references,
    "align_spectra": t_align_spectra,
    "difference_spectrum": t_difference_spectrum,
    # CAT-XRS · X-ray Raman processing (energy-loss axis, NOT edge-step)
    "calibrate_energy_loss": t_calibrate_energy_loss,
    "build_loss_axis": t_build_loss_axis,
    "average_xrs_scans": t_average_xrs_scans,
    "subtract_compton_background": t_subtract_compton_background,
    "normalize_xrs": t_normalize_xrs,
    "overlay_xrs_spectra": t_overlay_xrs_spectra,
    "sum_crystals": t_sum_crystals,
    "align_crystals": t_align_crystals,
    "tag_crystal_q": t_tag_crystal_q,
    # CAT-XRS · X-ray Raman interpretation
    "extract_xrs_descriptors": t_extract_xrs_descriptors,
    "interpret_xrs_oxidation_state": t_interpret_xrs_oxidation_state,
    "interpret_q_dependence": t_interpret_q_dependence,
    "compare_xrs_to_references": t_compare_xrs_to_references,
    # CAT-EXAFS
    "list_collector_scans": t_list_collector_scans,
    "extract_chi": t_extract_chi,
    "fourier_transform_chi": t_fourier_transform_chi,
    "exafs_products": t_exafs_products,
    "overlay_chi_spectra": t_overlay_chi_spectra,
    "assess_xrs_quality": t_assess_xrs_quality,
    "summarize_xrs_chemistry": t_summarize_xrs_chemistry,
    # Slack tools (require the [slack] extra).
    "post_slack_message": lambda args: _t_slack(
        "post_message", args, kw=("channel_id", "text", "thread_ts"),
    ),
    "read_channel_messages": lambda args: _t_slack(
        "read_channel_messages", args, kw=("channel_id", "limit", "oldest"),
    ),
    "read_thread_replies": lambda args: _t_slack(
        "read_thread_replies", args, kw=("channel_id", "thread_ts"),
    ),
    "list_channels": lambda args: _t_slack("list_channels", args, kw=()),
}


# ---------------------------------------------------------------------------
# s3df (postgres-backed) handlers
# ---------------------------------------------------------------------------

_PG_BACKEND = None


def _pg_backend():
    """Lazily build a process-wide PostgresBackend. Connections themselves
    are still per-call inside the backend; this just avoids re-importing
    psycopg2 on every tool invocation."""
    global _PG_BACKEND
    if _PG_BACKEND is None:
        from beamtimehero_cli.spec_data.postgres_backend import PostgresBackend
        _PG_BACKEND = PostgresBackend()
    return _PG_BACKEND


def _s3df(call) -> tuple[str, list[str]]:
    """Wrap a backend call so missing driver / DB outage produces a JSON
    error payload instead of crashing the tool loop."""
    try:
        result = call(_pg_backend())
        if result is None:
            return "Not found.", []
        return json.dumps(result, indent=2, default=str), []
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}), []
    except Exception as e:  # noqa: BLE001
        logger.warning("s3df tool failed", exc_info=True)
        return json.dumps({"ok": False, "error": str(e)}), []


def t_s3df_list_scans(args):
    return _s3df(lambda b: b.list_scans(limit=args.get("limit", 20)))


def t_s3df_get_latest_scan(args):
    return _s3df(lambda b: b.get_latest_scan())


def t_s3df_read_scan(args):
    def _go(b):
        meta = b.get_scan_metadata(args.get("file_name", ""), args.get("scan_number", 1))
        if not meta:
            return None
        df = b.read_scan(args["file_name"], args["scan_number"])
        if df is not None:
            meta["data"] = scan_data.df_to_llm_text(df)
        return meta
    return _s3df(_go)


def t_s3df_get_active_counter(args):
    return _s3df(lambda b: b.get_active_counter(
        args.get("file_name", ""), args.get("scan_number", 1),
    ))


def t_s3df_get_scan_deadtime(args):
    return _s3df(lambda b: b.get_scan_deadtime(
        args.get("file_name", ""), args.get("scan_number", 1),
    ))


def t_s3df_plot_scan(args):
    """Plot a scan from the postgres-backed pickle store."""
    try:
        backend = _pg_backend()
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}), []

    file_name = args.get("file_name", "")
    scan_number = args.get("scan_number", 1)
    counter = args.get("counter")
    normalize_by = args.get("normalize_by")

    try:
        df = backend.read_scan(file_name, scan_number)
        if df is None:
            return f"Scan not found: {file_name} #{scan_number}", []
        if not counter:
            active = backend.get_active_counter(file_name, scan_number)
            if active:
                counter = active["active_counter"]
        meta = backend.get_scan_metadata(file_name, scan_number) or {}

        from beamtimehero_cli.science.plots.scan import render_scan
        fig, summary = render_scan(
            df, file_name, scan_number,
            counter=counter, normalize_by=normalize_by,
            scan_command=meta.get("scan_command"),
        )
        if fig is None:
            return summary, []
        b64 = fig_to_base64(fig)
        import matplotlib.pyplot as plt
        plt.close(fig)
        return summary, [b64]
    except Exception as e:  # noqa: BLE001
        logger.warning("s3df plot_scan failed", exc_info=True)
        return json.dumps({"ok": False, "error": str(e)}), []


# ---------------------------------------------------------------------------
# s3df psql (raw SQL)
# ---------------------------------------------------------------------------

def t_s3df_psql_execute_readonly_sql(args):
    return _s3df(lambda b: b.execute_readonly_sql(
        args.get("query", ""),
        max_rows=args.get("max_rows", 100),
    ))


# ---------------------------------------------------------------------------
# Slack adapter (lives below; both branches' handlers added below in _HANDLERS)
# ---------------------------------------------------------------------------

def _t_slack(fn_name: str, args: dict, *, kw: tuple[str, ...]) -> tuple[str, list[str]]:
    """Common adapter for slack tools — dispatch to ``notify.slack`` and
    wrap the dict result as JSON. Missing token / missing slack-sdk
    degrade to a JSON error payload rather than crashing the loop.
    """
    from beamtimehero_cli.notify import slack as _slack
    try:
        fn = getattr(_slack, fn_name)
        payload = {k: args[k] for k in kw if k in args and args[k] is not None}
        return json.dumps(fn(**payload), indent=2, default=str), []
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}), []
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"slack {fn_name} failed: {e}"}), []


# Branch-specific overrides: when a tool name has different implementations
# per tree, register them here. The (tree, name) key wins over the flat
# _HANDLERS entry for that name. Same JSON schema, different backend.
_BRANCH_HANDLERS: dict[tuple[str, ...], callable] = {
    # s3df: postgres metadata + pickle DataFrames
    ("s3df", "list_scans"): t_s3df_list_scans,
    ("s3df", "get_latest_scan"): t_s3df_get_latest_scan,
    ("s3df", "read_scan"): t_s3df_read_scan,
    ("s3df", "get_active_counter"): t_s3df_get_active_counter,
    ("s3df", "get_scan_deadtime"): t_s3df_get_scan_deadtime,
    ("s3df", "plot_scan"): t_s3df_plot_scan,
    # s3df psql: raw queries
    ("s3df", "psql", "execute_readonly_sql"): t_s3df_psql_execute_readonly_sql,
}


def _build_dispatch(
    definitions: "list[dict] | None" = None,
) -> dict[tuple[str, ...], "callable"]:
    """Build the ``(tree, ..., name) -> handler`` dispatch table.

    For each definition: categorize() decides the tree, then we pick the
    handler from ``_BRANCH_HANDLERS[(tree, name)]`` if present, else fall
    back to ``_HANDLERS[name]``. Tools without any handler are skipped —
    that happens for plan-aware tools described in the catalog but
    dispatched through the autonomous repo's own executor.

    ``definitions`` defaults to the library's own definitions plus
    anything a consumer passed to ``tool_catalog.register_definitions``.
    Iterating the library list alone was the bug: a consumer could
    register a handler and a definition and still get no dispatch key,
    because the loop never saw its definition.
    """
    from beamtimehero_cli.tool_catalog.categorize import categorize
    from beamtimehero_cli.tool_catalog.definitions import AUTONOMY_TOOL_DEFINITIONS

    if definitions is None:
        from beamtimehero_cli.tool_catalog import _REGISTERED_DEFINITIONS
        definitions = list(AUTONOMY_TOOL_DEFINITIONS) + list(_REGISTERED_DEFINITIONS)

    out: dict[tuple[str, ...], "callable"] = {}
    for tdef in definitions:
        name = tdef.get("function", {}).get("name")
        if not name:
            continue
        tree = categorize(tdef)
        key = tree + (name,)
        handler = _BRANCH_HANDLERS.get(key) or _HANDLERS.get(name)
        if handler is None:
            continue
        out[key] = handler
    return out


DISPATCH: dict[tuple[str, ...], "callable"] = _build_dispatch()


def register_handlers(mapping: "dict") -> None:
    """Register out-of-tree tool handlers, then rebuild ``DISPATCH``.

    Key shape decides which table the handler lands in, matching the two
    that already exist:

    * ``str`` — a bare tool name, into ``_HANDLERS``. Applies on whatever
      branch ``categorize()`` puts the definition on.
    * ``tuple`` — a full ``(tree, ..., name)`` path, into
      ``_BRANCH_HANDLERS``. Use this when the same leaf name needs a
      different backend per branch.

    Override precedence therefore falls out of ``_build_dispatch`` rather
    than being a second set of rules: ``_BRANCH_HANDLERS`` wins for its
    exact path, ``_HANDLERS`` covers every other path for that name. So a
    name-keyed ``list_scans`` replaces ``("spec-file", "list_scans")`` and
    leaves ``("s3df", "list_scans")`` alone, because s3df's is branch-keyed.

    ``DISPATCH`` is cleared and refilled rather than rebound: the module
    attribute is read by ``executor.py`` and captured by consumers, and
    rebinding it is what left stale tables behind.
    """
    if not mapping:
        return
    # Validate the whole mapping first: a half-applied registration leaves
    # DISPATCH holding some of a consumer's handlers and not others, which
    # is harder to diagnose than a refusal.
    problems: list[str] = []
    for key, handler in mapping.items():
        if not isinstance(key, (str, tuple)):
            problems.append(
                f"key {key!r}: must be a tool name (str) or a "
                "(tree, ..., name) tuple"
            )
        if not callable(handler):
            problems.append(f"handler for {key!r} is not callable")
    if problems:
        raise ValueError(
            "cannot register handlers ({}):\n  {}".format(
                len(problems), "\n  ".join(problems)
            )
        )

    for key, handler in mapping.items():
        if isinstance(key, str):
            _HANDLERS[key] = handler
        else:
            _BRANCH_HANDLERS[tuple(key)] = handler

    DISPATCH.clear()
    DISPATCH.update(_build_dispatch())
