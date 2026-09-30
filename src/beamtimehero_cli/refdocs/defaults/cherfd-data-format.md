# cherfd sweep files, and what the tools do to them

## On disk

Per sweep, under `<CHERFD_DATA_DIR>/<file_dir>/` (`<N>` = sweep number,
`<dir>` = fwd|rev, `<ts>` = `YYYY-MM-DD_HHMMSS`):

| File | Holds |
|---|---|
| `scan_results_<root>_<N>_<dir>_<ts>_dataframe.pkl` | merged frames (pandas) -- **what the tools read** |
| `vortex_<root>_<N>_<dir>_<ts+~1s>.pkl` | Xspress3 `roi1`, `roi_double`, `vortex = roi1 + 2*roi_double` |
| `scan_results_<root>_<N>_<dir>_<ts>.pkl` | pickled `cherfd.ScanResults` (command, parameters, summary) |
| `scan_data_<root>_<N>_<dir>_<ts>.pkl` | raw FPGA frames before the detector merge |
| `partial/<file_dir>/partial_scan_results_...` | sweeps cut short by a stop |

Frame columns: `trig_1` (trigger end time, us; bit 31 = status frame),
`trig_2` (duration, us), `config_*`, `counter_*`, `adc_*` (offset-binary),
`encoder_1`, `motor_1`, `dio`, `timestamp` (CA time of the 0.2 s DMA chunk --
shared by ~20 frames), `absev` (energy from `encoder_1` and the calibration in
force at acquisition time), `gap` (older files: `gap_mm`; sparse, as-of merged),
`vortex`.

## What is wrong with the raw table, and the fix

| Problem | Fix (`science/cherfd/frames.py`) |
|---|---|
| Rows are not in time order within a DMA chunk | re-ordered by trigger number `round(trig_1 / period)` |
| Detector frames were pasted on by row index, so they are paired with the wrong trigger by up to +/-4 frames | detector frame k paired with trigger k (`align="trigger"`; `"row"` keeps the recorded pairing) |
| FPGA status frames are not filtered | dropped |
| Integer columns are `object` dtype | coerced to float |
| Trailing all-NaN row; first chunk may be missing | dropped / counted as missing triggers |
| Corrupt energies (e.g. 205917 eV) | dropped when > 20 eV outside the commanded range |
| `adc_*` offset-binary | minus 2**31 (flagged: not yet hardware-verified) |
| Unpickling ScanResults imports cherfd (pyepics, log files) | stub unpickler |

## Reduction

* Sweeps are rebinned on a shared grid (`bin_ev`, default 0.25 eV).
* With a normalizer the merged value is sum(signal)/sum(I0) over every frame
  of every sweep in the bin (Poisson error on the signal); without one it is the
  frame-weighted mean (scatter error). **No per-sweep min-max** -- the
  cherfd_claude viewer's `average_sweeps` does that and erases real intensity
  changes.
* **Forward/reverse offset.** On 2025-10_Sokaras Cu K data (0.4 speed, 100 Hz,
  10 random runs) reverse sweeps read features **0.076 +/- 0.012 eV lower** than
  forward ones -- **1.1 +/- 0.2 frames**, the same with either detector
  alignment. One frame is what a labelling offset predicts: the encoder is
  latched at the trigger *end* while the detector integrated the window before
  it, so forward sweeps read ~half a frame high and reverse ~half a frame low.
  That is a hypothesis consistent with the data, not a verified hardware fact,
  so the tools measure it (`cherfd_compare_directions`, also in frames) and
  apply it only when asked (`direction_correction_ev`, split symmetrically).
  If confirmed, the principled fix is labelling each frame with its window-mean
  energy, which would also remove it from single-direction data.
* **Shift estimation** smooths both spectra (sigma 0.3 eV) before the
  least-squares search; without that the estimate pixel-locks to multiples of
  the frame spacing (it did: an earlier -0.11 eV figure was that artefact).
* Signal/normalizer are experiment inputs. The default signal is `vortex` and
  every result echoes what was used; there is no default normalizer.
