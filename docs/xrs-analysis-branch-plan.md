# The XRS (X-ray Raman) analysis branch

**Status: Phases 0–2 implemented and tested; Phase 3 items are documented
extensions below.**

> **Module paths below are pre-`science/`.** The scientific core was gathered
> into `src/beamtimehero_cli/science/` after this was written, so read:
>
> | Written here | Now |
> |---|---|
> | `analysis/xrs.py` | `science/xrs/{calibrate,reduce}.py` |
> | `interpretation/xrs_edges.py` | `science/tables/xrs_edges.py` |
> | `interpretation/xrs_descriptors.py` | `science/xrs/descriptors.py` |
> | `interpretation/xrs_interpret.py` | `science/xrs/interpret.py` |
> | `spec_data/xrs_plotting.py` | `science/plots/xrs.py` |
>
> (Superseded 2026-09-11: the shims described here have been removed; the
> `science/` paths are now the only ones.) The old paths still import — they
> are re-export shims. The physics, the
> phase table, and the reasoning below are unaffected. **Scope:** the tools so the agent can process, average,
overlap, and *interpret* X-ray Raman (XRS / non-resonant inelastic X-ray
scattering, NRIXS) spectra — a technique our XAS/HERFD/XES tools handled
incorrectly by construction.

## What shipped

- **Phase 0 (counter/normalization override):** every multi-scan tool
  (`average_scans`, `analyze_convergence`, `analyze_efficiency`,
  `plot_scan_stack`, …) now takes an explicit `counter` and a `normalization`
  mode (`edge_step`/`divide_by_i0`/`raw`), threaded through the single
  chokepoint `spec_data.scans.get_normalized_scan_arrays`. Auto-selection stays
  as a fallback but echoes a `counter_warning` when it picks a flat dark channel
  (the `vortDT`-over-`vortDT2` trap). See `beamtimehero ref counter-selection`.
- **Phase 1 (processing):** `analysis/xrs.py` (pure math) + `spec_data/xrs_data.py`
  (loaders + elastic-line calibration store) + `spec_data/xrs_plotting.py`, driving
  the CLI tools `calibrate_energy_loss`, `build_loss_axis`, `average_xrs_scans`,
  `subtract_compton_background`, `normalize_xrs`, `overlay_xrs_spectra`,
  `sum_crystals`, `align_crystals`, `tag_crystal_q`.
- **Phase 2 (interpretation):** `interpretation/xrs_edges.py`,
  `xrs_descriptors.py`, `xrs_interpret.py`, driving `extract_xrs_descriptors`,
  `interpret_xrs_oxidation_state`, `interpret_q_dependence`,
  `compare_xrs_to_references`, `assess_xrs_quality`, `summarize_xrs_chemistry`.
- All XRS tools live on a dedicated **`xrs`** CLI branch (`beamtimehero xrs …`)
  and are exposed to the chemcatal chat agents via the `cc` profile.
- Tests: `tests/test_xrs.py` (pure math, interpretation, and an end-to-end pass
  through `execute_tool`).

The plan below is the original design; the four **open questions to confirm with
the beamline** at the end still stand (they don't block the implementation, but
the defaults — SPEC macro names, exact `vortDT` semantics, spectrometer config,
per-channel q — should be verified against the live beamline).

---

## Why XRS can't reuse the XAS path (the one thing to internalize)

XRS is **not an absorption measurement**. Hard X-rays (~10 keV) go in and out;
the analyzer is held at a fixed energy Ω and the monochromator Ω₀ is scanned, so
the abscissa is **energy loss ω = Ω₀ − Ω**, and the measured quantity is the
dynamic structure factor **S(q,ω)**, not an absorption coefficient. The
consequences that break our tools:

