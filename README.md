# beamtimehero_cli

Generic command-line interface for the SSRL BL15-2 beamline.

Provides:

- **SPEC injection** — motor moves, scans, macro execution against a SPEC server (TCP, GNU screen, or sandbox/mock transports).
- **Scan data reads** — direct silx-based SPEC file parsing, scan analysis, plotting.
- **Spectroscopy analysis** — XAS/HERFD descriptors and interpretation, X-ray Raman (energy-loss) reduction, EXAFS chi(k)/Fourier-transform products.
- **Deployment backends** — the same scan-read surface served from S3DF Postgres + pickled scan data; Slack messaging.
- **Log reads** — beamline control log parsing, search.
- **Action logging** — every command writes to a local SQLite audit trail.
- **Reference docs** — `beamtimehero ref <name>` to fetch bundled procedure docs.

This is the generic CLI surface. It does not include orchestrator or
agent-harness concepts — those live in consuming projects.

## Install

Not on PyPI — install from a clone, into a virtualenv:

```bash
git clone https://github.com/slaclab/beamtimehero_cli
cd beamtimehero_cli
python3 -m venv venv && source venv/bin/activate
pip install -e .
```

Python 3.11 or newer. `pyproject.toml` declares a 3.9 floor, but nothing
exercises it and CI gates on 3.11/3.13 — on an older interpreter the
scientific stack (silx, lmfit, xraydb) may not resolve at all.

Commands below assume the repository root as the working directory.

## Quick start

Every command here works with no configuration:

```bash
beamtimehero --help                                   # the command tree
beamtimehero ref --list                               # bundled reference docs
beamtimehero catalog --names-only | head              # the tool names
SPEC_MOCK=1 beamtimehero spec-read get-beam-status    # a real answer, mock backend
```

`SPEC_MOCK` defaults to `1`, so nothing reaches a beamline until you set it to
`0`. Tools that read scan files need `BL_SCAN_DIR` pointed at a scan root —
there is no bundled sample data, and without it they report that no directory
is configured rather than returning an empty result:

```bash
BL_SCAN_DIR=/path/to/scans beamtimehero spec-file list-scans --limit 5
```

## Driving this from an agent

This CLI exists to be called by an LLM agent, so that path is documented
first-class: **[`beamtimehero ref agent-integration`](src/beamtimehero_cli/refdocs/defaults/agent-integration.md)**.
The short version — two modes:

**Progressive discovery** (the default). Give the agent one tool that runs
`beamtimehero <args>` and let it explore with `--help`. Right for a general
coding agent or anything with shell access.

**Full schema registration.** Export every tool schema and register them up
front:

```bash
beamtimehero catalog                        # all tools, JSON-schema form
beamtimehero catalog --tree exafs           # one branch
beamtimehero catalog --profile bl-aligner   # one profile's surface
```

Or in-process:

```python
from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS, execute_tool
text, images_b64 = execute_tool(("spec-file",), "list_scans", {"limit": 5})
```

Four properties make this safe to hand a model — SPEC mocked by default,
`--justification` required on every mutation, a SQLite audit log, and JSON
argument errors. The refdoc states each one precisely, including where the
last of them stops holding, and carries a Claude Code allowlist.

## CLI surface

Eleven top-level trees, each with its own leaves. `beamtimehero --help` prints
them with one-line descriptions, and `--help` works at any depth;
`beamtimehero ref getting-started` is the same list as a page you can hand to
someone. Agent profiles — curated alias views over the catalog, e.g.
`bl-aligner` — are listed with `beamtimehero --list-profiles`.

## Tool catalog (human-readable)

The nested `--help` surface is aimed at LLM agents. For humans there is a
generated one-page catalog — the full CLI tree plus an A–Z list of every tool
with descriptions, parameters, and backend lineage:

```bash
open docs/tool_catalog.html                 # view (source checkout only)
python -m beamtimehero_cli.docgen           # regenerate after catalog changes
```

That page is not shipped in the wheel, so from an installed package use
`beamtimehero catalog` instead — same content, JSON rather than HTML.

## Science index (for contributors)

The scientific core lives in `src/beamtimehero_cli/science/`, organized by
technique. Its own [README](src/beamtimehero_cli/science/README.md) has the
layout and the one rule it follows. For a generated index of every science
function — signature, what it does, **which tools reach it**, and what it
cites:

