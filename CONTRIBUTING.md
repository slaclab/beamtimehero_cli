# Contributing to beamtimehero_cli

This repo is a toolbelt: a flat catalog of ~130 tools that LLM agents call,
plus the science those tools run. Several applications depend on it, which
determines what is safe to change and what needs a conversation first.

## If you are contributing science

**Work in [`src/beamtimehero_cli/science/`](src/beamtimehero_cli/science/README.md).**
That directory has its own README with the layout, the one rule it follows, and
a "you want to change X → go to Y" table. Start there.

One thing to read alongside it: **`beamtimehero ref counter-selection`**. Which
detector counter is the signal, and how it gets normalized, are experiment
inputs the pipeline must not infer on its own — getting either wrong returns a
confident, wrong number rather than an error, and it has already cost a real
experiment. That refdoc is binding on every multi-scan tool
(`average_scans`, `analyze_convergence`, `analyze_efficiency`, and the rest).

Everything inside `science/` is fair game — normalization, fits, descriptors,
interpretation logic, the tabulated physics, the defaults in each
`policy.py`. That is the part of this project that should keep changing, and
you do not need permission to change it.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e '.[dev]'
python -m pytest                          # 938 tests, ~30s
python -m pytest tests/test_interpretation.py -q     # the science tests
ruff check src tests                      # CI gates on this too, before pytest
```

Python 3.11 or newer for development — that is what CI gates on. `pyproject.toml`
declares a 3.9 floor, but nothing has ever exercised it; CI runs a 3.9 job that
reports without blocking, so if you see it red the fix is to raise the declared
floor, not to chase it. Two optional extras exist and are not in `dev`: install `.[slack]`
if you are touching `notify/slack.py`, `.[postgres]` for
`spec_data/postgres_backend.py`. Both are imported lazily, so a bare install
runs fine without them.

Run every command in this file from the repository root — the `docgen` entry
points read and write paths relative to the working directory. `open` is
macOS; substitute `xdg-open` or your browser elsewhere.

The science tests build synthetic spectra with analytically known ground truth
(an erf edge + Gaussian pre-edge + Gaussian white line), so every descriptor
has a right answer to check against. By area:

| Area | Tests |
|---|---|
| XAS descriptors & interpretation | `tests/test_interpretation.py`, `tests/test_interpretation_tools.py` |
| Reduction (counters, reps, normalization) | `tests/test_analysis_xas.py` |
| EXAFS | `tests/test_analysis_exafs.py` |
| XRS | `tests/test_xrs.py` |
| Tool-level science | `tests/test_twocol_and_analysis_leaves.py` |
| Scientific defaults (pinned values) | `tests/test_science_policy.py` |
| The `science/` boundary itself | `tests/test_science_boundary.py` |

`tests/test_interpretation.py` is the clearest model for the synthetic-spectrum
pattern. Every test imports from `beamtimehero_cli.science.*` directly — the
old `interpretation` / `analysis` / `generic_data` re-export shims were removed
on 2026-09-11, so there is one import path per function and no compatibility
layer to keep in mind.

The scientific defaults are pinned, and changing one has a required second
step. That contract — which defaults are guarded, which are not, and what you
must update in the same commit — is in **[README.md, "Scientific defaults and
how they are pinned"](README.md#scientific-defaults-and-how-they-are-pinned)**.
Read it before you change a number.

`tests/test_science_boundary.py` asserts the `science/` boundary per file,
both halves: it imports nothing else from `beamtimehero_cli`, and reads no
environment, filesystem or network. If you need data loaded, take it as an
argument and let `spec_data/` load it.

To see the whole science surface at once — every function with its signature,
what calls it, and what it cites:

```bash
open docs/science_index.html                    # view
python -m beamtimehero_cli.docgen_science       # regenerate
```

The **"used by"** column on that page is the one worth knowing about: it is
computed from a call graph, so before changing a function you can see which
tools depend on it without tracing imports by hand.

If you add or change a published method, record the reference in that module's
`CITATIONS` dict. The index collects those into a bibliography and lists the
entries still marked `None` as attribution gaps; `docgen_science` prints the
running count when it regenerates.

## What to raise rather than edit

Three places define what the *agents* see. Changing them changes what several
separate applications see, so open an issue
([slaclab/beamtimehero_cli/issues](https://github.com/slaclab/beamtimehero_cli/issues))
or ask a maintainer first:

| File | Why |
|---|---|
| `tool_catalog/definitions.py` | the JSON schema for every tool — names, parameters, defaults |
| `tool_catalog/categorize.py` | which CLI branch each tool sits on |
| `cli/` | the parser and the agent profiles built over the catalog |

Concretely, these need a heads-up: **renaming a tool, adding or renaming a
parameter, changing a parameter's type, or moving a tool to a different
branch.** Adding a brand-new tool is fine — it is additive.

Changing what a tool *computes* is not on this list. That is science, and it
belongs in `science/`.

## Adding a tool

Additive, so it needs no permission — but it does touch two of the three files
above, and there are five steps rather than the obvious two:

1. **`tool_catalog/lineage.py`** — a `TOOL_LINEAGE` entry. Eight fields,
   listed in `LINEAGE_REQUIRED_KEYS`, and all eight must be present.
   `tests/test_tool_catalog_wiring.py` additionally requires four of them to be
   non-empty: `long_description`, `python_func` (the call chain, so an operator
   can trace a call to its implementation), `output` and `source_detail`, plus
   `source` from a fixed enum. Three carry a legal empty value: `depends_on`
   may be `[]`, `spec_command` may be `None` if the tool never touches SPEC,
   and `mutates` is a bool where `False` is the common case.

   **`mutates` is the one to get right.** It means "issues a SPEC command
   registered as an action, so it requires a `--justification` and is
   written to the action log before dispatch" — and nothing broader.
   Writing a file, a row or a Slack message is not mutating; only hardware
   is. It is the sole input to the `spec-write` classification and to every
   consumer's write filter, so a wrong value is a safety-class error, not a
   documentation error. `tests/test_mutates.py` checks it against what your
   handler actually passes to `audited_call`, so a mismatch fails the suite
   rather than shipping.

   One optional ninth field, `spec_commands`, applies only if your handler
   picks its SPEC command at run time from an argument (today: `set_gain`,
   `post_scan_move`). List the command names it may issue; the static check
   cannot read them off the AST and will fail without it.

   This entry feeds `docs/tool_catalog.html` *and* the classification rules
   in `categorize.py`, so a tool without one is invisible on the catalog
   page and lands in whatever branch the default rule picks.
2. **`tool_catalog/definitions.py`** — the JSON schema the agent sees. Read
   every scientific default from the relevant `policy.py` rather than writing
   the literal (see `_exafs_policy.DEFAULT_KMIN` in the `fourier_transform_chi`
   entry); `tests/test_science_policy.py` asserts the schema and the science
   function agree, and a literal that merely *matches* fails it.
3. **`tool_catalog/tools_core.py`** — a handler `t_<name>(arguments)` returning
   `(text, images_b64)`. Register it in `_HANDLERS`, keyed by the bare tool
   name. To give the *same* leaf name a different handler on another branch,
   register that one in `_BRANCH_HANDLERS` instead, keyed by the full tree path
   — `("s3df", "list_scans")`, or `("s3df", "psql", "execute_readonly_sql")` for
   a nested branch. `_build_dispatch()` consults `_BRANCH_HANDLERS` first, so it
   wins over the flat entry. Keep it thin, per the layering rule below:
   unpack, ask `spec_data` for arrays, call one science function, serialise.
   `ValueError` from `science/` is the one error path — let it propagate to
   the handler's JSON error envelope rather than catching it deeper.
4. **`tool_catalog/categorize.py`** — only if the tool needs a branch the
   precedence rules would not give it. Prefer a `"tree"` field on the
   definition over an entry in `CATEGORY_OVERRIDES`. The rules, first match
   wins: explicit `tree` → `CATEGORY_OVERRIDES` → `source == "autonomy_db"`
   → `mutates` → `spec_command is not None` → `tool`.

   Note what is *not* on that list: the JSON schema's `required`. A
   `--justification` flag no longer classifies anything — `mutates` does.
   Adding the flag to a read tool's schema will not move it to
   `spec-write`, and removing it from a write tool's schema will not move
   it off (it will fail `tests/test_mutates.py` instead, which is the
   point).
5. **Regenerate both pages and commit them.** `tests/test_docs_fresh.py`
   byte-compares each against its generator:

   ```bash
   python -m beamtimehero_cli.docgen           # docs/tool_catalog.html
   python -m beamtimehero_cli.docgen_science   # docs/science_index.html
   ```

   The second is easy to forget and just as easy to trip over: if your handler
   reaches any `science/` function, that function's "used by" column changes.

`tests/test_tool_catalog_wiring.py` checks all of this: every definition has a
handler and a complete lineage entry, every `source` is a documented enum
value, and every `depends_on` names a tool that actually exists. A half-wired
tool fails the suite rather than returning "Unknown tool" at runtime.

### Adding a tool from another repository

The five steps above are for a tool that belongs in this catalog. A tool
that belongs to *one* consuming application should not be added here at
all — register it from there:

```python
from beamtimehero_cli.tool_catalog import register_tools

