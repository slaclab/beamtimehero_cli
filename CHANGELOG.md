# Changelog

Tagged releases that apps pin (see CONTRIBUTING.md "Releases and the apps that pin them").

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
