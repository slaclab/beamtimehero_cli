# Tender (SSRL BL 6-2a) data and how it is analysed

Reference for agents answering questions about a Tender beamtime. Served by
`beamtimehero ref tender-analysis` once integrated. The worked example with
figures, on real data, is chemcat's `static/docs/tender-story.html`. Every
number quoted below comes from it or from the bundled Na₂SO₄ series.

## What the data is

- **Detector.** An Andor CCD, 512 rows × 2048 columns, written as `.sif`
  files. Each file is a stack of one or more frames. The analyser crystal
  disperses emitted X-rays along the **2048 columns**, so a column is an
  *emission* energy: a pixel number until an elastic calibration maps it to
  eV. The 512 rows are spatial and get summed away.
- **Header.** The acquisition comment carries `mono` (incident energy, eV),
  `I0` (the incident-beam monitor), `I1` and `exptime`. The detector clock in
  the header can be wrong, so never trust header dates.
- **Files are raw ADU.** An electronic pedestal (common mode, ~920 ADU,
  read-out noise ~3 ADU) sits under everything. Even the brightest image's
  photons raise a column sum only ~11 % above it, so nothing is ever
  computed on raw sums.
- **Layout.** A beamtime directory holds one subdirectory per compound. The
  real BL 6-2a beamtime has ~20 of them and 16 893 files. All analysis works
  on one compound directory at a time.
- **Measurements, not files.** Files are grouped by NAME (never by content)
  into measurements:
  - **RIXS / HERFD** (`..._RIXS_<series>_<energy>.sif`): an incident-energy
    series, one image per energy, plus one `*_dark.sif`. Reduced to a RIXS
    map, then a HERFD spectrum and TFY.
  - **XES** (`..._<line>_<energy>eV_<scan>.sif`): several scans at ONE
    incident energy, each with a dark. Reduced to one emission spectrum.
  - **Left out, with a reason**: `dark` (paired as background), `aux`
    (alignment, calibration, test), `echem` (operando electrochemistry
    naming: skipped by default, so it currently cannot be processed),
    `elastic` (only for calibration), `energy_out_of_range`.
  - Repeats of a RIXS series share sample + emission line and differ in
    series index (`_RIXS_01`, `_RIXS_02`).

## The reduction, step by step

Each step below lists the setting that controls it and its default.

