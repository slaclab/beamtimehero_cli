"""Tree-classification for tool definitions.

Each tool lives in a tree branch (``tool``, ``spec-read``, ``spec-write``,
``db``, plus the deployment-specific ``s3df``/``s3df psql``, ``spec-file``,
and ``slack`` branches added in Phase 2).

The classification follows this precedence, first match wins:

1. The tool definition's own ``"tree"`` field (a string like ``"s3df"``
   or a dotted path like ``"s3df.psql"`` for sub-branches). The most
   specific signal, and the only one that lets two definitions sharing a
   name sit on different branches.
2. ``CATEGORY_OVERRIDES`` — an explicit per-tool-name override (used to
   move file-cache scan tools out of ``tool`` and into ``spec-file``
   without touching every definition entry).
3. Lineage-driven rules: ``source == "autonomy_db"`` → ``db``;
   ``mutates`` → ``spec-write``; ``spec_command`` set → ``spec-read``;
   otherwise → ``tool``.

``mutates`` is a declared field on the lineage entry, not an inference
from the JSON schema. The rule used to read "requires ``justification``",
which meant the safety class of a tool was a side effect of how its
arguments happened to be spelled: dropping the flag from a schema moved a
motor-moving tool onto ``spec-read``. It also gave consumers no way to
mark a tool mutating without a ``justification`` argument. See
``lineage.py`` for what ``mutates`` does and does not cover.

Lives in ``tool_catalog/`` (not ``cli/``) so both the CLI parser and the
DISPATCH builder can import without a circular dependency.
"""
from __future__ import annotations

from beamtimehero_cli.tool_catalog.lineage import TOOL_LINEAGE


# Tool name → category override. Used to relocate tools whose default
# classification doesn't match the desired branch layout. Each value is
# either a top-level branch name (``"spec-file"``) or a dotted path for
# nested branches (``"s3df.psql"``).
#
# File-cache scan tools historically lived under ``tool`` because they
# fit no other branch. With the ``spec-file`` branch added we move them
# here without touching every individual definition entry.
CATEGORY_OVERRIDES: dict[str, str] = {
    "list_scans": "spec-file",
    "get_latest_scan": "spec-file",
    "read_scan": "spec-file",
    "get_active_counter": "spec-file",
    "get_scan_deadtime": "spec-file",
    "normalize_scan": "spec-file",
    "average_scans": "spec-file",
    "plot_scan": "spec-file",
    "plot_averaged_scans": "spec-file",
    "plot_scan_stack": "spec-file",
    "plot_first_half_vs_second_half": "spec-file",
    "plot_running_average": "spec-file",
    "plot_feature_evolution": "spec-file",
    "group_scans_by_spot": "spec-file",
    "analyze_per_spot": "spec-file",
    "analyze_convergence": "spec-file",
    "analyze_efficiency": "spec-file",
    "analyze_feature_evolution": "spec-file",
    # CAT-10 scientific-interpretation tools read the same file cache
    "record_energy_calibration": "spec-file",
    "get_energy_calibration": "spec-file",
    "extract_xas_descriptors": "spec-file",
    "interpret_oxidation_state": "spec-file",
    "interpret_coordination_geometry": "spec-file",
    "summarize_sample_chemistry": "spec-file",
    # CAT-10 atomic descriptor tools — same file cache, same tree as the
    # capstones they decompose (no new CLI branch; see categorize header).
    "identify_edge": "spec-file",
    "find_edge_e0": "spec-file",
    "normalize_xas_intensity": "spec-file",
    "fit_xas_pre_edge": "spec-file",
    "fit_xas_white_line": "spec-file",
    "assess_xas_quality": "spec-file",
    "detect_per_scan_drift": "spec-file",
    # CAT-10 cross-file comparison leaves (LCF / registration / differences)
    # — same file cache, and the merged two-col ingest lives on that path too.
    "compare_xas_to_references": "spec-file",
    "align_spectra": "spec-file",
    "difference_spectrum": "spec-file",
    # CAT-XRS · dedicated X-ray Raman analysis branch (energy-loss axis).
    # Kept separate from the XAS/HERFD spec-file tools so the Raman-specific
    # processing + interpretation surface is coherent on its own.
    "calibrate_energy_loss": "xrs",
    "build_loss_axis": "xrs",
    "average_xrs_scans": "xrs",
    "subtract_compton_background": "xrs",
    "normalize_xrs": "xrs",
    "overlay_xrs_spectra": "xrs",
    "sum_crystals": "xrs",
    "align_crystals": "xrs",
    "tag_crystal_q": "xrs",
    "extract_xrs_descriptors": "xrs",
    "interpret_xrs_oxidation_state": "xrs",
    "interpret_q_dependence": "xrs",
    "compare_xrs_to_references": "xrs",
    "assess_xrs_quality": "xrs",
    "summarize_xrs_chemistry": "xrs",
    # CAT-EXAFS · dedicated k-space analysis branch. Kept separate from the
    # XANES/HERFD spec-file tools (which stop at normalized mu(E)) so the
    # chi(k)/FT surface is coherent on its own; reads SPEC files or SSRL
    # EXAFS Data Collector ASCII directories via spec_data/exafs_data.py.
    "list_collector_scans": "exafs",
    "extract_chi": "exafs",
    "fourier_transform_chi": "exafs",
    "exafs_products": "exafs",
    "overlay_chi_spectra": "exafs",
}


def categorize(tool_def: dict) -> tuple[str, ...]:
    """Return the tree path a tool belongs to as a tuple of segments."""
    name = tool_def.get("function", {}).get("name", "")

    # Explicit ``tree`` field on the definition wins over everything —
    # it's the most specific signal and lets two definitions sharing a
    # name (e.g. spec-file/list_scans + s3df/list_scans) coexist.
    explicit = tool_def.get("tree")
    if explicit:
        return _split(explicit) if isinstance(explicit, str) else tuple(explicit)

    # Per-name overrides apply only to definitions that don't pin their
    # own tree — used to move the file-cache scan tools out of "tool"
    # without touching each entry.
    if name in CATEGORY_OVERRIDES:
        return _split(CATEGORY_OVERRIDES[name])

    # ``.get`` on both sides: a consumer may register a definition with no
    # lineage entry at all, and that must fall through to the default
    # branch rather than raise from inside the parser build.
    lineage = TOOL_LINEAGE.get(name) or {}
    if lineage.get("source") == "autonomy_db":
        return ("db",)

    if lineage.get("mutates"):
        return ("spec-write",)

    if lineage.get("spec_command") is not None:
        return ("spec-read",)

    return ("tool",)


def _split(tree: str) -> tuple[str, ...]:
    """Parse a dotted-path tree string into a tuple of segments."""
    return tuple(s for s in tree.split(".") if s)
