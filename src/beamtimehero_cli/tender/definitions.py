"""The ``tender`` tree's schemas and lineage, appended to the catalog by
``tool_catalog/definitions.py`` and ``tool_catalog/lineage.py``.

  TOOL_DEFINITIONS   OpenAI function schemas (what the agent sees)
  LINEAGE            lineage entries (mutates=False for every leaf)
  CATEGORY           leaf -> tree, mirrored in categorize.CATEGORY_OVERRIDES

Stdlib only: the catalog imports this at build time.
"""

from __future__ import annotations

TREE = "tender"

_SAMPLE = {"type": "string", "description": (
    "Compound directory inside the beamtime (one per compound), as listed by "
    "tender_list_measurements; '' = .sif files sitting directly in the beamtime directory.")}
_MEAS = {"type": "string", "description": (
    "Measurement label from tender_list_measurements (tender_analysis "
    "Measurement.label(), e.g. 'Na2SO4_pellet_20pcSucrose_Ka_RIXS_01').")}
_THRESH = {"type": "array", "items": {"type": "number"}, "description": (
    "ADU thresholds [bcg_cutoff, low, xray, hi] (or [bcg_cutoff, low, hi], xray derived "
    "as 1.1*low). Omit for the library default [60, 100, 170, 2000]. low gates the 3x3 "
    "photon test, xray the per-grain sum, hi rejects cosmics; bcg_cutoff is used only by "
    "the two-pass evolution mode, never by the portal.")}
_DARK = {"type": "string", "enum": ["auto", "none"], "description": (
    "Background. auto = the paired *_dark.sif (RIXS: only when exactly ONE is paired; "
    "otherwise the library silently uses the min-projection). none = min-projection "
    "(per-pixel minimum over the measurement's own frames). Same meaning as the "
    "Processing tab's Background switch.")}
_BCGADJ = {"type": "boolean", "description": (
    "Scale the background to each frame's pedestal (common mode) before subtracting. "
    "Default true.")}
_NORM = {k: {"type": "number", "description": d} for k, d in (
    ("e0", "Edge energy override (eV); default: maximum of dmu/dE."),
    ("pre1", "Pre-edge fit start, relative to e0 (eV)."),
    ("pre2", "Pre-edge fit end, relative to e0 (eV)."),
    ("norm1", "Post-edge fit start, relative to e0 (eV). Tender scans often end ~50 eV "
              "above the edge; larch's default start sits on the near-edge resonances."),
    ("norm2", "Post-edge fit end, relative to e0 (eV)."),
    ("nnorm", "Post-edge polynomial degree 0-3 (1 = straight line)."))}

