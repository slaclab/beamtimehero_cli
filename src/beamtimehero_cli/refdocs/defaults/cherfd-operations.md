# Running CHERFD from an agent

CHERFD is continuous scanning on the cscan box at SSRL BL15-2 and BL4-1: the
mono sweeps continuously under an FPGA motion profile while the FPGA fires
detector triggers at a fixed rate (and, at BL15-2, the undulator gap tracks
the mono predictively). One **acquisition run** is N sweeps (each optionally
forward + reverse) saved as one pickle set per sweep.

## The station

A deployment sets one variable, `BTH_STATION` (`bl15` = BL15-2, the default;
`bl41` = BL4-1). Everything station-specific follows from it -- for the SPEC
tools as well as these (see `config.example.yaml`):

| | BL15-2 (`bl15`) | BL4-1 (`bl41`) |
|---|---|---|
| Scan detector | Vortex on the 8-channel Xspress3 | 24-element Ge (Xspress3, xsp32 service) |
| Default signal | `vortex` (ROI1 + 2*ROI_double) | `xsum` (good-element sum; also `xsA`, `xsB`, `xs01`..`xs24`) |
| Detector setup | SPEC `xtc 1` hands the Xspress3 to cherfd | the xsp32 service must be running |
| Crystals (A / B) | Si(111) / Si(311), B in use | Si(220) / Si(111), SPEC's `mono.cfg` says which |
| Step size | 1e-5 deg | 2e-5 deg |
| Undulator | yes (gap tools, `undulator_scan` default on) | none: gap tools refuse, `undulator_scan` off |
| cscan_daq counts | yes | no (superseded by cscan_acq; SPEC `csct` counts) |
| Per-sweep overhead | ~17 s | ~4.8 s |

The station profile is `science/cherfd/policy.py STATION_PROFILES`; the live
server reports its own station at `GET /station`. `cherfd_get_station` shows
both, and `cherfd_check_readiness`, `cherfd_start_scan` and
`cherfd_move_energy` refuse when the server is another station. The
scan detector is the station's or `none` (box channels only: transmission,
TFY on a diode). `cherfd_set_detector` chooses between them (refused during
an acquisition; the BL4-1 cscan UI's technique selector writes the same
persisted setting). Readiness reports `none` as a warning; any other
detector is a blocker.

The tools sit on three branches, split like SPEC's so an agent surface can
grant them separately:

| Branch | What | Mutates |
|---|---|---|
| `cherfd` | planning (validate, build, simulate, estimate), sweep data, QC, reduction, analysis, export, logs | never |
| `cherfd-read` | live controller / DAQ state, readiness, wait-for-scan | never |
| `cherfd-write` | start/stop scans, energy and gap moves, tracking, calibration, counts | every leaf, `--justification` required |

The generic tools are not duplicated:

| Need | Use |
|---|---|
| Beam / ring / shutter state | `spec-read get-beam-status` (also folded into `cherfd_check_readiness`) |
| SPEC motors, sample stages, filters | `spec-write ...` |
| XAS interpretation (E0, pre-edge, white line, oxidation state, LCF) | `cherfd_export_merged`, then the `spec-file` tools on that file |
| Staff Slack | `slack post-slack-message` |
| Action history | `db recent-actions` -- cherfd actions are in the same log as `cherfd:<command>` |

```bash
beamtimehero cherfd cherfd-build-command --element Cu
beamtimehero cherfd cherfd-validate-command --command "cherfd 8968 8990 1 9010 .3 9100 1 100"
beamtimehero cherfd-read cherfd-check-readiness --command "cherfd 8968 9100 .4 100"
beamtimehero cherfd-write cherfd-start-scan --command "cherfd 8968 9100 .4 100" --sweeps 2 --reverse true --justification "Cu K HERFD, spot 1"
beamtimehero cherfd-read cherfd-wait-for-scan --timeout-s 600
beamtimehero cherfd cherfd-list-runs --limit 5
```

## The loop

1. **Plan** (no hardware) -- `cherfd_build_command` (from element/edge) or write
   one; `cherfd_validate_command` (station energy window, one direction, speed
   clamp, trigger quantization, harmonic changes at BL15-2);
   `cherfd_simulate_scan` (the production profile generator for this station:
   authoritative refusal/duration); `cherfd_estimate_acquisition_time`.
