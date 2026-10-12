"""Scan data operations -- the file-backed chokepoint every generic tool uses.

Two on-disk formats hide behind the same function surface:

- SPEC files (default): reads via the ``local_data`` module (silx), with its
  JSON metadata cache. ``file_name`` = SPEC file, ``scan_number`` = scan.
- SSRL EXAFS Data Collector ASCII (BL 4-3 style stations): when
  ``SSRL_COLLECTOR_DIR`` is set — chemcatal-bth sets it for collector-format
  beamtimes — every function here routes to ``SSRLAsciiBackend`` instead.
  ``file_name`` = scan-group key ('<sample>_<NNN>'), ``scan_number`` = sweep.
  A collector directory never contains SPEC files, so the switch is
  session-wide, not per-file.

Pure-math helpers (edge-step normalization, active-counter selection,
per-rep noise estimation, averaging) live in ``beamtimehero_cli.science.reduce``
so the postgres-backed flow can reuse them without copy-pasting.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from beamtimehero_cli import config as bl_config
from beamtimehero_cli.science.reduce import counters as _counters
from beamtimehero_cli.science.reduce import normalize as _norm
from beamtimehero_cli.science.reduce import reps as _reps
from beamtimehero_cli.spec_data import local_data, twocol_ascii
from beamtimehero_cli.spec_data.ssrl_backend import (
    SSRL_COLLECTOR_DIR_ENV, SSRLAsciiBackend,
)


def _collector():
    """SSRLAsciiBackend when the session points at a collector dir, else None.

    Resolved per call (cheap: the backend re-scans its small directory per
    operation anyway) so a wrapper that sets/clears SSRL_COLLECTOR_DIR per
    beamtime selection is always honored."""
    if os.getenv(SSRL_COLLECTOR_DIR_ENV):
        return SSRLAsciiBackend()
    return None


def _scan_numbers_for_file(file_name):
    b = _collector()
    if b is not None:
        return b.get_scan_numbers_for_file(file_name)
    return local_data.get_scan_numbers_for_file(file_name)


def list_processed_scans(limit=20):
    """List scans, most recent first."""
    b = _collector()
    if b is not None:
        return b.list_scans(limit=limit)
    return local_data.list_processed_scans(limit=limit)


def get_scan_metadata(file_name, scan_number):
    """Get full metadata for a single scan."""
    b = _collector()
    if b is not None:
        return b.get_scan_metadata(file_name, scan_number)
    return local_data.get_scan_metadata(file_name, scan_number)


def read_processed_scan(file_name, scan_number):
    """Read scan data from the data file. Returns DataFrame or None."""
    b = _collector()
    if b is not None:
        return b.read_scan(file_name, scan_number)
    return local_data.read_processed_scan(file_name, scan_number)


def read_processed_scan_ex(file_name, scan_number):
    """Read scan data returning ``(df, reason)`` — reason distinguishes
    ``not_found`` / ``empty_scan`` / ``parse_error: ...`` when df is None.
    """
    b = _collector()
    if b is not None:
        df = b.read_scan(file_name, scan_number)
        return (df, None) if df is not None else (None, "not_found")
    return local_data.read_processed_scan_ex(file_name, scan_number)


def get_scan_deadtime(file_name, scan_number):
    """Get dead time info for a single scan."""
    b = _collector()
    if b is not None:
        return b.get_scan_deadtime(file_name, scan_number)
    return local_data.get_scan_deadtime(file_name, scan_number)


def get_active_counter(file_name, scan_number):
    """Determine the 'active' fluorescence/absorption counter for a scan.

    Selection logic lives in ``science.reduce.counters.pick_active_counter`` and is
    shared with the postgres backend.
    """
    df = read_processed_scan(file_name, scan_number)
    if df is None:
        return None
    counter, reason = _counters.pick_active_counter(df, priority=bl_config.active_counter_priority())
    return {
        "file_name": file_name,
        "scan_number": scan_number,
        "active_counter": counter,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# LLM-facing DataFrame rendering
# ---------------------------------------------------------------------------

_LLM_MAX_ROWS = 200
_LLM_EDGE_ROWS = 100


def df_to_llm_text(df, max_rows: int = _LLM_MAX_ROWS) -> str:
    """Render a DataFrame for inclusion in LLM-facing tool output.

    Small frames render byte-identically to ``df.to_string()``. Frames
    larger than *max_rows* are elided to head+tail with an explicit
    marker so a 1000-point scan doesn't become a multi-thousand-token
    blob per tool call.
    """
    if len(df) <= max_rows:
        return df.to_string()
    head = df.head(_LLM_EDGE_ROWS).to_string()
    tail = df.tail(_LLM_EDGE_ROWS).to_string()
    elided = len(df) - 2 * _LLM_EDGE_ROWS
    return (
        f"{head}\n... [{elided} of {len(df)} rows elided — "
        f"use plot/analysis tools for the full series] ...\n{tail}"
    )


# Backward-compat shim: re-export the pure math from the science layer
# so callers that still import ``scans._edge_step_normalize`` keep working.
_edge_step_normalize = _norm.edge_step_normalize


def edge_step_normalize_scan(file_name, scan_number, counter=None, normalize_by="I0"):
    """Load a scan, normalize by I0, then apply edge-step normalization."""
    df = read_processed_scan(file_name, scan_number)
    if df is None:
        return None

    if counter is None:
        active = get_active_counter(file_name, scan_number)
        if active is None:
            return None
        counter = active["active_counter"]

    try:
        energy, normalized = _edge_step_normalize(df, counter, normalize_by)
    except KeyError as e:
        return {"error": str(e)}

    result_df = pd.DataFrame({"energy": energy, "normalized": normalized})
    result_df = result_df.set_index("energy")

    return {
        "file_name": file_name,
        "scan_number": scan_number,
        "counter": counter,
        "normalize_by": normalize_by,
        "num_points": len(result_df),
        "data": df_to_llm_text(result_df),
    }


def get_most_recent_file():
    """Find the most recently modified data file (excluding alignment)."""
    b = _collector()
    if b is not None:
        return b.get_most_recent_file()
    return local_data.get_most_recent_file()


# Energy-grid alignment tolerance: 4 decimals of eV (0.1 meV) — far below
# any mono step size, far above float jitter between nominally-equal grids.
_ENERGY_ALIGN_DECIMALS = 4


def _concat_aligned(series_list, decimals=_ENERGY_ALIGN_DECIMALS):
    """Concat per-scan Series on a tolerance-aligned energy index.

    Exact-float index alignment in ``pd.concat`` silently fans out rows
    (and later ``dropna()`` discards them) when energy grids carry float
    jitter between reps. When the grids are already exactly identical
    the series are concatenated as-is — behavior (and index values) are
    unchanged. Otherwise each index is rounded to ``decimals`` decimals
    of eV before concat; duplicate rounded energies within one scan are
    averaged.
    """
    if len(series_list) > 1:
        first = series_list[0].index
        identical = all(
            len(s.index) == len(first) and np.array_equal(s.index.values, first.values)
            for s in series_list[1:]
        )
        if not identical:
            aligned = []
            for s in series_list:
                s2 = s.copy()
                s2.index = np.round(s2.index.values.astype(float), decimals)
                if s2.index.has_duplicates:
                    s2 = s2.groupby(level=0).mean()
                    s2.name = s.name
                aligned.append(s2)
            series_list = aligned
    return pd.concat(series_list, axis=1)


# ---------------------------------------------------------------------------
# Merged two-column ASCII support (already-processed spectra, e.g. MERGE/*.dat)
# ---------------------------------------------------------------------------

def _merged_twocol_path(file_name) -> Path | None:
    """Resolve *file_name* to a merged two-column ASCII under the data roots.

    Roots are the same the scan backends read from: the collector directory
    when ``SSRL_COLLECTOR_DIR`` is set, plus ``BL_SCAN_DIR``; then the CHERFD
    export directory (``CHERFD_EXPORT_DIR``), where ``cherfd_export_merged``
    writes the file whose name it tells the caller to pass here. Tries the name
    as a root-relative path first (``MERGE/foo.dat``), then falls back to a
    recursive basename search — ``local_data.list_files`` reports these
    files by relative path, but users often quote just the basename.
    """

    roots = []
    collector_dir = os.getenv(SSRL_COLLECTOR_DIR_ENV)
    if collector_dir:
        roots.append(Path(collector_dir))
    if bl_config.BL_SCAN_DIR:
        roots.append(Path(bl_config.BL_SCAN_DIR))
    from beamtimehero_cli.cherfd_control import settings as cherfd_settings
    roots.append(cherfd_settings.export_dir())

    name = str(file_name)
    for root in roots:
        if not root.is_dir():
            continue
        candidate = root / name
        try:
            candidate.resolve().relative_to(root.resolve())
            inside_root = True
        except (ValueError, OSError):
            inside_root = False  # path traversal — never resolve outside the root
        if inside_root and candidate.is_file() and twocol_ascii.is_twocol_ascii(candidate):
            return candidate
        for match in sorted(root.rglob(Path(name).name)):
            if match.is_file() and twocol_ascii.is_twocol_ascii(match):
                return match
    return None


def _apply_energy_window(combined: pd.DataFrame, e_min, e_max) -> pd.DataFrame:
    """Shared [e_min, e_max] windowing for normalized-array DataFrames."""
    if e_min is None or e_max is None:
        return combined
    if e_min >= e_max:
        raise ValueError(f"e_min ({e_min}) must be less than e_max ({e_max}).")
    windowed = combined.loc[(combined.index >= e_min) & (combined.index <= e_max)]
    if len(windowed) < 5:
        raise ValueError(
            f"Energy window [{e_min}, {e_max}] yielded only {len(windowed)} points. "
            f"Available range: [{combined.index.min():.2f}, {combined.index.max():.2f}]."
        )
    return windowed


def _merged_scan_arrays(file_name, e_min=None, e_max=None):
    """:func:`get_normalized_scan_arrays` result for a merged two-column ASCII.

    Returns ``(combined, file_name, "merged", [1])`` with a single ``merged``
    column served as ``normalization="raw"`` — the file already holds a
    processed (typically normalized) spectrum, so no counter selection and no
    re-normalization apply. Returns None when *file_name* is not such a file.
    """
    path = _merged_twocol_path(file_name)
    if path is None:
        return None
    energy, intensity, meta = twocol_ascii.read_twocol(path)
    combined = pd.DataFrame(
        {"merged": intensity}, index=pd.Index(energy, name="energy"),
    )
    combined.attrs["counter"] = "merged"
    combined.attrs["counter_reason"] = (
        "merged two-column ASCII (already processed; served as-is)"
    )
    combined.attrs["counter_explicit"] = False
    combined.attrs["counter_warning"] = None
    combined.attrs["normalization"] = "raw"
    combined.attrs["source_path"] = meta["path"]
    combined.attrs["source_format"] = "twocol_ascii"
    combined = _apply_energy_window(combined, e_min, e_max)
    return combined, file_name, "merged", [1]


def get_normalized_scan_arrays(file_name=None, e_min=None, e_max=None, scan_numbers=None,
                               counter=None, normalization="edge_step"):
    """Load all scans for a file, normalize, and return as a DataFrame on a common energy grid.

    .. note::

       This is the single chokepoint for every multi-scan tool (``average_scans``,
       ``analyze_convergence``, ``analyze_efficiency``, ``plot_scan_stack``, …).
       It hardcodes ``get_active_counter`` + edge-step normalization, so none of
       those tools can currently express "use this counter" or "don't
       edge-step-normalize". That is the fix site: thread an explicit ``counter``
       and a normalization mode through here and all of them gain override at
       once. Until then, off-XAS techniques (XRS) cannot be processed correctly.
       See ``beamtimehero ref counter-selection``.

    .. note::

       Already-merged two-column ASCII spectra (e.g. ``MERGE/*.dat`` written
       by post-processing pipelines) are also served here: when *file_name*
       has no scans in any backend but resolves to such a file under the data
       roots, the result is a single ``merged`` column with
       ``normalization="raw"``, ``counter="merged"``, ``used_scans=[1]`` —
       so every multi-scan tool accepts merged files with no extra flags.

    Parameters
    ----------
    file_name : str, optional
        SPEC file name. If None, uses the most recent file.
    e_min, e_max : float, optional
        If both are given, restrict the returned DataFrame to rows whose energy
        index is in [e_min, e_max]. Edge-step normalization is still performed
        on the full scan (so pre/post anchors are unchanged) — only the
        returned slice is windowed.
    scan_numbers : list[int], optional
        If given, restrict to these specific scan numbers (used by per-spot
        analysis to run the same pipeline on a subset).
    counter : str, optional
        Signal counter to analyze. If None, auto-selected via
        ``pick_active_counter`` (highest-max heuristic) and a flat-channel
        guardrail warning is attached to ``combined.attrs["counter_warning"]``.
        **Pass this explicitly for XRS / any non-edge technique** — the
        auto-selector picks the brightest channel, which can be a dark
        background channel. See ``beamtimehero ref counter-selection``.
    normalization : {"edge_step", "divide_by_i0", "raw"}, default "edge_step"
        Per-rep normalization before combining. ``edge_step`` assumes an
        absorption edge and is wrong for XRS; use ``divide_by_i0`` or ``raw``
        for non-edge data. See ``ref counter-selection``.
    """
    if file_name is None:
        file_name = get_most_recent_file()
        if file_name is None:
            raise ValueError("No data files found.")

    known_scans = _scan_numbers_for_file(file_name)
    if not known_scans:
        # Not a SPEC/collector scan file — maybe an already-merged two-column
        # ASCII (e.g. MERGE/*.dat written by post-processing pipelines).
        merged = _merged_scan_arrays(file_name, e_min=e_min, e_max=e_max)
        if merged is not None:
            return merged

    if scan_numbers is None:
        scan_numbers = known_scans

    if not scan_numbers:
        raise ValueError(f"No scans found for file '{file_name}'.")

    counter_warning = None
    counter_explicit = counter is not None
    if counter is None:
        active = get_active_counter(file_name, scan_numbers[0])
        if active is None:
            raise ValueError(f"Could not load scan data for '{file_name}' scan {scan_numbers[0]}.")
        counter = active["active_counter"]
        counter_reason = active["reason"]
        first_df = read_processed_scan(file_name, scan_numbers[0])
        if first_df is not None:
            counter_warning = _counters.counter_selection_warning(first_df, counter)
    else:
        counter_reason = "caller-specified counter"

    normalized_scans = []
    used_scans = []
    for sn in scan_numbers:
        df = read_processed_scan(file_name, sn)
        if df is None:
            continue
        try:
            energy, norm = _norm.normalize_series(
                df, counter, normalize_by="I0", mode=normalization,
            )
        except KeyError:
            continue
        normalized_scans.append(pd.Series(norm, index=energy, name=f"S{sn:03d}"))
        used_scans.append(sn)

    if not normalized_scans:
        raise ValueError(
            f"No valid scans to normalize in '{file_name}' on counter '{counter}'."
        )

    combined = _concat_aligned(normalized_scans)
    combined.attrs["counter"] = counter
    combined.attrs["counter_reason"] = counter_reason
    combined.attrs["counter_explicit"] = counter_explicit
    combined.attrs["counter_warning"] = counter_warning
    combined.attrs["normalization"] = normalization

    combined = _apply_energy_window(combined, e_min, e_max)

    return combined, file_name, counter, used_scans


# Backward-compat shim — implementation lives in ``science.reduce.reps``.
_estimate_per_rep_noise = _reps.estimate_per_rep_noise


def average_energy_scans_arrays(
    file_name=None,
    e_min=None,
    e_max=None,
    weighting: str = "equal",
    scan_numbers=None,
    counter=None,
    normalization: str = "edge_step",
):
    """Array-returning core of :func:`average_energy_scans`.

    Returns ``(info, result_df)`` where ``info`` is the summary dict
    WITHOUT the rendered ``"data"`` string and ``result_df`` is the
    averaged DataFrame (index=energy, columns=``average``, ``std``).
    On failure returns ``({"error": ...}, None)``.

    Use this from numeric consumers (plotting, analysis) — the
    ``to_string()`` rendering in :func:`average_energy_scans` exists
    only for LLM-facing tool output.
    """
    try:
        combined, file_name, counter, used_scans = get_normalized_scan_arrays(
            file_name, e_min=e_min, e_max=e_max, scan_numbers=scan_numbers,
            counter=counter, normalization=normalization,
        )
    except ValueError as e:
        return {"error": str(e)}, None

    if weighting == "inverse_variance":
        # For weighting we need a baseline estimate from the FULL scan, not the
        # windowed slice (the slice may not include the baseline).
        full_combined, _, _, _ = get_normalized_scan_arrays(
            file_name, scan_numbers=scan_numbers,
            counter=counter, normalization=normalization,
        )
        sigmas = _estimate_per_rep_noise(full_combined)
        weights = 1.0 / np.square(sigmas)
        weights = weights / weights.sum()
        avg = (combined.values * weights[np.newaxis, :]).sum(axis=1)
        # Weighted std across reps (using the same weights, ddof~0)
        diff = combined.values - avg[:, np.newaxis]
        var = (np.square(diff) * weights[np.newaxis, :]).sum(axis=1)
        std = np.sqrt(var)
        avg = pd.Series(avg, index=combined.index)
        std = pd.Series(std, index=combined.index)
        weights_used = weights.tolist()
    elif weighting == "equal":
        avg = combined.mean(axis=1)
        std = combined.std(axis=1)
        weights_used = None
    else:
        return {"error": f"Unknown weighting '{weighting}'. Use 'equal' or 'inverse_variance'."}, None

    result_df = pd.DataFrame({"energy": avg.index, "average": avg.values, "std": std.values})
    result_df = result_df.set_index("energy")

    info = {
        "file_name": file_name,
        "active_counter": counter,
        "counter_reason": combined.attrs.get("counter_reason"),
        "normalization": combined.attrs.get("normalization", normalization),
        "num_scans_averaged": len(used_scans),
        "scan_numbers": used_scans,
        "num_points": len(result_df),
        "weighting": weighting,
        "energy_window": [e_min, e_max] if (e_min is not None and e_max is not None) else None,
    }
    if combined.attrs.get("counter_warning"):
        info["counter_warning"] = combined.attrs["counter_warning"]
    if weights_used is not None:
        info["weights_used"] = [round(w, 6) for w in weights_used]
    return info, result_df


def average_energy_scans(
    file_name=None,
    e_min=None,
    e_max=None,
    weighting: str = "equal",
    scan_numbers=None,
    counter=None,
    normalization: str = "edge_step",
):
    """Average all energy scans in a SPEC file after edge-step normalization.

    Parameters
    ----------
    file_name : str, optional
        SPEC file name. If None, uses the most recent file.
    e_min, e_max : float, optional
        Restrict the returned average to this energy window. Normalization is
        still done on the full scan; only the output is windowed.
    weighting : {"equal", "inverse_variance"}, default "equal"
        "equal": unweighted mean across scans.
        "inverse_variance": weight each scan by 1/sigma_i^2 where sigma_i is
        estimated from the post-edge baseline std of that scan. Higher-SNR
        spots contribute more.
    scan_numbers : list[int], optional
        If given, restrict to these specific scan numbers.

    The returned dict carries the averaged data rendered via ``to_string``
    under ``"data"`` (LLM-facing). Numeric consumers should call
    :func:`average_energy_scans_arrays` instead of re-parsing that text.
    """
    info, result_df = average_energy_scans_arrays(
        file_name=file_name, e_min=e_min, e_max=e_max,
        weighting=weighting, scan_numbers=scan_numbers,
        counter=counter, normalization=normalization,
    )
    if result_df is None:
        return info
    out = {k: v for k, v in info.items() if k != "weights_used"}
    out["data"] = df_to_llm_text(result_df)
    if "weights_used" in info:
        out["weights_used"] = info["weights_used"]
    return out


def average_latest_energy_scans(e_min=None, e_max=None, weighting: str = "equal",
                                counter=None, normalization: str = "edge_step"):
    """Find the latest file with >1 energy-motor scan and return the average."""
    b = _collector()
    if b is not None:
        # collector semantics: the most recent scan group with >1 sweep
        file_name = next(
            (m["file_name"] for m in b.list_scans(limit=500)
             if len(b.get_scan_numbers_for_file(m["file_name"])) > 1), None)
    else:
        file_name = local_data.average_latest_energy_scans_file()
    if not file_name:
        return {"error": "No file found with more than 1 energy scan."}
    return average_energy_scans(
        file_name=file_name, e_min=e_min, e_max=e_max, weighting=weighting,
        counter=counter, normalization=normalization,
    )


def group_scans_by_spot(file_name, tol_mm: float = 0.05):
    """Group a file's scans by sample spot, using motor positions (Sx, Sy, Sz).

    Two scans are considered the same spot if their Sx, Sy, Sz all agree within
    `tol_mm`. Returns a list of {"spot_id": int, "center": {Sx,Sy,Sz},
    "scan_numbers": [...]} entries, ordered by first-appearance.

    Reads motor_positions from each scan's df.attrs. Scans missing Sx/Sy/Sz are
    grouped under spot_id=-1 (unknown).
    """
    scan_numbers = _scan_numbers_for_file(file_name)
    if not scan_numbers:
        return {"error": f"No scans found for file '{file_name}'."}

    def _motor_xyz(motors: dict) -> tuple[float, float, float] | None:
        """Pull (Sx, Sy, Sz) from motor_positions, tolerating the silx quirk
        where single-space-separated motor names get returned as one joined
        key. We re-split the joined key and pair with the scan's own #P0
        positions stored on disk if needed.
        """
        if not motors:
            return None
        # Direct lookup — works when names parsed correctly. Sy is optional:
        # stations with a 2-axis sample stage (Sx/Sz only) group on those
        # two, with Sy pinned to 0 so the tolerance test degrades gracefully.
        try:
            return (
                float(motors["Sx"]),
                float(motors.get("Sy", 0.0)),
                float(motors["Sz"]),
            )
        except (KeyError, TypeError, ValueError):
            pass
        # Fallback: silx joined the motor names. Each key is a long string;
        # the corresponding value is just the FIRST motor's position. We
        # cannot recover Sx/Sy/Sz from this dict alone — pass through.
        return None

    def _motor_xyz_from_p0(file_name: str, sn: int) -> tuple[float, float, float] | None:
        """Last-resort: parse the #P0 / #O0 lines from the raw SPEC file."""
        from beamtimehero_cli.spec_data import local_data as _ld
        cache = _ld._load_cache()
        entry = cache.get(f"{file_name}::{sn}")
        if not entry or not entry.get("file_path"):
            return None
        try:
            from pathlib import Path as _P
            text = _P(entry["file_path"]).read_text(errors="ignore")
        except OSError:
            return None
        # Find the file-level #O0 (first one) and the per-scan #P0.
        names: list[str] = []
        for line in text.splitlines():
            if line.startswith("#O0 "):
                names = line[4:].split()
                break
        if not names:
            return None
        # Now find the scan's own #P0
        in_scan = False
        for line in text.splitlines():
            if line.startswith(f"#S {sn} "):
                in_scan = True
                continue
            if in_scan and line.startswith("#S "):
                break
            if in_scan and line.startswith("#P0 "):
                try:
                    vals = [float(x) for x in line[4:].split()]
                except ValueError:
                    return None
                idx = {n: i for i, n in enumerate(names)}
                if "Sx" in idx and "Sz" in idx:
                    try:
                        sy = vals[idx["Sy"]] if "Sy" in idx else 0.0
                        return vals[idx["Sx"]], sy, vals[idx["Sz"]]
                    except IndexError:
                        return None
                return None
        return None

    spots = []  # list of dicts: {"center": (Sx, Sy, Sz), "scan_numbers": [...]}
    unknown = []

    for sn in scan_numbers:
        df = read_processed_scan(file_name, sn)
        if df is None or not hasattr(df, "attrs"):
            unknown.append(sn)
            continue
        motors = df.attrs.get("motor_positions") or {}
        xyz = _motor_xyz(motors) or _motor_xyz_from_p0(file_name, sn)
        if xyz is None:
            unknown.append(sn)
            continue
        sx, sy, sz = xyz

        matched = False
        for spot in spots:
            cx, cy, cz = spot["center"]
            if abs(cx - sx) <= tol_mm and abs(cy - sy) <= tol_mm and abs(cz - sz) <= tol_mm:
                spot["scan_numbers"].append(sn)
                matched = True
                break
        if not matched:
            spots.append({"center": (sx, sy, sz), "scan_numbers": [sn]})

    out = []
    for i, spot in enumerate(spots):
        cx, cy, cz = spot["center"]
        out.append({
            "spot_id": i,
            "center": {"Sx": round(cx, 4), "Sy": round(cy, 4), "Sz": round(cz, 4)},
            "scan_numbers": spot["scan_numbers"],
            "n_scans": len(spot["scan_numbers"]),
        })

    if unknown:
        out.append({"spot_id": -1, "center": None, "scan_numbers": unknown, "n_scans": len(unknown)})

    return {
        "file_name": file_name,
        "tol_mm": tol_mm,
        "n_spots": len([s for s in out if s["spot_id"] != -1]),
        "spots": out,
    }


def get_raw_counter_arrays(file_name=None, scan_numbers=None, counter=None):
    """Load raw active-counter arrays per scan (NOT normalized), aligned on a
    common energy grid. Used for counts-based Poisson floor calculations.

    Returns (combined_counts_df, file_name, counter, used_scans). The DataFrame
    holds the active counter divided by count time (rate, in counts/sec) so
    different scans with different count times are comparable in shape; total
    counts per point per scan = rate * count_time, exposed in attrs.

    ``counter`` — if None, auto-selected; pass explicitly to match the counter
    used by the normalized pipeline (see ``ref counter-selection``).
    """
    if file_name is None:
        file_name = get_most_recent_file()
        if file_name is None:
            raise ValueError("No data files found.")
    if scan_numbers is None:
        scan_numbers = _scan_numbers_for_file(file_name)
    if not scan_numbers:
        raise ValueError(f"No scans found for file '{file_name}'.")

    if counter is None:
        active = get_active_counter(file_name, scan_numbers[0])
        if active is None:
            raise ValueError(f"Could not load scan data for '{file_name}' scan {scan_numbers[0]}.")
        counter = active["active_counter"]

    series_list = []
    used = []
    count_times = []
    for sn in scan_numbers:
        df = read_processed_scan(file_name, sn)
        if df is None or counter not in df.columns:
            continue
        energy = df.index.values.astype(float)
        ct = df.attrs.get("count_time") or 1.0
        rate = df[counter].values.astype(float) / float(ct)
        series_list.append(pd.Series(rate, index=energy, name=f"S{sn:03d}"))
        used.append(sn)
        count_times.append(float(ct))

    if not series_list:
        raise ValueError(f"No scans with counter '{counter}' found in '{file_name}'.")

    combined = _concat_aligned(series_list)
    combined.attrs["count_times"] = count_times
    combined.attrs["counter"] = counter
    return combined, file_name, counter, used
