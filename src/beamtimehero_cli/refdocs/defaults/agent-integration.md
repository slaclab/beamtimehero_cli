# Using beamtimehero from an agent

This CLI exists to be driven by an LLM agent. The nested `--help` surface, the
JSON-only output, and the audit trail are all shaped around that. This doc is
how you wire one up.

## The two modes

**Progressive discovery (default).** Give the agent one tool that runs
`beamtimehero <args>` and let it explore with `--help`. This is the right
choice for a general coding agent or anything with shell access: the agent sees
a small surface and drills down only where it needs to.

**Full schema registration.** Hand the agent all 131 tool schemas up front.
Right for a purpose-built harness where the agent should not spend turns on
discovery.

Which mode you are in is a property of your harness, not of this CLI. The
`TOOLS_MODE` variable exists and is read by some consuming applications, but
nothing in `beamtimehero_cli` itself reads it — setting it here changes
nothing.

Get the schemas with:

```bash
beamtimehero catalog                        # every tool, JSON-schema form
beamtimehero catalog --tree exafs           # one branch
beamtimehero catalog --profile bl-aligner   # one agent profile's surface
beamtimehero catalog --names-only           # just the names
beamtimehero catalog --indent 0             # one line, for piping
```

The output is a list of `{"type": "function", "function": {name, description,
parameters}}` entries — the shape most harnesses already accept. Nothing about
it is Python-specific.

## Why this is safe to hand an agent

Four properties, all deliberate. They are the reason you can let a model drive
this without supervising every call:

- **Nothing reaches a beamline unless you say so.** `SPEC_MOCK` defaults to
  `1`, and every SPEC command answers from the mock backend. An agent exploring
  on a laptop cannot move a motor. Note the check is `SPEC_MOCK == "1"`
  exactly: `0` disables the mock, but so does any other value, including an
  empty string and a YAML `true` that arrives as `"True"`. Quote it in config
  files.
- **Mutations require a reason.** Every leaf under `spec-write` requires
  `--justification`, and refuses to run without it.
- **Everything is recorded.** Each invocation is written to a SQLite action
  log — argv, tool, justification, exit code, latency, stdout tail. See
  `beamtimehero ref action-log`.
- **Argument errors come back as JSON.** A bad flag returns
  `{"ok": false, "error": "argparse: ..."}` on stdout and exits 2. An agent can
  read and correct that; a stack trace on stderr it usually cannot.

  Be precise about the limit here, because it is the one property that does not
  hold all the way down. A tool that *runs* and then fails is different: it
  returns a plain-text line on stdout — `Tool error (tool/read_file): Path is
  outside scan directory: ...` — writes a traceback to stderr, and **exits 0**.
  So parse tool results as text, not JSON, and do not use the exit code to
  decide whether a tool succeeded. The message itself is written to be
  actionable; the envelope around it is not yet uniform.

## Mode 1: subprocess

Register one tool. A ready-made schema ships in the package as
`tool_catalog.CLI_TOOL_DEFINITION` — note it is a **list of one entry**, in the
same `{"type": "function", "function": {...}}` shape as `catalog` output, so
you can concatenate it with other tools directly. Import it rather than copying
it; the description below is illustrative and the shipped wording is kept in
sync with the parser:

```json
{
  "name": "run_command",
  "description": "Run a beamtimehero CLI command. Start with 'beamtimehero --help' to discover the command tree; use '--help' at any depth for a specific command's options.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "description": "Full command, e.g. 'beamtimehero spec-file list-scans --limit 5'"
      }
    },
    "required": ["command"]
  }
}
```

Then let the agent work down the tree:

```bash
beamtimehero --help                              # the branches
beamtimehero spec-file --help                    # leaves on one branch
beamtimehero spec-file extract-xas-descriptors --help
```

If the agent can read files and you are working in a source checkout, point it
at `docs/tool_catalog.html` — the whole surface on one page, with parameters
and backend lineage. That file is not shipped in the wheel; from an installed
package, `beamtimehero catalog` is the same content as JSON.

### From Claude Code