TOOL_DEFINITIONS = [
    {"type": "function", "function": {
        "name": "tender_list_measurements",
        "description": (
            "Tender (BL 6-2a, Andor .sif) discovery. With no sample: the beamtime's compound "
            "directories and their .sif counts. With sample: that compound's files grouped into "
            "MEASUREMENTS (a RIXS/HERFD energy series, or an XES emission measurement at one "
            "incident energy), each with its paired dark, energy range, which background the "
            "default settings would use, whether it has been processed, which RIXS series are "
            "repeats, and every file NOT included with the reason. Filenames only: instant on "
            "thousands of files. Start here."),
        "parameters": {"type": "object", "properties": {"sample": _SAMPLE}, "required": []}}},
    {"type": "function", "function": {
        "name": "tender_inspect_measurement",
        "description": (
            "Pre-flight checks for ONE measurement from its file headers (8 kB per file, no "
            "frame decode): incident energies (header mono vs filename), the I0 monitor (zeros, "
            "which the library silently treats as 1; sudden drops), exposures, frame counts, "
            "whether chat can re-reduce it within budget, and -- reading the dark and one data "
            "image -- whether the paired 'dark' is really dark or was taken with beam on (a "
            "'lit' dark silently removes photons and reshapes every spectrum; the automatic "
            "first pass uses it anyway). Returns the processed record, if any, and the "
            "Processing-tab job to reproduce with the recommended background."),
        "parameters": {"type": "object", "properties": {
            "sample": _SAMPLE, "measurement": _MEAS,
            "check_dark": {"type": "boolean", "description": "Read the dark and one data "
                           "image to test the dark (default true)."}},
            "required": ["measurement"]}}},
    {"type": "function", "function": {
        "name": "tender_preview_image",
        "description": (
            "One detector image (512 x 2048; columns = emission energy, rows summed away) "
            "through the per-frame chain: pedestal and read-out noise, background subtraction, "
            "the three ADU thresholds, photon events, and the image's emission spectrum. "
            "Returns where the single-photon peak sits against the xray gate (is the gate "
            "eating photons?), the emission peaks (doublet?), and two figures: image over "
            "spectrum on a shared column axis, and the three ADU histograms with the "
            "thresholds that gate them. Use to explain the data format, to tune thresholds, "
            "or to see what a background choice does. Reads one file (plus the dark)."),
        "parameters": {"type": "object", "properties": {
            "sample": _SAMPLE,
            "file": {"type": "string", "description": ".sif basename. Or give measurement + point."},
            "measurement": _MEAS,
            "point": {"type": "string", "description": (
                "Which image of the measurement: 'last' (highest incident energy; default for "
                "RIXS), 'first', a 0-based index, or an incident energy in eV (e.g. '2481').")},
            "frame": {"type": "integer", "description": "0-based frame; omit to sum all frames."},
            "dark": {"type": "string", "enum": ["auto", "none", "flat"], "description": (
                "auto = paired dark; none = min-projection over the measurement (reads it "
                "all); flat = pedestal only (a diagnostic, not a job option).")},
            "threshold": _THRESH, "bcg_adjust": _BCGADJ,
            "central_pix": {"type": "integer", "description": "Draw a ROI band at this column."},
            "n": {"type": "integer", "description": "ROI width for the drawn band (default 7)."},
            "show": {"type": "string", "enum": ["events", "bkg_sub", "raw"],
                     "description": "Image shown (default photon events)."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "tender_herfd",
        "description": (
            "The tender-herfd reduction of a RIXS measurement, re-run within a chat budget "
            "and cached: builds the RIXS map (incident energy x emission pixel) with the "
            "given photon/background settings, then reads out the HERFD (ROI band of n "
            "columns around central_pix) and TFY, I0-corrects, and normalises. Reports the "
            "automatic ROI centre and whether it sits in the VALLEY of a Ka1/Ka2 doublet, "
            "compares ROI centres (compare_centres), checks the normalisation (does 'norm' end "
            "near 1? is the post-edge too short for larch's defaults?) and proposes post-edge "
            "ranges, and compares with the processed record. Figures: the map with the band "
            "over the emission profile; HERFD/TFY/normalised. A ROI, I0 or normalisation "
            "change re-uses the cached map (instant); a threshold/background change rebuilds "
            "it. Over budget, it falls back to the processed CSV. Returns reproduce_with: the "
            "Processing-tab job for these settings."),
        "parameters": {"type": "object", "properties": {
            "sample": _SAMPLE, "measurement": _MEAS,
            "threshold": _THRESH, "dark": _DARK, "bcg_adjust": _BCGADJ,
            "central_pix": {"type": "integer", "description": "ROI centre column; omit = automatic (one gaussian over the last 10 points)."},
            "n": {"type": "integer", "description": "ROI width in columns (default 7, as the portal; the bare library default is 3)."},
            "i0_corr": {"type": "boolean", "description": "Divide each point by its I0 (default true)."},
            "compare_centres": {"type": "string",
                                "description": "'peaks' = the emission peaks + the automatic centre, or comma-separated columns (e.g. '1276,1333'): overlay those HERFDs."},
            "rebuild": {"type": "boolean", "description": "Ignore the cached map."},
            **_NORM},
            "required": ["measurement"]}}},
    {"type": "function", "function": {
        "name": "tender_xes",
        "description": (
            "The tender-xes reduction of an XES measurement (all its scans summed, dark-"
            "subtracted, photon-thresholded, curvature-corrected), within a chat budget: the "
            "emission spectrum with its main peak, centroid and FWHM, the per-scan centroid "
            "shift (a damage/drift check), and I0. On a pixel axis unless a calibration is "
            "given (calibration_m/_b, a saved calibration_file from the notebooks, or "
            "elastic_sample = a directory of elastic scans to fit one now); with a calibration "
            "it also checks the sample's own elastic peak at the incident energy. Over "
            "budget, it falls back to the processed CSV."),
        "parameters": {"type": "object", "properties": {
            "sample": _SAMPLE, "measurement": _MEAS,
            "threshold": _THRESH, "dark": _DARK, "bcg_adjust": _BCGADJ,
            "calibration_m": {"type": "number", "description": "eV per pixel."},
            "calibration_b": {"type": "number", "description": "eV at pixel 0."},
            "calibration_file": {"type": "string", "description": "ElasticCalibration JSON basename in the workspace (or its tender/ dir)."},
            "elastic_sample": {"type": "string", "description": "Compound directory of *elastic*.sif scans (each with a dark at the same energy) to fit a calibration from."},
            "window": {"type": "integer", "description": "± columns around the main peak for centroids (default 60)."}},
            "required": ["measurement"]}}},
    {"type": "function", "function": {
        "name": "tender_results",
        "description": (
            "The processed record (automatic first pass and people's Processing jobs), from "
            "the job manifests and CSV headers. action=list: every Tender output with who/what "
            "made it and the settings it used. action=compare: overlay HERFD outputs, "
            "re-normalised with ONE set of ranges so heights compare, and list every setting "
            "that differs between them. action=average: average repeats (outputs=..., or "
            "sample + repeats_of=<measurement> for all its series), aligning E0 first "
            "(align_e0, default true) and reporting the offsets and the spread."),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "compare", "average"]},
            "sample": _SAMPLE,
            "measurement": {"type": "string", "description": "list: only this measurement."},
            "outputs": {"type": "array", "items": {"type": "string"},
                        "description": "Output ids '<job>:<file>' from action=list (≤ 8)."},
            "repeats_of": {"type": "string", "description": "average: a RIXS measurement whose repeats (same sample + line) to average."},
            "align_e0": {"type": "boolean", "description": "Shift repeats onto the first one's E0 before averaging (default true)."},
            **_NORM},
            "required": []}}},
]