```bash
open docs/science_index.html                    # view
python -m beamtimehero_cli.docgen_science       # regenerate
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before your first change, and
`docs/architecture-review.html` for why the layout is the way it is.

## Scientific defaults and how they are pinned

The scientific *choices* — fit windows, k-weight, glitch and convergence
thresholds, how an absorber is guessed — live in a `policy.py` per technique,
separate from the computations that use them. Five modules have one: `xas/`,
`exafs/`, `xrs/`, `reduce/` and `statistics/`.

Every constant in those five is pinned by `tests/test_science_policy.py`, which
also asserts that the science functions and the agent-facing JSON schema read
*from* policy rather than from a literal that merely agrees with it. Three
consequences, and they are the point rather than an obstacle:

- **Changing a constant fails the test.** Update its expected value in the same
  commit. That diff is the record of which physics default moved, and when.
- **Adding a new policy constant fails the test**, with a message naming it.
  Pin it.
- **Adding a sixth `policy.py` fails the test.** Discovery globs
  `science/*/policy.py`, but the suite also asserts the exact set of modules,
  so add yours to that set as well as pinning its constants.

What this does **not** cover: numeric defaults that sit inline in a technique
module instead of its `policy.py`. Changing one leaves the suite green, so say
so in the commit message yourself. As of now that is the FT grid and
first-shell window in `exafs/fourier.py`, the MBACK polynomial order and gap
widths in `xas/normalize.py`, the outlier cuts in `xrs/reduce.py`, and the
`0.95`/`0.99` similarity thresholds in `fitting/similarity.py`. None is less
material than the pinned ones — if you find yourself editing one, promoting it
into a `policy.py` is the better move.

## Configuration

Everything resolves from environment variables.
**[`config.example.yaml`](config.example.yaml)** is the authoritative list of
them — all ~36, grouped by what you are trying to do (off-beamline use,
beamline data on disk, where this package writes, a live SPEC session, station
overrides, the S3DF Postgres deployment, Slack, LLM log-checking), each with
its default and what it is for.

Point the CLI at a copy and it applies them:

```bash
cp config.example.yaml config.yaml    # edit, then
export BEAMTIMEHERO_CONFIG=$PWD/config.yaml
```

Anything already exported wins over the file, so a checked-in baseline plus
per-host overrides works. A `.env` file is also loaded, but it is resolved
relative to the installed package rather than your shell's working directory —
in a source checkout that means `<repo>/.env`, and a `.env` sitting wherever
you happened to run the command is **not** picked up. Use
`BEAMTIMEHERO_CONFIG` or plain `export` if you need per-directory settings.
`tests/test_config_surface.py` asserts every variable the code reads appears
in `config.example.yaml`, so that list cannot drift.

Rather than restate them here, read the file — it is grouped by task, so the
three or four you need for off-beamline use sit together at the top.
`SPEC_MOCK` is the one to know: it defaults to `1`, and **only the exact string
`1` keeps the mock on.** Any other value routes to a real beamline, so if you
set it in YAML, quote it (`SPEC_MOCK: "1"`) — an unquoted `true` becomes the
string `"True"` and goes live.

## Extending the CLI

Consumers can compose their own subtrees on top of the upstream parser instead
of forking it. Import the helpers from `beamtimehero_cli.cli.api`, which
re-exports them from `cli.__main__` under a name that says they are the
contract. Branch names live in `beamtimehero_cli.cli.trees`
(`CANONICAL_TREES`, `RESERVED_TOP_LEVEL`, `TREE_HELPS`), which imports
nothing — so a name check costs no import of `config`.

| Name | Purpose |
|---|---|
| `build_parser()` | Build the default top-level parser (all canonical trees plus registered profiles). |
| `build_ref_subtree(subs)` | Mount only the `ref` subtree on an existing `_SubParsersAction`. |
| `build_catalog_subtrees(subs, tool_defs, *, agent_role=None, trees=None)` | Mount the catalog subtrees (`tool`, `db`, `spec-read`, `spec-write`, `spec-file`, `xrs`, `exafs`, `s3df`, `slack`, …) from a tool-definitions list (filtered or unfiltered). Returns `{branch path: subparsers action}`. `agent_role` stamps `_agent_role` on every leaf; `trees` limits which empty branches are pre-created. Both default to the unrestricted behaviour. |
| `categorize(tool_def)` | Data-driven tree path for a tool def (e.g. `("spec-file",)`, `("s3df", "psql")`). |
| `add_arg(parser, key, prop, required)` | JSON-schema property → argparse flag. |
| `ToolParser` | `ArgumentParser` subclass that emits `{"ok": false, ...}` JSON on parse errors. |
| `run_ref(args)` | Dispatch a `ref` invocation. |
| `run_tool_leaf(args, *, executor=None)` | Dispatch a catalog-leaf invocation, optionally through your own executor. |
| `dispatch(parser, args, *, executor=None)` | Top-level dispatcher (delegates to `run_ref` / `run_tool_leaf`). |
| `TeeStdout` | Stdout wrapper that captures a bounded tail (used by `main()` for CLI logging). |
| `run_with(parser_builder, dispatcher, argv=None, *, known_trees=None)` | Wrap a custom parser-builder + dispatcher with the same stdout-tee tail capture and `record_cli_invocation` CLI logging that `main()` provides. |
| `main(argv=None, *, executor=None)` | Full standalone entry point — same as the `beamtimehero` console-script. |

To add tools rather than re-arrange them, call
`tool_catalog.register_tools(definitions=..., lineage=..., handlers=...)`;
see `CONTRIBUTING.md` and `beamtimehero ref agent-integration`. Every
registry is updated in place, so registration order and import order do
not interact.

Minimal composition example:

```python
import sys
from beamtimehero_cli import refdocs
from beamtimehero_cli.cli.api import build_parser, dispatch

def main() -> int:
    refdocs.register_doc("my-procedure", "/path/to/my_doc.md", "Project-specific procedure")
    parser = build_parser()
    trees = parser._subparsers._group_actions[0]  # the top-level subparsers action
    my_tree = trees.add_parser("my-subtree", help="Project-specific subcommands")
    my_tree.add_argument("--foo")
    args = parser.parse_args()
    if args.tree == "my-subtree":
        print("foo =", args.foo)
        return 0
    return dispatch(parser, args)

if __name__ == "__main__":
    sys.exit(main())
```

The default `beamtimehero` console-script keeps working unchanged for any
consumer that doesn't extend it.