| | XAS / HERFD / XES (what we have) | XRS / NRIXS (what we need) |
|---|---|---|
| x-axis | incident/emission energy | **energy loss**, referenced to the elastic line |
| feature shape | **edge step** (+ EXAFS) | **weak bump on a large, sloping Compton background** — no step |
| normalization | edge-step (pre→0, post→1) | **area or f-sum/absolute** — edge-step is meaningless (no step) |
| scan alignment | `find_e0` (max derivative) | **elastic line** (max derivative sits on the Compton rise) |
| signal counter | brightest fluorescence channel | the channel that actually shows the **elastic line + edge**, not the brightest |
| extra knob | polarization | **momentum transfer q** (dipole at low q → multipole at high q) |

Every one of those rows is a place the current pipeline does the wrong thing
silently. That is exactly what corrupted the cathode-campaign XRS averages
(wrong counter `vortDT` over the real signal `vortDT2`, then edge-step
normalization of a channel with no edge).

Sources for the physics/pipeline below: Sahle et al. *J. Synchrotron Rad.* 22,
400 (2015) — the canonical methods paper; Sokaras et al. *Rev. Sci. Instrum.*
83, 043112 (2012) — the SSRL BL6-2 XRS end-station; XRStools (ESRF) — the
de-facto reduction package we mirror. Full ledger at the end.

---

## Architecture: mirror the existing split, don't fork it

The codebase already separates pure-math processing from pure-science
interpretation. XRS slots into the same seams:

- **`analysis/xrs.py`** — new, parallel to `analysis/xas.py`. Pure math: arrays
  in, arrays/dicts out, no I/O. Elastic-line calibration, per-crystal alignment,
  crystal summing + rejection, Compton subtraction, XRS normalization.
- **`spec_data/scans.py`** — add `get_xrs_spectrum_arrays(...)` alongside
  `get_normalized_scan_arrays(...)`. Crucially it takes an **explicit `counter`**
  and an **XRS normalization mode**, and aligns reps on the **elastic line**, not
  `find_e0`. (This is why Phase 0's chokepoint fix matters: the plumbing to pass
  `counter`/normalization through is shared.)
- **`interpretation/`** — add XRS descriptors + verdicts next to the existing
  CAT-10 XANES ones (`descriptors.py`, `interpret.py`, `quality.py`,
  `calibrations.py`). Same output contract as the XANES interpreters.
- **CLI branch** — expose the XRS tools under a dedicated surface so the agent
  sees one coherent set. Either a new `xrs` tree category in
  `tool_catalog/categorize.py` (parallel to `spec-file`) or an `xrs-analyst`
  profile (`refdocs/defaults/profiles.md` pattern). A profile is lighter and
  matches how per-agent surfaces are already curated.
- **chemcatal side** — if the portal viewer / notebook needs XRS too, add an
  `xrs_core/` parallel to `xas_core/`; keep larch `pre_edge` strictly on the
  XAS/HERFD/XES paths.

**Calibration to *our* beamline:** the cathode-campaign data (`gscan energy`
data scans, `ascan mono` elastic scans, a `gscan` Compton scan, signal on
`vortDT2`) reads as a **Vortex SDD / few-ROI** setup, not the 40+-crystal ID20
spectrometer. So the multi-crystal alignment/rejection machinery is real but
**secondary** for us — the immediate, load-bearing needs are: correct counter,
elastic-line loss axis, Compton subtraction, elastic-aligned averaging, and
area normalization. Build those first; make the per-crystal layer scale in when
the full analyzer array is used.

---

## Phase 0 — unblock the correct channel (prerequisite, already scoped)

The `counter-selection` fix: thread an explicit `counter` and a `normalization`
mode through `get_normalized_scan_arrays`, expose them on the nine multi-scan
tools, echo the chosen counter+reason, and warn on the flat-high-offset-channel
signature. With just this, an operator can already produce a *correct* average
on `vortDT2` — even before the XRS-specific pipeline exists. Do this first.

---

## Phase 1 — XRS processing tools ("process, average, overlap")

Ordered as the reduction pipeline runs. Each is a CLI tool over `analysis/xrs.py`.

1. **`calibrate_energy_loss`** — fit the elastic (Rayleigh) line from an
   `ascan mono` elastic scan (per crystal/ROI when present): center-of-mass →
   **ω = 0**, FWHM → **energy resolution**. Returns the loss-axis mapping and the
   instrumental resolution. This replaces `find_e0` as the alignment anchor.