There is no MCP server yet, so allow the command and let Claude use Bash.
In `.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(beamtimehero --help)",
      "Bash(beamtimehero ref:*)",
      "Bash(beamtimehero catalog:*)",
      "Bash(beamtimehero spec-file:*)",
      "Bash(beamtimehero xrs:*)",
      "Bash(beamtimehero exafs:*)",
      "Bash(beamtimehero spec-read:*)"
    ]
  }
}
```

Those are the read-only and analysis branches. Leave `spec-write` off the
allowlist so beamline mutations stay a per-call decision, even with
`SPEC_MOCK=1`.

## Mode 2: in-process

```python
from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS, execute_tool

# Whatever your harness registers tools from.
schemas = TOOL_DEFINITIONS

# Dispatch by (tree, name). The tree is a tuple because the same leaf name
# exists on more than one branch — ("spec-file", "list_scans") reads SPEC
# files, ("s3df", "list_scans") reads Postgres.
text, images_b64 = execute_tool(("spec-file",), "list_scans", {"limit": 5})

print(text)                  # JSON string
for b64 in images_b64:       # base64 PNGs, when the tool produced figures
    ...
```

`execute_tool` returns `(result_text, images_b64)` and does not raise: an
unknown tool comes back as `"Unknown tool: ..."`, and a tool that fails comes
back as `"Tool error (...): ..."`. Scientific functions raise `ValueError` on
data that cannot support the analysis, and the handlers turn that into a JSON
`error` field — so an agent gets a sentence it can act on instead of an
exception.

### Which safety class a tool is in

Ask the catalogue, not the schema. Each tool's lineage entry carries a
`mutates` flag, and that is the one thing to filter on:

```python
from beamtimehero_cli.tool_catalog.lineage import TOOL_LINEAGE

TOOL_LINEAGE["move_motor"]["mutates"]          # True  — moves hardware
TOOL_LINEAGE["write_summary"]["mutates"]       # False — writes a file
TOOL_LINEAGE["post_slack_message"]["mutates"]  # False — messages a human
```

`mutates` means "issues a SPEC command registered as an action, so it
requires a `--justification` and is audited". Nothing broader: a tool that
writes a file, a calibration row or a Slack message changes state
somewhere and is still `False`, because none of them can touch the
beamline. It is also what puts a tool on the `spec-write` branch, so the
branch and the flag can never disagree.

Do not infer the class from `"justification" in parameters.required`. The
two agree today and a test keeps them agreeing, but only `mutates` is the
declaration — a consumer registering its own tool can mark it mutating
without a `justification` argument, and the inference would miss it.

## Composing your own CLI

A consumer that builds its own parser over this catalog imports from two
modules, both stable:

```python
from beamtimehero_cli.cli.api import build_parser, dispatch, run_with
from beamtimehero_cli.cli.trees import CANONICAL_TREES, RESERVED_TOP_LEVEL
```

`cli.api` re-exports the parser helpers (`ToolParser`, `build_ref_subtree`,
`build_catalog_subtrees`, `add_arg`, `run_ref`, `run_tool_leaf`, `dispatch`,
`run_with`, `main`). `cli.trees` holds the branch names and imports
nothing at all, so you can check a name against `RESERVED_TOP_LEVEL`
before anything reads `beamtimehero_cli.config`. Import from
`cli.__main__` only for something neither module exposes, and expect it to
move.

To add your own tools rather than just re-arrange these ones, call
`register_tools`:

```python
from beamtimehero_cli.tool_catalog import register_tools

register_tools(
    definitions=[MY_TOOL_DEF],
    lineage={"my_tool": {...}},        # eight required keys, incl. mutates
    handlers={"my_tool": t_my_tool},
)
```

Every registry is updated in place, so it does not matter whether you
register before or after importing the parser, and anything already
holding `TOOL_LINEAGE` or `DISPATCH` sees your tools. To replace a
handler rather than add one, register under the same key: a bare name
covers every branch that tool sits on, a `(tree, ..., name)` tuple covers
exactly one. `beamtimehero ref agent-integration` and `CONTRIBUTING.md`
are the same contract from the two sides.

Pass `executor=` to `run_tool_leaf`, `dispatch` or `main` to route calls
through your own dispatch table — a restricted surface, a guard, an audit
hook — instead of reassigning module attributes.

