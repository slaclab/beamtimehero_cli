# `science/` — the scientific and mathematical core

This is where the physics lives. If you are a scientist contributing to this
project, **this directory is your working area**, and you should not need to
read the toolbelt machinery to work in it.

## The one rule

> Everything under `science/` takes **numbers in** and returns **numbers out**.
> No file paths, no environment variables, no SPEC, no application database,
> no argparse.

One sanctioned exception: `xraydb`, whose tabulated edge energies and emission
lines come from a read-only table shipped inside the library. That is a constant
lookup, not I/O you have to arrange — treat it like a physical constant.

You can check this from a function's signature alone, which is the point. The
corollaries double as a routing rule:

| If your function… | …it belongs in |
|---|---|
| needs to know *where the data lives* | `beamtimehero_cli/spec_data/` |
| needs to know *what the agent asked for* | `beamtimehero_cli/tool_catalog/` |
| takes arrays and returns a dict of numbers | **here** |

For plotting specifically: a figure function that takes **arrays or a descriptor
dict** belongs in `science/plots/`; one that takes a **file name** belongs in
`spec_data/`. That split is now actually in place: the array-taking figures live
here, and the six that load a scan by file name (`plot_scan`,
`plot_averaged_scans_overlay`, `plot_scan_stack`,
`plot_first_half_vs_second_half`, `plot_running_average`,
`plot_feature_evolution`) stay in `spec_data/plotting.py`, which is the only
plotting module left outside this package.

## The layout

Organized by **technique, then pipeline stage** — the order you'd look something
up in, not the order the code was written.

```
science/
├── tables/     tabulated physics. data, not algorithms.
│   ├── edges.py            edge energies, families, edge suggestion
│   ├── edge_shifts.py      per-element eV/valence slopes, core-hole widths
│   ├── emission_lines.py   preferred Siegbahn line per edge
│   └── xrs_edges.py        XRS edge assignments
├── reduce/     detector counts → one clean spectrum. technique-agnostic.
│   ├── counters.py         which channel carries the signal (+ flat-channel warning)
│   ├── normalize.py        per-scan monitor / edge-step normalization
│   ├── reps.py             averaging and filtering across repeated scans
│   ├── deadtime.py         ICR-based non-paralyzable correction
│   ├── artifacts.py        glitch, saturation, self-absorption flags
│   └── policy.py           ★ glitch/saturation thresholds, rep-averaging cuts
├── statistics/ judging a stack of reps — converged? enough? heterogeneous?
│   ├── features.py         per-rep window scalar, running mean/SEM, F-statistic
│   ├── efficiency.py       CV, Poisson limit, optimal scan count
│   └── policy.py           ★ SEM/drift convergence and efficiency thresholds
├── xas/        XANES / HERFD
│   ├── normalize.py        area (Bugarin & Glatzel), MBACK, Athena-style pre/post
│   ├── e0.py               edge position, core-hole re-broadening
│   ├── fits.py             pseudo-Voigt pre-edge and white-line fits
│   ├── descriptors.py      the descriptor bundle the interpret_* tools consume
│   ├── compare.py          E0 registration, difference spectra, LCF
│   ├── interpret.py        oxidation state, coordination geometry, summary
│   └── policy.py           ★ the defaults and heuristics
├── exafs/      k-space
│   ├── kspace.py           E ↔ k, k-grid rebinning
│   ├── background.py       AUTOBK-lite spline removal
│   ├── fourier.py          windowed FT into R space, first-shell peak
│   └── policy.py           ★ k-weight, k range, window taper, R_bkg
├── xrs/        X-ray Raman, energy-loss axis
│   ├── calibrate.py        elastic line → loss axis, momentum transfer
│   ├── reduce.py           crystal summing, Compton background, area norm
│   ├── descriptors.py
│   └── interpret.py
├── fitting/    generic curve fits (only scan-similarity so far)
│   └── similarity.py     cosine similarity between scans (unpinned 0.95/0.99)
└── plots/      figures over arrays / descriptor dicts
    ├── xas.py              descriptor figure, alignment / difference / LCF
    ├── exafs.py            chi(k) extraction, |chi(R)|, k-space overlay
    ├── xrs.py              elastic fit, loss spectra, Compton subtraction
    └── scan.py             generic scan render, statistics trend, fig_to_base64
```