register_tools(
    definitions=[MY_TOOL_DEF],          # appended to TOOL_DEFINITIONS
    lineage={"my_tool": {...}},         # validated against LINEAGE_REQUIRED_KEYS
    handlers={"my_tool": t_my_tool},    # str key -> _HANDLERS
)                                       # tuple key -> _BRANCH_HANDLERS
```

Steps 1-4 above still apply in full; step 5 belongs to whoever owns the
page. `register_lineage` enforces the lineage half at the call itself:
one `ValueError` listing every problem across every entry, rather than a
tool that quietly lands on the wrong branch because `mutates` was
missing.

Every registry is updated **in place**. That is load-bearing:
`categorize.py` binds `TOOL_LINEAGE` at import and `executor.py` reads
`tools_core.DISPATCH`, so rebinding a module attribute is invisible to
half the readers — which is why consumers used to monkey-patch library
modules instead. Do not reassign `TOOL_LINEAGE`, `TOOL_DEFINITIONS` or
`DISPATCH`; call the register functions.

Import from `beamtimehero_cli.cli.api` (parser helpers) and
`beamtimehero_cli.cli.trees` (branch names, imports nothing) rather than
from `cli.__main__`. Anything in `__main__` and not re-exported by
`cli.api` is internal and may move.

## Commits

Commit subjects follow `area: lowercase imperative summary` — `science:`,
`tests:`, `docs:`, `spec_data:`, `exafs:` — with a body explaining why, not
what. Two content rules matter more than the format:

- Changing a `policy.py` constant means updating its pinned value in
  `tests/test_science_policy.py` **in the same commit**. That diff is the
  record of which physics default moved and when.
- Changing a default that *isn't* pinned — the inline ones in
  `exafs/fourier.py`, `xas/normalize.py`, `xrs/reduce.py` and
  `fitting/similarity.py` — means saying so in the commit message yourself,
  since no test will say it for you. (`reduce/` and `statistics/` *are* pinned;
  they go in the bullet above.)

## Layering

```
tool_catalog/     JSON schema in, JSON + base64 PNG out. what the agent sees.
      ↑