If what you are composing is a *restricted* surface for one agent — a
subset of the trees, a write allow-list, a motor allow-list — do not
assemble it from these helpers by hand. Declare it as an `AgentSurface`
and let `build_surface` generate the parser branch, the schemas, the
dispatch table, the guarded executor, the shell-permission pattern, the
prompt fragment and a checked-in manifest from the one declaration:

```bash
beamtimehero ref agent-surfaces
```

## What an agent will hit first

**No data.** There is no bundled sample data in this package. Set
`BL_SCAN_DIR` to a real scan root (or `SSRL_COLLECTOR_DIR` for SSRL Data
Collector files) before expecting the scan tools to return anything. The SPEC
tools work regardless, from the mock backend.

`list_scans` says so explicitly, which is the behaviour to rely on:

```json
{"scans": [], "error": "No scan directory configured: BL_SCAN_DIR=... ",
 "scan_dir_configured": false}
```

The others are less helpful, and an agent needs to know it: with nothing
configured `list-logs` returns `[]`, `read-scan` returns `Scan not found.`,
`list-files` returns `No files found in scan directory.` — none of which
distinguishes "no data here" from "not configured". If a read comes back empty,
check `BL_SCAN_DIR` before concluding the beamline has no scans.

**Configuration.** Everything resolves from environment variables.
`config.example.yaml` in the repository lists all of them, grouped by what you
are trying to do, with defaults. Point the CLI at a copy:

```bash
export BEAMTIMEHERO_CONFIG=/path/to/config.yaml
```

Exported variables always win over the file, so a checked-in baseline plus
per-host overrides works.

**Plot files.** Tools that draw figures return base64 PNGs; the CLI also writes
them to disk and reports the path. That defaults to `./data/tool_plots` under
the working directory — set `BEAMTIMEHERO_PLOTS_DIR` if you do not want them
landing wherever the agent happened to run.

## A first session, end to end

```bash
beamtimehero --help                                   # what exists
beamtimehero ref --list                               # the reference docs
beamtimehero catalog --names-only | head -20          # the tool names
SPEC_MOCK=1 beamtimehero spec-read get-beam-status    # a real answer, no beamline
beamtimehero spec-file list-scans --limit 5           # needs BL_SCAN_DIR
```

Every one of those runs with no configuration. The last returns the
"not configured" report until you set a scan directory, which is the honest
answer rather than an empty list.

A healthy install answers the third and fourth like this:

```
$ beamtimehero catalog --names-only | head -3
abort_current_scan
align_beamline
align_crystals

$ SPEC_MOCK=1 beamtimehero spec-read get-beam-status
{
  "ok": true,
  "kind": "read",
  "result": {
    "spear_current_ma": 485.2,
    "beamline_state": "OPEN",
    "gap_owned": true,
    "beam_good": true,
    "reason": null,
    "raw": "{'spear_current': 485.2, 'bl_state': 'OPEN', 'gap_owned': 1}"
  },
  "output": "{'spear_current': 485.2, 'bl_state': 'OPEN', 'gap_owned': 1}"
}
```

Those numbers are the mock backend's fixed values, so they are what you should
see verbatim on a working install with no configuration.

## When it doesn't work

| Symptom | Cause |
|---|---|
| `command not found: beamtimehero` | The virtualenv isn't active, or `pip install -e .` was run against a different interpreter. |
| `{"ok": false, "error": "argparse: invalid choice: ..."}` | The leaf is on a different branch. `beamtimehero catalog --names-only` lists every tool; note the CLI spells them with hyphens (`list-scans`) while tool descriptions cite them with underscores (`list_scans`). |
| A read returns `[]` or `Scan not found.` | Almost always `BL_SCAN_DIR` unset rather than genuinely empty — see "No data" above. |
| `Tool error (...)` on stdout, exit 0 | The tool ran and failed. Read the message; the exit code will not tell you. |
| Every SPEC call takes ~2 s | `SPEC_EVAL_URL` points at a sandbox that isn't running. The mock path probes it first and waits out the timeout. Unset it for pure off-beamline use. |
| A `spec-write` leaf says "Only in phase ..." | Descriptive only. Phase gating lives in the consuming application, not this package; the phase is recorded in the audit log and nothing here blocks the call. |