## You want to change… → go to

| You want to change… | Go to |
|---|---|
| an edge energy, core-hole width, or edge-shift slope | `tables/` |
| how edge *auto-detection* scores candidates (tolerances, bonuses) | `tables/edges.py` — see note below |
| which counter is picked, or how reps are averaged | `reduce/` |
| whether a scan series has converged, or how many reps are enough | `statistics/` |
| how sample-spot heterogeneity is judged | `statistics/features.py` |
| how μ(E) is normalized for HERFD metrics (area, MBACK, Athena pre/post) | `xas/normalize.py` |
| the naive per-scan edge-step normalization the generic scan tools use | `reduce/normalize.py` |
| how E₀ is found, or core-hole re-broadening | `xas/e0.py` |
| the pre-edge or white-line fit itself | `xas/fits.py` |
| what goes into the descriptor bundle | `xas/descriptors.py` |
| how an oxidation state or geometry verdict is reached | `xas/interpret.py` |
| a default window, k-weight, component count, or auto-detection rule | `<technique>/policy.py` |
| χ(k) extraction, background, or the Fourier transform | `exafs/` |
| the energy-loss axis, Compton background, or crystal summing | `xrs/` |
| scan-to-scan similarity | `fitting/similarity.py` |
| a knife-edge / aperture / emission-peak fit | `generic_data/fitter.py` |
| what an EXAFS k/R-space figure looks like | `plots/exafs.py` |
| what an XRS energy-loss figure looks like | `plots/xrs.py` |
| what a XANES descriptor / comparison figure looks like | `plots/xas.py` |
| a figure that loads a scan by **file name** | `spec_data/plotting.py` — not science |

## `policy.py` — where the defaults live

Each technique has a `policy.py` holding the scientific *choices* as distinct
from the computations: fit windows, how many white-line components an edge
family needs, how the absorber is guessed when the caller doesn't say, the
default k-weight, the minimum data a fit needs.

These used to sit inline in the tool handlers, several of them duplicated
across handlers (the default k-weight appeared five times). They are collected
here so changing a default is a one-line edit in an obvious file — and so each
choice can carry the citation that justifies it.

**If you are changing a number, it probably belongs in a `policy.py`.**
`xas/`, `exafs/`, `xrs/`, `reduce/` and `statistics/` each have one.
`fitting/` does not, though it should: `similarity.py` hard-codes the `0.99`
convergence target and the `0.95` outlier cut that decide its verdict, and its
own `CITATIONS` lists them as an unfilled gap.