2. **Pre-flight** -- `cherfd_check_readiness --command ...`. Blockers must be
   empty. It runs the server's `cherfd_check_command` (the profile inside
   MOTOR1's live soft limits) and reads the shared cscan box. The detector
   setup (above) cannot be read from here -- an all-zero signal column is
   the symptom.
3. **Configure** -- `cherfd_set_data_path` (one file_root per sample/spot;
   roots can be reused but runs are then told apart by time).
4. **Acquire** -- `cherfd_start_scan` then `cherfd_wait_for_scan`. The start
   tool confirms from `/status` that the scan really started: the server
   answers `success: true` before the controller has accepted it (it does
   refuse a bad command up front, with HTTP 400). `cherfd_wait_for_scan`
   returns `ok: false` when the scan ended in `error`.
5. **Check** -- `cherfd_get_latest_run` -> `cherfd_assess_run_quality`.
6. **Reduce** -- `cherfd_compare_directions` (fwd/rev offset), then
   `cherfd_merge_sweeps` with `direction_correction_ev` if needed;
   `cherfd_analyze_sweep_drift` (damage, drift) and
   `cherfd_analyze_convergence --target-relative-noise` (more sweeps or move on).
7. **Interpret** -- `cherfd_export_merged`, then the `spec-file` XAS tools.

## Safety model

* **Mock by default, stricter than SPEC_MOCK.** Only `CHERFD_MOCK=0` reaches a
  real server; every other value (unset, `1`, `true`, `""`) is the mock. The
  mock reproduces the real routes' shapes and refusals and persists its state
  across CLI calls. `SPEC_MOCK` does not govern it; the two are independent.
* **Allowlist.** Handlers name a registered command (`cherfd_control/commands.py`);
  nothing else can be sent. Nothing here talks EPICS -- only the cherfd
  REST server and the cscan_daq app, which own the hardware sequencing and its
  interlocks.
* **Justification + action log** on every action, written before dispatch, to
  the same action log as SPEC actions.
* **Safety switches.** The same `safety_switches.json` as SPEC: `spec_write_enabled:
  false` or `cherfd_write_enabled: false` refuses cherfd actions (an unreadable
  file refuses too). `cherfd_read_enabled: false` refuses reads. Stops
  (`cherfd_stop_scan`, `cherfd_stop_gap`, `cherfd_daq_abort`) always go through.
* **Offline validation first.** `cherfd_start_scan` refuses a command that fails
  validation and refuses unless the controller is idle.

## Things the hardware does that an agent must know

* After `cherfd_stop_scan` the controller is latched in `Stopped`; new scans
  are refused until `cherfd_clear_stop`. A stop halts every scan axis (`STOP`,
  then `SPMG=Stop`, waits for DMOV); an axis that does not report stopped
  leaves the controller in `error`.
* The controller ends a sweep in `error` on purpose when it cannot trust it:
  a profile cut short, fewer Ge frames than triggers (BL4-1; the data saved
  before is kept), or the box triggers changing under it (a SPEC count fired
  mid-sweep). Read `error_message`, then `cherfd_clear_stop`.
* One command, one direction: energies all increasing or all decreasing (the
  FPGA profile has one direction bit). Scan back with `reverse`.
* The whole profile must lie inside MOTOR1's soft limits, which are read
  live; `cherfd_check_command` asks the server. Profiles are relative, so a
  scan only runs from where it was planned.
* `/move_energy` and `/move_gap` answer **409** while an acquisition owns the
  mono, including the idle gaps between its sweeps.
* Speeds outside 0.2-1.0 are **silently clamped** by the controller; the
  trigger period is whole milliseconds (60 Hz runs at 62.5 Hz). Validation
  reports the command that will actually run (`effective_command`).
* Above 80 Hz the Xspress3 is outside its tested rate.
* BL15-2: a scan range crossing an undulator harmonic change shows an
  intensity step.
* BL4-1, shared box: SPEC's `ct` uses the same cscan box. A scan waits for a
  running count (up to ~70 s) before taking the box, and refuses if TRIG is
  Enable with nothing of ours running (cscan_acq, or a dead run --
  `csct_release` in SPEC clears it). Readiness reports both.
* BL4-1, mono backlash: a sweep that starts by reversing direction loses
  ~0.0084 deg of drive slack, so it starts ~2 eV short at 7.1 keV. The energy
  axis is right (absev comes from the encoder); only the coverage is short.
* Table tracking: BL4-1 only (Tz1/Tz2 = MOTOR3/MOTOR4) and switched off for
  now -- continuous scans are mono-only, and the server refuses to enable it.