| # | Step | What happens | Setting (default) | Watch out for |
|---|---|---|---|---|
| 1 | Pedestal | The zero peak of the ADU histogram is measured per frame | `bcg_adjust` (on): scale the background to each frame's pedestal | A pedestal that jumps between frames; a histogram with no photon tail (beam lost) |
| 2 | Background | The background is subtracted from every frame | `dark`: `auto` = the paired `_dark.sif`; `none` = min-projection (per-pixel minimum over the measurement's own frames) | **A "dark" taken with beam on the sample.** It carries the emission band and removes real photons non-uniformly: the Na₂SO₄ `_01` white line lost 62 % and came out at 7.81 edge steps instead of 3.50. Nothing in the pipeline checks this, and the automatic first pass uses the dark. RIXS with 2+ paired darks falls back to the min-projection silently. |
| 3 | Photon extraction | A pixel is kept if it is > `low`/4 and its 3×3 sum is > `low`; clusters are kept if their sum is > `xray`; pixels > `hi` are cosmics | `threshold` = [bcg_cutoff, low, xray, hi], default [60, 100, 170, 2000] ADU | The thresholds are in ADU, and ADU scale with photon energy (S Kα ≈ 230 ADU, Ag L ≈ 333). The single-photon peak in the grain histogram must sit clearly above `xray`. `bcg_cutoff` is used only by the two-pass evolution mode, never by the portal. |
| 4 | Curvature | One "banana" curve is fitted on the summed signal and applied to every image | fitted (not a portal setting) | In the pinned library (c9ab6fd) the correction shifts pixels *within* columns, so it cannot change a spectrum or a HERFD. The real bend, which is sideways (21 columns on the elastic line), is uncorrected and broadens lines ~2.5×. A fix exists (deanSLAC fork b39b2a7) but is not deployed. On a narrow line, the fit can also extrapolate past the detector and crash. |
| 5 | Spectrum | Column sums of the cleaned image | — | Features identical at every incident energy (hot columns, bad dark); a line running off the detector edge |
| 6 | ROI / HERFD | Sum `n` columns around `central_pix` for each image, giving one HERFD point per incident energy | `n` = 7 (the bare library call defaults to 3!), `central_pix` = auto | **The auto-centre is ONE gaussian on the last 10 files in file order.** On a Kα₁/Kα₂ doublet it lands in the valley (Na₂SO₄: 1314, with peaks at 1276 and 1333). The white line then reads 4.38 / 3.50 / 2.87 edge steps for lower peak / auto / upper peak. Pick the centre on purpose, and use the same one for every sample compared. |
| 7 | TFY | Whole-detector sum per point | — | TFY is broader than HERFD (Na₂SO₄ white line 3.0 vs 2.4 eV FWHM) |
| 8 | I0 | Each point is divided by its header I0 | `i0_corr` (on) | I0 = 0 or missing is silently treated as 1, which makes a spike. Beam dumps show as sudden drops. |
| 9 | Normalisation | Pre-edge line + post-edge curve; divide by the edge step | e0, pre1, pre2, norm1, norm2, nnorm: larch defaults (**Tender jobs cannot set them yet**) | Tender scans often end ~50 eV above E0, where larch's default post-edge (E0+15…) sits on resonances. Na₂SO₄: edge step 18.4 instead of ~9.8, `norm` ends at 0.52 instead of 1, and every normalised height is ~1.9× too small. Check that `norm` ends near 1. |
| 10 | Averaging repeats | Series put on a common energy grid; mean, σ, n | E0 alignment (not in the library) | Na₂SO₄ `_01`/`_02` differ by 1.40 eV in E0 with identical mono readbacks. Averaging unaligned blurs the edge. Aligned, their spread is at counting noise. |
| 11 | Emission calibration (XES) | Elastic scans at ≥ 2 mono energies give pixel → eV | calibration JSON from the notebook | Only valid for the geometry it was measured in. It is an extrapolation outside the calibrated span (the sample's own elastic peak checks it: ±0.2 eV in the story). Portal XES jobs write a pixel axis only. |

## Where results come from

- **Automatic first pass.** Every settled measurement of an active Tender
  beamtime is reduced with DEFAULT settings (paired dark, auto ROI, larch
  normalisation) and stamped `origin: first-pass`. These results are what
  users see first. They inherit every pitfall in rows 2, 6 and 9 above.
- **Processing tab jobs.** `tender-herfd` (RIXS → `<label>_herfd.csv`:
  energy_eV, mu, norm, flat, tfy) and `tender-xes` (XES →
  `<label>_xes.csv`: pixel, intensity). The job's settings are in its
  manifest, and the CSV header records the thresholds, background, central
  pixel used, normalisation method and pre-edge e0/edge step.
- **Notebooks** (`~/tender/01..04`, `calibration.ipynb`) run the same
  library at the same commit. Presets saved there (`~/work/<bl>/<bt>/tender/*.json`)
  can be replayed from the Processing tab.

## How to walk a user through it (tools)

1. `tender_list_measurements`: which compounds, then which measurements,
   what is left out and why, what has been processed, and which series are
   repeats.
2. `tender_inspect_measurement`: headers (energies, I0), then **is the dark
   dark?** Do this before trusting any first-pass result.
3. `tender_preview_image`: one image through steps 1-5. Use it to explain
   the format, to tune thresholds (grain peak vs the xray gate), and to see
   a background choice.
4. `tender_herfd`: the RIXS map with the band, the doublet and auto-centre
   check, ROI comparisons (`compare_centres='peaks'`) and the normalisation
   check with post-edge candidates. ROI, I0 and normalisation changes are
   instant (the map is cached).
5. `tender_xes`: the emission spectrum, the per-scan drift/damage check, and
   an energy axis from a calibration.
6. `tender_results`: the processed record, a comparison on ONE
   normalisation with the settings that differ, and repeat averaging with E0
   alignment.

Every tool returns `reproduce_with`: the exact Processing-tab job for what
it showed. Anything under `not_yet_job_fields` (normalisation ranges,
calibration) cannot be reproduced by a job yet. Say so rather than implying
it can.

## Interpreting (be careful)

- The incident-energy axis is the monochromator readback with no reference
  measured alongside. Absolute energies are good to ~1 eV. Within-session
  differences are more reliable.
- S K-edge: the edge moves up with oxidation state. Sulfate (S⁶⁺) is one
  intense narrow white line (Na₂SO₄: E0 2479.8 eV, peak 2481.0 eV).
- Ag L₃-valence XES: the shape reflects the bonding partner. The band is
  wider and shifted for covalent Ag-S than for ionic Ag-O. Differences of a
  few tenths of an eV are the result, and absolute energies are
  approximate.
- Before you call a difference chemistry, compare only spectra reduced with
  the same settings, and check scan-to-scan stability (damage shows as a
  steady trend).