spec_data/        backends: SPEC files, Postgres, SSRL collector. knows where data lives.
spec_control/     SPEC transports (tcp / screen / sandbox / mock).
      ↑
science/          arrays in, dicts out. no I/O, no env, no SPEC.
```

A tool handler in `tool_catalog/tools_core.py` should unpack the argument dict,
ask `spec_data` for arrays, call one science function, and serialise the
result. If you find yourself writing a scientific decision or a numpy
expression in a handler, it wants to be a named function in `science/`
instead — that is the boundary this repo is organized around.

## Practical notes

- **SPEC is mocked by default.** `SPEC_MOCK` defaults to `1`; nothing touches a
  beamline unless you set it to `0`.
- **Every mutating tool requires `--justification`** and writes to a SQLite
  audit trail. That is deliberate; don't route around it.
- **Both HTML pages under `docs/` are generated, not hand-written.**
  `python -m beamtimehero_cli.docgen` writes `docs/tool_catalog.html`;
  `python -m beamtimehero_cli.docgen_science` writes `docs/science_index.html`.
  Regenerate and commit them rather than editing the HTML —
  `tests/test_docs_fresh.py` compares both byte for byte.
- **The plotting split is done.** Figures that take arrays or a descriptor
  dict live in `science/plots/` (`exafs.py`, `xrs.py`, `xas.py`, `scan.py`).
  The six that load a scan by file name stay in `spec_data/plotting.py`. If
  your change is about what a plot looks like, `science/plots/` is almost
  certainly the place.

- **Actions must be SHA-pinned.** `slaclab` enforces `sha_pinning_required`, so
  every `uses:` in `.github/workflows/` is a full 40-character commit SHA with
  the version in a trailing comment. First-party `actions/*` get no exemption.
  A tag or branch ref fails the whole run at `Set up job` before any step
  executes, and it presents as a red run with an *empty step list* — which
  reads like infrastructure flakiness rather than policy, so it is worth
  recognising. To bump an action, look up the commit SHA for the tag you want;
  do not put the tag back.

## Background

`docs/architecture-review.html` is the analysis this layout came out of: why the
science was hard to find, the full destination map, and a status section
recording where the implementation diverged from the plan. Read it if you want
the reasoning rather than the rules.

`docs/xrs-analysis-branch-plan.md` is the domain background for the XRS branch.
Its "why XRS can't reuse the XAS path" table is worth reading before you touch
anything under `science/xrs/` — the XAS defaults are not merely suboptimal on
the energy-loss axis, they are wrong by construction. Its module paths predate
the `science/` move; the header maps them.
