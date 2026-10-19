# Changelog

Tagged releases that apps pin (see CONTRIBUTING.md "Releases and the apps that pin them").

## Unreleased

- cherfd edge scans: `cherfd_start_scan`, `cherfd_check_readiness` and
  `cherfd_check_command` take `cscan Fe`, `cscan Fe xanes`, `cscan PtL3 exafs`,
  `cscan Fe xanes 80` (the scan server's reviewed default for an edge,
  cherfd_claude `cscan_box/edge_scans.py`). The tools expand it through
  `/check_command` and validate, estimate and send the full command; an edge
  not enabled on the server, or a server that predates edge scans, is refused
  before anything is sent. The mock expands a small fixed table.
  `cherfd_check_readiness` no longer raises `KeyError` on a command that does
  not parse.
- SPEC log glob is a station default (`BTH_SPEC_LOG_FILE_PATTERN`): BL4-1 reads
  `log_*` (41_spec writes `log_fourone_MM-DD-YYYY`), so `list_logs`,
  `search_logs` and `get_latest_log_entries` find its logs without a host
  patch. BL15-2 keeps `log__*`.
- `shutter`: `fson` is refused at BL4-1 (`station.SHUTTER_COMMANDS`). It has no
  fast shutter and its SPEC no longer accepts `fson`; hold the shutter open with
  `fsopen` then `fsoff`. BL15-2 unchanged.
- Agent surfaces: the motor allow-list guards moves only. Read-only tools
  (`mutates` false, today `read_motor_position`) may name any motor, so a role
  that moves the table only through a dedicated tool can still `wm Tz`. A
  surface that carries no motor-taking write tool no longer needs a motor set.

## v0.7.0 — 2026-10-12

Additive; BL15-2 defaults unchanged (unset `BTH_STATION` behaves as before).

- **Station services, read-only** (new lineage source `station_service`; HTTP GET only):
  `get_sample_environment` (gas mixer / ion-chamber HV + temperature PVs from
  `BTH_SAMPLE_TEMP_PVS`), `get_ge_detector_status` (xsp32 GUI `/api/state`),
  `get_motor_diagnostics` (Motor Analyzer config + recent runs). BL4-1 URLs are
  station defaults (`BTH_GASMIXER_URL`, `BTH_XSP32_GUI_URL`, `BTH_MOTOR_ANALYZER_URL`).
- **App logs**: `list_app_logs`, `read_app_log`, `search_app_logs` over named roots
  (`BTH_APP_LOG_ROOTS`; BL4-1 default = its apps' `logs/`), traversal-safe.
- **Provenance**: `export_results_bundle` (copy + sha256 manifest + zip, sources
  restricted to the data roots), `record_provenance` / `get_provenance`.
- **BL4-1 SPEC** (refused at other stations): `measure_darks` (non-interactive
  measure_offsets), `set_check_beam` / `get_check_beam`, `get_crystal_set`.
- `post_slack_dm`.
- `cherfd_build_exafs_command`: continuous EXAFS, equal-k regions with k spacing <= dk
  (policy `EXAFS_DK`, `EXAFS_K_REGIONS`, `ETOK`).
- `capture_sample_image`: camera per station (BL4-1: none until its RPi-Cam is
  installed and `SAMPLE_CAM_HOST` set; no longer reaches the BL15-2 Pi) and
  `resolution=low|full`.
- `SPEC_EVAL_URL` defaults to :5016 at BL4-1 (its :5006 is the gas mixer).
- CLI: leaf `--help` shows the tool description; trees with no tools are not listed;
  `station.HIDDEN_TREES` hides `xrs` and `tender` at BL4-1; `db` help text fixed.
- `cherfd_export_merged` hint no longer says to copy exports into BL_SCAN_DIR.
- Tests: `tests/conftest.py` isolates every run (temp data dir, SPEC/CHERFD mocks,
  no non-loopback network).
- Docs: README covers BL4-1; CONTRIBUTING lists every consumer and its pin.

## v0.6.1 — 2026-10-12

- `cherfd_channel_stats` (offline `cherfd` tree, read-only): per-channel counts per
  frame and per second (counts / the frame's `trig_2` gate) for `counter_*` and
  detector sums, per-frame means for `adc_*`, std/sqrt(mean), zero fraction;
  ln(a/b) per frame for requested ratios with the edge jump from pre/post windows;
  per-sweep means as a run trend, overall and per direction. Science in
  `science/cherfd/channels.py`. Additive: no existing tool or default changes.

## v0.6.0 — 2026-10-12

- `station.SCAN_MODES` / `scan_modes()` / `default_scan_mode()` / `check_scan_mode()`:
  BL4-1 allows continuous (default) and step; BL15-2 step only. Unset `BTH_STATION`
  is BL15-2, so behaviour is unchanged there. Not overridable by any variable.
- The table never moves in a continuous scan: `StationProfile.table_tracking`
  (False at both stations). `cherfd_set_table_tracking enable=true` and
  `cherfd_set_table_anchor` are refused, `cherfd_start_scan` refuses while the
  server has tracking on, readiness reports it as a blocker, and a server
  advertising `table_tracking_available` counts as a station mismatch. The mock
  server refuses the same. (Anchor storage is newly refused at BL15-2 too, where
  table tracking was already unavailable.)
- Includes fa32666 (`tool caget`, a read-only EPICS CA read).

## v0.5.0 — tag on 6e27009

The commit autonomous_fourone was tested and pinned at on 2026-10-11 (station
setting `BTH_STATION`, BL4-1 cherfd support, `cherfd_set_detector`, merged
two-column files in `CHERFD_EXPORT_DIR`).