NAMES = [d["function"]["name"] for d in TOOL_DEFINITIONS]
CATEGORY = {n: TREE for n in NAMES}

_COMMON = {"spec_command": None, "mutates": False, "source": "filesystem"}

LINEAGE = {
    "tender_list_measurements": {
        **_COMMON,
        "long_description": ("Groups a Tender beamtime's Andor .sif files into runnable "
                             "measurements from filenames alone, the same grouping the "
                             "portal's Processing tab and first pass use."),
        "python_func": "tender_analysis.scan_directory(validate=False) + manifest lookup",
        "output": "JSON: compounds | measurements, repeats, not_included, processed status",
        "source_detail": "directory listings of the beamtime's raw tree",
        "depends_on": []},
    "tender_inspect_measurement": {
        **_COMMON,
        "long_description": ("Header-only checks of one measurement plus a dark-vs-data "
                             "column-profile test of the paired dark."),
        "python_func": "SifFile.metadata per file; diagnostics.dark_check",
        "output": "JSON: energies, I0, darks, dark_check, reproduce_with + dark profile plot",
        "source_detail": "8 kB header per .sif; the dark and one data image decoded",
        "depends_on": ["tender_list_measurements"]},
    "tender_preview_image": {
        **_COMMON,
        "long_description": "One image through reduce_frame, with ADU histograms.",
        "python_func": "tender_analysis.preview.preview + extract_signal(histograms=True)",
        "output": "JSON: pedestal, grains, spectrum peaks + image/spectrum and histogram plots",
        "source_detail": "one .sif decoded (plus the dark)",
        "depends_on": ["tender_list_measurements"]},
    "tender_herfd": {
        **_COMMON,
        "long_description": ("tender-herfd's maths at chat size: HerfdAccumulator (sparse), "
                             "the library's auto-centre, and export.normalize_mu; the RIXS "
                             "map is cached per (measurement, reduction settings)."),
        "python_func": "live.HerfdAccumulator -> pipeline._fit_central_pixel -> normalize_mu",
        "output": "JSON: map build, auto centre, emission peaks, curves, normalisation checks, "
                  "reproduce_with + map/profile and HERFD plots",
        "source_detail": "every .sif of the measurement, within TENDER_MAX_FRAMES",
        "depends_on": ["tender_list_measurements", "tender_inspect_measurement"]},
    "tender_xes": {
        **_COMMON,
        "long_description": ("tender-xes's maths streamed one file at a time, plus optional "
                             "ElasticCalibration."),
        "python_func": "extract_signal per file -> CurvatureCorrection -> ElasticCalibration",
        "output": "JSON: peak, centroid, FWHM, per-scan shift, calibration + spectrum plot",
        "source_detail": "every .sif of the measurement, within TENDER_MAX_FRAMES",
        "depends_on": ["tender_list_measurements"]},
    "tender_results": {
        **_COMMON, "source": "tool_chain",
        "long_description": ("Reads tender-herfd/-xes job manifests and CSVs; compares on one "
                             "normalisation; averages repeats with E0 alignment."),
        "python_func": "manifest.json + CSV header -> average.average_series",
        "output": "JSON: outputs | settings_that_differ, e0, spread + overlay plot",
        "source_detail": "TENDER_PROCESSED_DIR (the beamtime's pipeline/ tree)",
        "depends_on": ["tender_list_measurements"]},
}