2. **`build_loss_axis`** — convert a scan's monochromator-energy axis to
   **energy loss** using the calibration from (1). Every XRS tool operates on the
   loss axis, not `scan_energy`.
3. **`align_crystals`** *(multi-analyzer; secondary for us)* — shift/interpolate
   each crystal/ROI onto a common loss grid from its own elastic COM (each
   channel has a slightly different effective calibration).
4. **`sum_crystals`** *(multi-analyzer; secondary)* — co-add aligned channels
   into the total spectrum, with **outlier rejection**: flag channels deviating
   from the array median, low-SNR channels, and channels with poor elastic-line
   quality; report which were dropped and why (no silent truncation).
5. **`subtract_compton_background`** — the replacement for the XAS pre/post
   polynomial. Fit and subtract the broad Compton/valence background under the
   edge: **Pearson VII** (obscured-region interpolation), **linear**, or
   **constant** modes; leave a hook for the ab-initio Hartree-Fock /
   f-sum-constrained background later. Returns the isolated edge + the background
   model used.
6. **`average_xrs_scans`** — average repeated short scans **after** elastic-line
   re-referencing (drift correction), with **Poisson error propagation** and
   fresh-spot / radiation-damage awareness (reuse the existing per-spot grouping).
   Explicitly *not* `find_e0`-aligned, *not* edge-step normalized.
7. **`normalize_xrs`** — **area** (integral over a loss window) or **f-sum /
   absolute** (post-edge matched to the atomic core Compton cross section).
   Never edge-step.
8. **`overlay_xrs_spectra`** — the "overlap raman spectra" ask: overlay multiple
   reduced XRS spectra (samples, states of charge, q-bins) on the loss axis with
   consistent normalization and optional difference traces. Parallels
   `plot_averaged_scans` but on the XRS pipeline.
9. **`tag_crystal_q` / q-resolved products** — tag each crystal/ROI with its 2θ
   and computed **q = (2E₁/ℏc)·sin θ**; emit both a **sum-all** product (max
   signal) and a **bin-by-q** product (q-resolved) from the same reduced data.

## Phase 2 — XRS interpretation tools ("what is the takeaway")

Mirror the CAT-10 XANES interpreters; same verdict/output contract.

1. **`extract_xrs_descriptors`** — measurable features on the reduced,
   background-subtracted edge: **edge onset / inflection** (on the loss axis),
   **pre-edge peak position + integrated area**, **white-line height/position**,
   total **edge area**. Arrays in, numbers out.
2. **`interpret_xrs_oxidation_state`** — edge/main-peak shift vs reference
   couples; for **O K-edge**, pre-edge intensity → **TM 3d–O 2p covalency** and a
   growing low-loss pre-edge → **oxygen redox / O 2p holes** (the cathode
   workhorse signal); for metal **L-edges**, the **L₃/L₂ branching ratio** and
   the 2⁺/3⁺ feature split → oxidation state.
3. **`interpret_q_dependence`** — compare a feature across q-bins: **low q =
   dipole (XANES-like, s→p)**, **high q = monopole/quadrupole** turning on. Assign
   feature symmetry, flag dipole-forbidden pre-edges. This is the interpretation
   move XAS *cannot* make and is a headline capability of the XRS instrument.
4. **`compare_xrs_to_references` / LCF** — fingerprint and linear-combination-fit
   against a reference library (valid against XANES references in the **low-q
   dipole** regime); reuse larch `lincombo_fit` downstream where useful.
5. **`assess_xrs_quality`** — SNR **on the edge bump specifically** (not the whole
   spectrum, which is dominated by the Compton background — the direct analogue of
   the "score the feature window, not the plateau" rule already enforced for XAS),
   elastic-line resolution, and cross-crystal agreement.
6. **`summarize_xrs_chemistry`** — the takeaway composer: given the reduced
   spectrum + descriptors + q-dependence + references, state oxidation
   state/covalency/speciation with the evidence and caveats. Parallel to
   `summarize_sample_chemistry`.