Every constant in those five modules is pinned by a test, and changing one has
a required second step. The full contract — what is guarded, what is not, and
what you must update in the same commit — lives in
**[README.md, "Scientific defaults and how they are pinned"](../../../README.md#scientific-defaults-and-how-they-are-pinned)**.
Read it before you change a number; it is the one thing here worth leaving
`science/` for.

Edge auto-detection is *split*: `xas/policy.resolve_edge` decides
the explicit-vs-auto policy, but the scoring weights that pick the winner
(`_TOL_EV`, `_K_EDGE_BONUS`, `_COMMON_BONUS`, `_AMBIGUITY_MARGIN`) live in
`tables/edges.py` beside the scoring function. If a detection comes out wrong,
that is where to look.

## Conventions

**Counter and normalization are inputs, not inferences.** Before touching
anything that averages, compares or scores repeated scans, read
`beamtimehero ref counter-selection`. Which channel carries the signal and how
it is normalized must come from the caller and be echoed back in the output;
guessing returns a confident, wrong number instead of an error. `reduce/` is
where that convention is implemented.

**Citations.** Every module here that defines functions declares a
module-level `CITATIONS` dict — `tests/test_docgen_science.py` requires it, and
`CITATIONS = {}` is the valid answer for a module that implements no published
method. Where there is one, the dict maps *what it applies to* to the
reference:

```python
CITATIONS = {
    "Area normalization (the HERFD default)": AREA_NORM_CITATION,
    "Athena-style pre-edge/post-edge normalization": None,   # gap: needs a reference
}
```

A value of `None` means the method is implemented but not yet attributed.
Those are collected as **attribution gaps** on the generated science index, so
they read as a to-do list rather than an omission. If you add a method, add
its reference; if you recognise one of the gaps, fill it in.

**The generated index.** `python -m beamtimehero_cli.docgen_science` writes
`docs/science_index.html`: every function here with its signature, docstring,
citations, and — computed from a call graph — **which tools reach it**. Check
that before changing a function; it answers "what depends on this" without
tracing imports. It is generated from the source tree, so a new function
appears by existing — but the copy in `docs/` is a checked-in artifact, so
regenerate and commit it in the same change. `tests/test_docs_fresh.py`
compares it byte for byte.

**The defaults are pinned.** `tests/test_science_policy.py` guards every
constant in all five `policy.py` modules. Changing one fails that test by
design, and there is a required second step. See
[README.md](../../../README.md#scientific-defaults-and-how-they-are-pinned)
for the contract, including which defaults it does *not* cover.

**The boundary is enforced.** `tests/test_science_boundary.py` asserts the one
rule mechanically, per file: nothing here may import from the rest of
`beamtimehero_cli`, and nothing here may touch the environment, filesystem or
network. It reads the source with `ast`, so a function-local import or a
relative `from ...spec_data import x` is caught too. If it fails, move what
you needed into `science/` or take it as an argument.

**Provenance.** Anything that produces a number a scientist might quote also
reports how it was produced: which normalization, which fit window, which
baseline model, which calibration. See `xas/descriptors.py` for the pattern.

**Degrade on a bad fit; raise on bad data.** The two halves matter:

- A *fit* that fails returns a flag (`fit_ok`) and an unchanged spectrum rather
  than raising. Tools call these inside an agent loop, where an exception is an
  opaque failure but a flag is something the agent can report and work around.
  `xrs/calibrate.fit_elastic_line` degrading to argmax is the pattern.
- *Data* that cannot support the analysis at all raises `ValueError` — too few
  overlapping points, a missing counter, an impossible window. Handlers funnel
  these into one error path, so don't turn them into flags. See
  `xas/policy.check_overlap` and `exafs/policy.check_exafs_points`.

## Moved from

These modules moved into `science/` on 2026-09-04. The re-export shims that
kept the old paths importable were **removed on 2026-09-11**, once the last
consumer migrated — so an old path is now an `ImportError`, and this table is
how you find where something went:

| Old | New |
|---|---|
| `analysis/xas.py` | `science/reduce/{counters,normalize,reps,deadtime}.py`, `science/xas/compare.py` |
| `analysis/exafs.py` | `science/exafs/{kspace,background,fourier}.py` |
| `analysis/xrs.py` | `science/xrs/{calibrate,reduce}.py` |
| `analysis/render.py` | `science/plots/scan.py` |
| `interpretation/descriptors.py` | `science/xas/{e0,fits,descriptors}.py` |
| `interpretation/normalize.py` | `science/xas/normalize.py`, `science/tables/emission_lines.py` |
| `interpretation/{edges,calibrations,xrs_edges}.py` | `science/tables/` |
| `interpretation/quality.py` | `science/reduce/artifacts.py` |
| `interpretation/interpret.py` | `science/xas/interpret.py` |
| `interpretation/xrs_*.py` | `science/xrs/` |
| `interpretation/plotting.py` | `science/plots/xas.py` |
| `interpretation/calibration_store.py` | `beamtimehero_cli/calibration_store.py` (session state, not science) |
| `generic_data/lcf.py` | `science/xas/compare.py` |
| `generic_data/cosine_similarity.py` | `science/fitting/similarity.py` |
| `experiment_planning/scan_features.py` | `science/statistics/features.py` |
| `experiment_planning/scan_efficiency.py` | `science/statistics/efficiency.py` |
| `spec_data/exafs_plotting.py` | `science/plots/exafs.py` |
| `spec_data/xrs_plotting.py` | `science/plots/xrs.py` |
| `spec_data/plotting.py` (the 4 array/dict-taking figures) | `science/plots/{xas,scan}.py` |

There is no compatibility layer left: the new path is the only path.
