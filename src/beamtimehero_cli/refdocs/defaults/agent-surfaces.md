# Agent surfaces

An *agent surface* is a restricted view of this CLI, declared once as data
and compiled into everything that has to agree with it: the parser branch,
the tool schemas, the dispatch table, the executor, the shell-permission
pattern, the prompt paragraph, and a checked-in manifest.

It replaces a pattern every consumer of this library had independently
arrived at, and independently got slightly wrong: rebuild a smaller parser
by hand, stamp a role onto the leaves by walking argparse internals,
reassign a library module attribute to swap the executor, and keep the
motor list in one file, the write-tool list in another, and the
`Bash(...)` permission in a third.

Read this with the general integration doc, which covers the two
integration modes; this document is only about restricting the surface.

```bash
beamtimehero ref agent-integration
beamtimehero ref agent-surfaces
```

## Declare it

```python
from beamtimehero_cli.agent_surface import AgentSurface, build_surface

SURFACE = AgentSurface(
    name="blaligner",
    description="Beamline alignment agent.",
    layout="nested",
    branches=("tool", "spec-read", "spec-write", "spec-file"),
    write_tools={"move_motor", "run_motor_scan"},
    motors={"Sx", "Sy", "Sz"},
    phase="sample_alignment",
)
```

`AgentSurface` is frozen and rejects unknown fields. A misspelled field
name is an import-time error rather than a silently ignored allow-list,
because an ignored allow-list looks exactly like a working one.

Every field: `name`, `description`, `layout`, `branches`, `tools`,
`write_tools`, `motors`, `exclusive`, `include_ref`, `aliases`, `phase`.
The machine-readable shape is `docs/agent_surface.schema.json`, generated
from the model.

## Build it

```python
from beamtimehero_cli.agent_surface import Catalogue, build_surface

catalogue = Catalogue.default()          # after every register_tools() call
build = build_surface(SURFACE, catalogue)
```

`Catalogue.default()` snapshots the three registries — definitions,
lineage, dispatch — as they stand at that moment. Take it from your entry
point, not at module import, so consumer tools registered through
`register_tools()` are in it.

`build_surface` raises `SurfaceError` listing *every* problem at once: an
unknown tool path, a branch that does not exist, a `write_tools` entry
that is not a mutating tool or is not carried, a flat leaf name built
twice. One typo in a fifty-entry tool list costs one run, not fifty.

## The six artifacts

| Artifact | Call |
|---|---|
| Parser branch | `build.attach(subparsers)` → the branch's own subparsers |
| A whole parser | `build.make_parser(prog=...)` → `ref` + the branch, nothing else |
| Tool schemas | `build.schemas()` → definitions in surface order, each with `x-cli-path` |
| Dispatch + executor | `build.dispatch`, `build.executor` |
| Shell permission | `build.bash_pattern()` → `Bash(beamtimehero blaligner:*)` |
| Prompt fragment | `build.prompt_fragment()` → markdown |
| Manifest | `build.manifest()` → the spec plus everything it resolved to |

`attach` returns the branch's subparsers so you can hang a local subtree
beside the generated ones. `make_parser` leaves `build.root_subparsers`
set, which is where local top-level leaves go.

Dispatch a leaf through the surface's own executor:

```python
rc = build.run_leaf(args)                # = run_tool_leaf(args, executor=build.executor)
rc = build.run(parser, args)             # ref / catalog / --help / leaf
```

## Nested and flat

`layout="nested"` keeps the canonical trees under the branch, so a leaf is
reached as `<surface> <tree> <command>`; the branch is byte-identical to
what the unrestricted parser builds for the same tools. `layout="flat"`
puts one kebab leaf per tool directly under the branch, so a leaf is
`<surface> <command>` — shorter to type and to prompt, at the cost of
needing `aliases` when two carried tools share a name.

Both exist in production. Neither is a migration of the other.

## `write_tools` is about `mutates`, not about arguments

A tool is mutating if its lineage entry says `"mutates": true`. Any
mutating tool the surface would have carried and that is not named in
`write_tools` is **dropped** — not carried-and-refused, because a tool an
agent can see in `--help` is a tool it will try.

The old rule, "the schema requires `justification`", made a tool's safety
class a side effect of how its arguments were spelled. `write_tools` holds
*names*, not paths, because lineage is name-keyed; no tool name that
exists on two trees is mutating, and a test enforces that so the filter
can never half-apply.

## Motors are enforced in the executor

`motors` is checked by a before-hook on `build.executor`, so a harness
calling the executor directly is guarded too. The refusal is a JSON
envelope with `ok: false`, `agent_role` and `allowed_motors`.

A carried tool that takes a motor argument with an empty `motors` set is
an error: an empty set refuses every move, which is a surface nobody can
use.

Whether the motor *names* are checked depends on whether you supply a
source. There is no enumerable motor list in this library — the SPEC
config exists only on the beamline host — so:

```python
from beamtimehero_cli.spec_data import spec_config

Catalogue.default(known_motors=spec_config.known_motors())   # beamline host
Catalogue.default(known_motors=spec_config.mock_motors())    # CI, mock set
Catalogue.default()                                          # names recorded only
```

`known_motors()` returns `None` off the host, and the manifest records
which happened as `motors_validated`.

## `exclusive`

`exclusive=True` means no canonical tree may sit beside this branch in the
same parser; `attach` refuses if one is already there. It does not mean
"this branch and `ref` only" — an exclusive surface may carry as many
local leaves of its own as it likes. The point is that a denied tree is
*absent* from the parser, so invoking it is a parse error rather than a
permission decision.

## Check the manifest in

`build.manifest()` is deterministic: sorted leaves, sorted sets, paths as
strings. Check it in next to the spec and assert equality in a test. That
gives you the acceptance rule for adopting a surface — the generated
surface equals the current one, before any old code is deleted — and
afterwards, any widening of an agent's reach arrives as a reviewable diff
instead of as behaviour.

For the migration itself, compare parsers rather than manifests:

```python
from beamtimehero_cli.agent_surface.snapshot import snapshot_parser, strip_keys

old = strip_keys(snapshot_parser(hand_built), ["_agent_role"])
new = strip_keys(snapshot_parser(generated), ["_agent_role"])
assert old == new
```

`_agent_role` is stripped because the generated build stamps it on every
leaf while the hand-written walks usually reached only some — that
difference is the fix, so assert it separately.

## Import order

`from beamtimehero_cli.agent_surface import AgentSurface` does not import
`beamtimehero_cli.config`. Only `spec.py` is imported eagerly; everything
else resolves through the package's `__getattr__` on first use. So a
module that declares a surface can be imported before the environment
variables `config` reads at import time have been set. A subprocess test
holds that property; do not add an eager import to the package
`__init__`.
