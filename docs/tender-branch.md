# The `tender` tree (SSRL BL 6-2a)

Six read-only leaves for Tender data: Andor `.sif` detector images reduced
to RIXS maps, HERFD and XES spectra with
[tender-analysis](https://github.com/slaclab/tender-analysis) (the
optional `[tender]` extra, pinned to the commit chemcat's jobs run).

| Leaf | What it does |
|---|---|
| `tender_list_measurements` | Compounds, then one compound's measurements grouped from filenames: darks, repeats, left-out files and why, processed status |
| `tender_inspect_measurement` | Header checks (energies, I0), and whether the paired dark is really dark |
| `tender_preview_image` | One image through pedestal, background, thresholds and events, with ADU histograms |
| `tender_herfd` | A chat-sized re-reduction: RIXS map to HERFD/TFY to normalised, with ROI and normalisation checks; the map is cached |
| `tender_xes` | Emission spectrum, per-scan drift, optional elastic calibration |
| `tender_results` | The processed record (job manifests + CSVs): list, compare, average repeats |

Code: `src/beamtimehero_cli/tender/` (not `science/`: it reads files and
the environment). Data format and every pitfall: `beamtimehero ref
tender-analysis`. Tests: `tests/test_tender.py`. The equivalence tests
there pin a chat re-reduction to the `tender-herfd` / `tender-xes` jobs,
so a tender-analysis bump that changes the maths fails here first.

Environment: `TENDER_DATA_DIR` (falls back to `BL_SCAN_DIR`),
`TENDER_PROCESSED_DIR`, `TENDER_WORKSPACE_DIR`, `TENDER_CACHE_DIR`,
`TENDER_MAX_FRAMES` (0 = never decode frames), `TENDER_MAX_FILE_FRAMES`.

Staged and designed in `lx3g:git/tender-tools.git` (its
PLAN-bth-integration.md has the design and the library issues found).