Edge reference energies to seed the calibration/interpretation tables: Li K ~55,
B K ~188, C K ~284, N K ~400, O K ~530, F K ~685 eV (plus Si/P/S L-edges,
3d-metal L/M). C K-edge: π* ~285 eV (sp²) vs σ* ~292 eV (sp³); carbonate π* ~290.

## Phase 3 — polish

Ab-initio HF Compton background + true absolute (f-sum) normalization; OCEAN /
FEFF9 q-dependent XRS simulation hooks for reference comparison; NeXus/HDF5
export (no XRS-specific NeXus class exists yet — map onto `NXxas`-like
energy-vs-intensity conventions).

---

## Reuse vs. build-new

- **Reuse:** rep grouping by spot (`group_scans_by_spot`), the convergence/
  efficiency *statistics* (they're counter-agnostic once fed the right array and
  a feature window), the plotting scaffolding, larch for downstream LCF/PCA.
- **Build new (XRS-specific):** elastic-line calibration, loss-axis construction,
  per-crystal alignment + rejection, Compton subtraction, XRS normalization,
  q tagging/binning, and all six interpreters. None may route through
  `edge_step_normalize`.

## Open questions to confirm with the beamline before coding

1. **SPEC macro names** for the energy-loss ("gscan"-style) scan, and whether the
   analyzer moves during a scan or is truly fixed. No published SSRL macro docs
   were found; classic SPEC ships `ascan`/`dscan`/`mesh` — the energy-loss scan is
   a local macro.
2. **`vortDT` vs `vortDT2` semantics** — confirmed empirically that `vortDT2` is
   signal and `vortDT` is a flat/dark channel on the cathode data, but confirm the
   Vortex-SDD/ROI channel mapping against beamline docs so the counter default and
   the flat-channel guardrail are keyed on the right thing.
3. **Spectrometer configuration in use** — single/few-ROI SDD vs the full
   multi-analyzer array. Decides how much of Phase 1's per-crystal layer is
   front-line vs. deferred.
4. **q values / 2θ per channel** — needed for the q-tagging and q-resolved
   interpretation tools.

## Source ledger (key)

- Sahle, Mirone, Niskanen, Inkinen, Krisch, Huotari (2015), *J. Synchrotron Rad.*
  22, 400 — planning/performing/analyzing XRS; background subtraction, per-crystal
  handling, q-regimes, f-sum normalization. DOI 10.1107/S1600577514027581.
- Sokaras et al. (2012), *Rev. Sci. Instrum.* 83, 043112 — SSRL BL6-2 XRS
  end-station (40+14 crystals, Si(440)/Si(660), inverse scanning mode).
- Huotari et al. (2017), *J. Synchrotron Rad.* 24, 521 — ID20 large-solid-angle
  spectrometer (72 analyzers).
- XRStools (ESRF): gitlab.esrf.fr/ixstools/xrstools; ftp.esrf.fr/scisoft/XRStools
  — the reduction pipeline this plan mirrors.
- Mizuno & Ohmura (1967), *J. Phys. Soc. Jpn.* 22, 445 — low-q XRS↔dipole-XAS
  equivalence.
- Jonas et al. (2025), *MRS Advances*, DOI 10.1557/s43580-025-01397-3 — operando
  XRS Ni 2p of NMC cathodes (bulk vs surface oxidation). *(Which SSRL beamline is
  unresolved in the sources — verify.)*
- Rajh et al. (2022), *J. Phys. Chem. C* 126, 5435 — operando metal-organic
  battery O K-edge; elastic-line re-referencing per point + summing across points.
- O K-edge oxygen-redox review: *Chem. Rev.* (2019), DOI 10.1021/acs.chemrev.9b00439.

*Note:* the zeolite-coke Devaraj et al. (2016) study is soft-XAS, **not** XRS —
do not cite it as an XRS example. A "Nagle-Cocco" XRS battery paper could not be
verified; use Jonas et al. and Rajh et al. as the operando-cathode references.
