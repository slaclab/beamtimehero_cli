# Running CHERFD from an agent

CHERFD is continuous-scan HERFD at SSRL BL15-2: the mono sweeps
continuously under an FPGA motion profile while the FPGA fires detector
triggers at a fixed rate, and the undulator gap tracks the mono predictively.
One **acquisition run** is N sweeps (each optionally forward + reverse) saved
as one pickle set per sweep.

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
   one; `cherfd_validate_command` (limits, speed clamp, trigger quantization,
   harmonic changes); `cherfd_simulate_scan` (the production profile generator:
   authoritative refusal/duration); `cherfd_estimate_acquisition_time`.
2. **Pre-flight** -- `cherfd_check_readiness --command ...`. Blockers must be
   empty. Make sure SPEC has handed the Xspress3 over (`xtc 1` in SPEC; this
   cannot be read from here -- an all-zero `vortex` column is the symptom).
3. **Configure** -- `cherfd_set_data_path` (one file_root per sample/spot;
   roots can be reused but runs are then told apart by time).
4. **Acquire** -- `cherfd_start_scan` then `cherfd_wait_for_scan`. The start
   tool confirms from `/status` that the scan really started: the server
   answers `success: true` before the controller has accepted it.
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
  are refused until `cherfd_clear_stop`.
* Speeds outside 0.2-1.0 are **silently clamped** by the controller; the
  trigger period is whole milliseconds (60 Hz runs at 62.5 Hz). Validation
  reports the command that will actually run (`effective_command`).
* Above 80 Hz the Xspress3 is outside its tested rate.
* A scan range crossing an undulator harmonic change shows an intensity step.
* The crystal set is hardcoded to B (Si 311) in the controller.
* Table tracking hardware (MOTOR2/3) is not commissioned yet.
* The stop path's effect on the mono is not hardware-verified upstream
  (`/stop` writes `STOP=0`, the same value a move writes to *clear* stop).
  Treat `cherfd_stop_scan` as "stop triggers and sweeps", and confirm with
  `cherfd_get_status`.
