"""Static HTML catalog generator — the human-readable view of the toolbelt.

The CLI's nested ``--help`` surface is aimed at LLM agents; this module
renders the same catalog as a single self-contained HTML page for humans:

    python -m beamtimehero_cli.docgen              # writes docs/tool_catalog.html
    python -m beamtimehero_cli.docgen -o out.html

The page has two views over the same data:

  1. **The tree** — every CLI branch with its leaves, matching what
     ``beamtimehero --help`` exposes (plus the ``ref`` docs and profiles).
  2. **A flat A–Z list** — every tool with a one-sentence description,
     expandable to the full schema (parameters, lineage, backend).

No servers, no build step, no external assets — the output opens from disk.
Data comes straight from the live registry (``TOOL_DEFINITIONS`` +
``categorize()`` + ``TOOL_LINEAGE``), so regenerating after a catalog change
can never go stale the way a hand-written listing would.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

from beamtimehero_cli import refdocs
from beamtimehero_cli.cli.profiles import PROFILES
from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS
from beamtimehero_cli.tool_catalog.categorize import categorize
from beamtimehero_cli.tool_catalog.lineage import build_detailed_tool

# Canonical display order for branches; unknown branches (added later)
# sort alphabetically after these.
BRANCH_ORDER: list[tuple[str, ...]] = [
    ("spec-read",),
    ("spec-write",),
    ("spec-file",),
    ("xrs",),
    ("exafs",),
    ("tool",),
    ("db",),
    ("s3df",),
    ("s3df", "psql"),
    ("slack",),
]

# Human-facing blurb per branch. Richer than the argparse help strings —
# this page is documentation, not a usage line.
BRANCH_NOTES: dict[tuple[str, ...], str] = {
    ("tool",): (
        "General-purpose tools with no SPEC connection: control-log "
        "queries, generic plotting, file I/O, the SPEC-macro sandbox, "
        "and the sample camera."
    ),
    ("db",): (
        "Queries over the local SQLite action log — the audit trail that "
        "every CLI invocation writes to."
    ),
    ("spec-read",): (
        "Live reads from the SPEC server: motor positions, beam status, "
        "scan state. Never mutates anything."
    ),
    ("spec-write",): (
        "Live SPEC mutations — motor moves, scans, shutters, gains, "
        "configuration. Every tool on this branch requires "
        "--justification, which is recorded in the action log."
    ),
    ("spec-file",): (
        "Scan reads and XAS/HERFD analysis over SPEC data files on disk "
        "(the file-cache backend). Works without a live SPEC connection."
    ),
    ("xrs",): (
        "X-ray Raman scattering analysis on the energy-loss axis: "
        "reduction (energy-loss calibration, Compton subtraction, "
        "crystal summing/alignment) plus chemical interpretation. Kept "
        "separate from the XAS tools, which are wrong for XRS by "
        "construction — see beamtimehero ref xrs-analysis."
    ),
    ("exafs",): (
        "EXAFS k-space analysis: chi(k) extraction, Fourier transforms, "
        "and overlays. Reads SPEC files or SSRL EXAFS Data Collector "
        "ASCII directories."
    ),
    ("s3df",): (
        "S3DF-deployment backend: the same scan-read surface as "
        "spec-file, but served from Postgres metadata + pickled scan "
        "data instead of files on the beamline host."
    ),
    ("s3df", "psql"): (
        "Direct read-only SQL against the S3DF Postgres database."
    ),
    ("slack",): (
        "Slack messaging: post text and images to the experiment channel."
    ),
}

_ABBREV_TAIL = re.compile(
    r"(?:\be\.g|\bi\.e|\betc|\bvs|\bcf|\bca|\bapprox|\bNo)\.$", re.IGNORECASE
)


def first_sentence(text: str) -> str:
    """Best-effort first sentence of a tool description."""
    text = " ".join((text or "").split())
    for m in re.finditer(r"(?<=[.!?])\s+", text):
        head = text[: m.start()]
        if _ABBREV_TAIL.search(head):
            continue
        return head
    return text


def kebab(name: str) -> str:
    return name.replace("_", "-")


def collect() -> dict:
    """Assemble everything the page renders, straight from the registry."""
    tools: list[dict] = []
    for tdef in TOOL_DEFINITIONS:
        tree = categorize(tdef)
        detail = build_detailed_tool(tdef, "/".join(tree))
        detail["tree"] = tree
        detail["summary"] = first_sentence(detail["description"])
        # From the declared ``mutates`` flag, not from the schema: the
        # page should say what the catalogue claims about a tool, so that
        # a lineage entry and a page that disagree is a bug someone can
        # see (tests/test_mutates.py holds the two together).
        detail["needs_justification"] = detail["mutates"]
        tools.append(detail)

    by_branch: dict[tuple[str, ...], list[dict]] = {}
    for t in tools:
        by_branch.setdefault(t["tree"], []).append(t)
    for leaves in by_branch.values():
        leaves.sort(key=lambda t: t["name"])

    known = [b for b in BRANCH_ORDER if b in by_branch]
    extra = sorted(b for b in by_branch if b not in BRANCH_ORDER)
    branches = [(b, by_branch[b]) for b in known + extra]

    name_count: dict[str, int] = {}
    for t in tools:
        name_count[t["name"]] = name_count.get(t["name"], 0) + 1

    return {
        "tools": sorted(tools, key=lambda t: (t["name"], t["tree"])),
        "branches": branches,
        "name_count": name_count,
        "refs": refdocs.list_docs(),
        "profiles": PROFILES,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

E = html.escape


def _anchor(t: dict) -> str:
    return "t-" + "-".join(t["tree"]) + "-" + t["name"]


def _chip(tree: tuple[str, ...]) -> str:
    cls = "-".join(tree)
    return f'<span class="chip chip-{E(cls)}">{E(" / ".join(tree))}</span>'


def _cli_of(t: dict) -> str:
    return "beamtimehero " + " ".join(t["tree"]) + " " + kebab(t["name"])


def _render_tree(cat: dict) -> str:
    out: list[str] = []
    for tree, leaves in cat["branches"]:
        note = BRANCH_NOTES.get(tree, "")
        rows = "".join(
            f'<a class="leaf" href="#{_anchor(t)}" title="{E(t["summary"])}">'
            f"{E(kebab(t['name']))}</a>"
            for t in leaves
        )
        out.append(
            f'<details class="branch" open>'
            f"<summary>{_chip(tree)}"
            f'<code class="path">beamtimehero {E(" ".join(tree))}</code>'
            f'<span class="count">{len(leaves)}</span></summary>'
            f'<p class="note">{E(note)}</p>'
            f'<div class="leafgrid">{rows}</div>'
            f"</details>"
        )

    # Non-tool surfaces: bundled reference docs + agent profiles.
    refs = "".join(
        f'<div class="refrow"><code>{E(name)}</code><span>{E(desc)}</span></div>'
        for name, desc in cat["refs"]
    )
    out.append(
        f'<details class="branch" open>'
        f'<summary><span class="chip chip-ref">ref</span>'
        f'<code class="path">beamtimehero ref &lt;name&gt;</code>'
        f'<span class="count">{len(cat["refs"])}</span></summary>'
        f'<p class="note">Bundled markdown reference docs — background reading, '
        f"not tools. Enumerate with beamtimehero ref --list.</p>"
        f'<div class="reflist">{refs}</div></details>'
    )
    for pname, profile in cat["profiles"].items():
        aliases = profile.get("aliases") or {}
        rows = "".join(
            f'<div class="refrow"><code>{E(alias)}</code>'
            f'<span>&rarr; beamtimehero {E(" ".join(path[:-1]))} {E(kebab(path[-1]))}</span></div>'
            for alias, path in aliases.items()
        )
        out.append(
            f'<details class="branch" open>'
            f'<summary><span class="chip chip-profile">profile</span>'
            f'<code class="path">beamtimehero {E(pname)}</code>'
            f'<span class="count">{len(aliases)}</span></summary>'
            f'<p class="note">{E(profile.get("description") or "")} '
            f"Profiles add no tools — each leaf is an alias for a canonical "
            f"tool elsewhere in the tree.</p>"
            f'<div class="reflist">{rows}</div></details>'
        )
    return "".join(out)


def _render_inputs(inputs: list[dict]) -> str:
    if not inputs:
        return '<p class="noargs">No parameters.</p>'
    rows = []
    for p in inputs:
        bits = [E(p.get("type") or "string")]
        if p.get("required"):
            bits.append('<b class="req">required</b>')
        if "default" in p:
            bits.append(f"default: <code>{E(repr(p['default']))}</code>")
        if p.get("enum"):
            opts = " | ".join(E(str(v)) for v in p["enum"])
            bits.append(f"one of: <code>{opts}</code>")
        rows.append(
            f'<tr><td class="flagcell"><code>--{E(kebab(p["name"]))}</code>'
            f'<div class="meta">{" &middot; ".join(bits)}</div></td>'
            f"<td>{E(p.get('description') or '')}</td></tr>"
        )
    return '<table class="params"><tbody>' + "".join(rows) + "</tbody></table>"


def _render_facts(t: dict, anchors_by_name: dict[str, str]) -> str:
    facts: list[tuple[str, str]] = []
    if t.get("python_func"):
        facts.append(("Python", f"<code>{E(t['python_func'])}</code>"))
    if t.get("spec_command"):
        facts.append(("SPEC command", f"<code>{E(str(t['spec_command']))}</code>"))
    if t.get("output"):
        facts.append(("Output", E(t["output"])))
    if t.get("source"):
        src = E(t["source"])
        if t.get("source_detail"):
            src += f' <span class="dim">— {E(t["source_detail"])}</span>'
        facts.append(("Source", src))
    deps = t.get("depends_on") or []
    if deps:
        links = []
        for d in deps:
            a = anchors_by_name.get(d)
            links.append(
                f'<a href="#{a}"><code>{E(d)}</code></a>' if a else f"<code>{E(d)}</code>"
            )
        facts.append(("Depends on", " ".join(links)))
    if not facts:
        return ""
    rows = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in facts)
    return f'<table class="facts">{rows}</table>'


def _render_flat(cat: dict) -> str:
    anchors_by_name = {}
    for t in cat["tools"]:
        anchors_by_name.setdefault(t["name"], _anchor(t))

    out: list[str] = []
    for t in cat["tools"]:
        q = " ".join(
            [t["name"], kebab(t["name"]), " ".join(t["tree"]), t["description"]]
        ).lower()
        just = (
            ' <span class="badge">--justification</span>'
            if t["needs_justification"]
            else ""
        )
        body = [f'<p class="cli"><code>{E(_cli_of(t))}</code>{just}</p>']
        body.append(f"<p>{E(t['description'])}</p>")
        long_d = t.get("long_description") or ""
        if long_d and long_d.strip() != t["description"].strip():
            body.append(f'<p class="long">{E(long_d)}</p>')
        if cat["name_count"][t["name"]] > 1:
            others = [
                " / ".join(o["tree"])
                for o in cat["tools"]
                if o["name"] == t["name"] and o["tree"] != t["tree"]
            ]
            body.append(
                f'<p class="twin">A second implementation of this tool lives '
                f'under <b>{E(", ".join(others))}</b> — same schema, different '
                f"backend.</p>"
            )
        body.append(_render_inputs(t["inputs"]))
        body.append(_render_facts(t, anchors_by_name))
        out.append(
            f'<details class="tool" id="{_anchor(t)}" data-q="{E(q)}">'
            f'<summary><code class="tname">{E(t["name"])}</code>{_chip(t["tree"])}'
            f'<span class="oneline">{E(t["summary"])}</span></summary>'
            f'<div class="tbody">{"".join(body)}</div></details>'
        )
    return "".join(out)


_CSS = """
:root {
  --paper: #f6f2ea; --panel: #fdfbf6; --ink: #221d14; --dim: #6f6656;
  --rule: #d9d1bf; --rule-strong: #a89c82; --accent: #9a3412;
  --code-bg: #ede7d9; --shadow: 0 1px 3px rgba(60, 48, 20, .08);
  --c-tool: #6d5a10; --c-db: #58622a; --c-spec-read: #14636b;
  --c-spec-write: #a03016; --c-spec-file: #1d5a96; --c-xrs: #7b3d8f;
  --c-exafs: #316648; --c-s3df: #8a5a1f; --c-slack: #9c2b60;
  --c-ref: #5c5c66; --c-profile: #4a5a83;
}
:root[data-theme="dark"] {
  --paper: #17130c; --panel: #201a11; --ink: #e9e1cf; --dim: #a2937a;
  --rule: #3a3323; --rule-strong: #5c5138; --accent: #e8863c;
  --code-bg: #2c2517; --shadow: 0 1px 3px rgba(0, 0, 0, .5);
  --c-tool: #d4b13c; --c-db: #b3c163; --c-spec-read: #58bcc7;
  --c-spec-write: #f08b64; --c-spec-file: #6aabe8; --c-xrs: #c98add;
  --c-exafs: #7cc39a; --c-s3df: #d9a659; --c-slack: #e577ab;
  --c-ref: #b0b0bd; --c-profile: #97a9d9;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; background: var(--paper); color: var(--ink);
  font: 16px/1.55 Charter, "Iowan Old Style", Cambria, Georgia, serif;
  background-image: radial-gradient(rgba(120, 100, 60, .06) 1px, transparent 1px);
  background-size: 26px 26px;
}
code, .tname, .path, .leaf {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: .86em;
}
code { background: var(--code-bg); padding: .1em .35em; border-radius: 3px; }
a { color: var(--accent); }
.wrap { max-width: 920px; margin: 0 auto; padding: 0 24px 96px; }

header { padding: 56px 0 0; }
.eyebrow {
  letter-spacing: .22em; text-transform: uppercase; font-size: .72rem;
  color: var(--dim); font-family: ui-monospace, Menlo, monospace;
}
h1 { font-size: 2.7rem; margin: .2em 0 .1em; font-weight: 600; letter-spacing: -.01em; }
h1 .thin { color: var(--accent); font-style: italic; font-weight: 400; }
.lede { max-width: 60ch; color: var(--dim); margin-top: .4em; }
.statstrip {
  display: flex; flex-wrap: wrap; gap: 0 36px; margin: 28px 0 0;
  border-top: 3px double var(--rule-strong); border-bottom: 1px solid var(--rule);
  padding: 12px 0;
}
.stat b { display: block; font-size: 1.5rem; font-weight: 600; }
.stat span {
  font-size: .68rem; letter-spacing: .14em; text-transform: uppercase; color: var(--dim);
}
#themetoggle {
  margin-left: auto; align-self: center; cursor: pointer;
  background: none; border: 1px solid var(--rule-strong); color: var(--ink);
  border-radius: 999px; padding: .35em .9em; font: inherit; font-size: .8rem;
}

h2 {
  margin: 64px 0 6px; font-size: 1.5rem;
  display: flex; align-items: baseline; gap: .6em;
}
h2 .no {
  color: var(--accent); font-family: ui-monospace, Menlo, monospace;
  font-size: .8em; font-weight: 400;
}
.sectionnote { color: var(--dim); margin: 0 0 20px; max-width: 68ch; }

#how p { max-width: 72ch; margin: .5em 0; }
#how code { white-space: nowrap; }

.branch {
  background: var(--panel); border: 1px solid var(--rule); border-radius: 6px;
  margin: 10px 0; box-shadow: var(--shadow);
}
.branch > summary {
  display: flex; align-items: center; gap: 12px; padding: 10px 16px;
  cursor: pointer; list-style: none;
}
.branch > summary::before {
  content: "▸"; color: var(--dim); font-size: .8em; transition: transform .15s;
}
.branch[open] > summary::before { transform: rotate(90deg); }
.branch .path { color: var(--dim); background: none; }
.branch .count {
  margin-left: auto; font-family: ui-monospace, Menlo, monospace;
  font-size: .78rem; color: var(--dim); border: 1px solid var(--rule);
  border-radius: 999px; padding: .05em .6em;
}
.branch .note { margin: 0 16px 10px 40px; color: var(--dim); font-size: .92rem; max-width: 70ch; }
.leafgrid { columns: 3 200px; gap: 24px; padding: 0 16px 14px 40px; }
.leaf { display: block; text-decoration: none; padding: .1em 0; color: var(--ink); }
.leaf:hover { color: var(--accent); }
.reflist { padding: 0 16px 14px 40px; }
.refrow { display: flex; gap: 14px; padding: .18em 0; align-items: baseline; }
.refrow span { color: var(--dim); font-size: .92rem; }

.chip {
  font-family: ui-monospace, Menlo, monospace; font-size: .72rem;
  padding: .12em .55em; border-radius: 999px; white-space: nowrap;
  color: var(--c, var(--dim));
  background: color-mix(in srgb, var(--c, var(--dim)) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--c, var(--dim)) 40%, transparent);
}
.chip-tool { --c: var(--c-tool); } .chip-db { --c: var(--c-db); }
.chip-spec-read { --c: var(--c-spec-read); } .chip-spec-write { --c: var(--c-spec-write); }
.chip-spec-file { --c: var(--c-spec-file); } .chip-xrs { --c: var(--c-xrs); }
.chip-exafs { --c: var(--c-exafs); } .chip-s3df, .chip-s3df-psql { --c: var(--c-s3df); }
.chip-slack { --c: var(--c-slack); } .chip-ref { --c: var(--c-ref); }
.chip-profile { --c: var(--c-profile); }

.toolbar {
  position: sticky; top: 0; z-index: 5; display: flex; gap: 10px; align-items: center;
  background: var(--paper); padding: 12px 0; border-bottom: 1px solid var(--rule);
}
#q {
  flex: 1; font: inherit; font-size: .95rem; color: var(--ink);
  background: var(--panel); border: 1px solid var(--rule-strong);
  border-radius: 6px; padding: .5em .9em;
}
#q:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.toolbar button {
  background: none; border: 1px solid var(--rule-strong); color: var(--dim);
  border-radius: 6px; padding: .45em .8em; font: inherit; font-size: .78rem; cursor: pointer;
}
.toolbar button:hover { color: var(--accent); border-color: var(--accent); }
#hits { font-size: .78rem; color: var(--dim); font-family: ui-monospace, Menlo, monospace; }

.tool { border-bottom: 1px solid var(--rule); }
.tool > summary {
  display: flex; align-items: baseline; gap: 12px; padding: 9px 4px;
  cursor: pointer; list-style: none;
}
.tool > summary:hover { background: color-mix(in srgb, var(--accent) 5%, transparent); }
.tool .tname { font-weight: 600; background: none; color: var(--accent); }
.tool .oneline {
  color: var(--dim); font-size: .92rem; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; flex: 1;
}
.tool[open] .oneline { display: none; }
.tool:target > summary { outline: 2px solid var(--accent); outline-offset: -2px; }
.tbody { padding: 2px 8px 20px 24px; max-width: 78ch; }
.tbody .cli { margin: .2em 0 .8em; }
.badge {
  font-family: ui-monospace, Menlo, monospace; font-size: .72rem;
  color: var(--c-spec-write); border: 1px solid var(--c-spec-write);
  border-radius: 999px; padding: .1em .55em; margin-left: .6em;
}
.long, .twin { color: var(--dim); font-size: .94rem; }
.twin b { color: var(--ink); font-weight: 600; }
.noargs { color: var(--dim); font-style: italic; }
table.params, table.facts {
  border-collapse: collapse; width: 100%; margin: 12px 0; font-size: .9rem;
}
table.params { border-top: 1px solid var(--rule-strong); }
table.params td { padding: 7px 14px 7px 0; border-bottom: 1px solid var(--rule); vertical-align: top; }
table.params td.flagcell { width: 15em; }
table.params .meta { color: var(--dim); font-size: .78rem; margin-top: 2px; max-width: 24ch; }
.req { color: var(--c-spec-write); font-weight: 600; }
table.facts th {
  text-align: left; color: var(--dim); font-weight: 500; padding: 4px 14px 4px 0;
  font-size: .78rem; letter-spacing: .08em; text-transform: uppercase;
  vertical-align: top; white-space: nowrap;
}
table.facts td { padding: 4px 0; vertical-align: top; }
.dim { color: var(--dim); }

footer {
  margin-top: 72px; border-top: 3px double var(--rule-strong); padding-top: 14px;
  color: var(--dim); font-size: .82rem;
}
@media (max-width: 640px) {
  h1 { font-size: 2rem; }
  .tool .oneline { display: none; }
}
"""

_JS = """
(function () {
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem("bth-theme"); } catch (e) {}
  if (saved) root.setAttribute("data-theme", saved);
  else if (matchMedia("(prefers-color-scheme: dark)").matches)
    root.setAttribute("data-theme", "dark");
  document.getElementById("themetoggle").addEventListener("click", function () {
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("bth-theme", next); } catch (e) {}
  });

  var tools = Array.prototype.slice.call(document.querySelectorAll(".tool"));
  var q = document.getElementById("q");
  var hits = document.getElementById("hits");
  function filter() {
    var needle = q.value.trim().toLowerCase();
    var shown = 0;
    tools.forEach(function (t) {
      var ok = !needle || t.getAttribute("data-q").indexOf(needle) !== -1;
      t.style.display = ok ? "" : "none";
      if (ok) shown++;
    });
    hits.textContent = shown + " / " + tools.length;
  }
  q.addEventListener("input", filter);
  filter();

  document.getElementById("expand").addEventListener("click", function () {
    tools.forEach(function (t) { t.open = true; });
  });
  document.getElementById("collapse").addEventListener("click", function () {
    tools.forEach(function (t) { t.open = false; });
  });

  function openHash() {
    if (!location.hash) return;
    var el = document.getElementById(location.hash.slice(1));
    if (el && el.tagName === "DETAILS") { el.open = true; }
  }
  window.addEventListener("hashchange", openHash);
  openHash();
})();
"""


def render() -> str:
    cat = collect()
    n_tools = len(cat["tools"])
    n_branches = len(cat["branches"])
    n_write = sum(1 for t in cat["tools"] if t["needs_justification"])

    stats = "".join(
        f'<div class="stat"><b>{v}</b><span>{k}</span></div>'
        for k, v in [
            ("tools", n_tools),
            ("branches", n_branches),
            ("SPEC-mutating", n_write),
            ("reference docs", len(cat["refs"])),
        ]
    )

    how = (
        "<p>Every tool is an OpenAI-style JSON-schema function definition in "
        "<code>tool_catalog/definitions.py</code> — one flat list, no "
        "decorators, no registration magic. <code>categorize()</code> assigns "
        "each definition to a branch of the CLI tree; a definition can also pin "
        "its own branch, which is how the same tool name (say "
        "<code>list_scans</code>) exists under both <b>spec-file</b> and "
        "<b>s3df</b> with identical schemas but different backends.</p>"
        "<p>The CLI surface is generated from those schemas: tool "
        "<code>plot_scan</code> becomes <code>beamtimehero spec-file "
        "plot-scan</code>, and each JSON-schema parameter becomes a "
        "<code>--kebab-case</code> flag. That nested <code>--help</code>-at-"
        "every-depth surface is the LLM agent's discovery mechanism; this page "
        "is the same catalog laid out for humans.</p>"
        "<p>Two safety conventions run through everything: every tool on "
        "<b>spec-write</b> requires <code>--justification</code>, and every "
        "CLI invocation — read or write — is recorded in the local SQLite "
        "action log (query it via the <b>db</b> branch).</p>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>beamtimehero — tool catalog</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">SSRL BL15-2 &middot; SPEC toolbelt for LLM agents</div>
  <h1>beamtimehero <span class="thin">tool catalog</span></h1>
  <p class="lede">The complete tool surface of the <code>beamtimehero</code> CLI:
  what an agent can call, where each tool lives in the command tree, and what
  it does under the hood.</p>
  <div class="statstrip">{stats}
    <button id="themetoggle" type="button">light / dark</button>
  </div>
</header>

<section id="how">
  <h2><span class="no">&sect;0</span> How the catalog works</h2>
  {how}
</section>

<section id="tree">
  <h2><span class="no">&sect;1</span> The tree</h2>
  <p class="sectionnote">The CLI branch structure, exactly as
  <code>beamtimehero --help</code> exposes it. Click any leaf to jump to its
  full entry below.</p>
  {_render_tree(cat)}
</section>

<section id="tools">
  <h2><span class="no">&sect;2</span> All tools, A&ndash;Z</h2>
  <p class="sectionnote">Every tool with a one-sentence description. Expand an
  entry for the full description, parameters, and backend lineage.</p>
  <div class="toolbar">
    <input id="q" type="search" placeholder="filter by name, branch, or description&hellip;"
      autocomplete="off" spellcheck="false">
    <span id="hits"></span>
    <button id="expand" type="button">expand all</button>
    <button id="collapse" type="button">collapse all</button>
  </div>
  {_render_flat(cat)}
</section>

<footer>Generated by <code>python -m beamtimehero_cli.docgen</code>
from the live tool registry &mdash; rerun after catalog changes; do not edit by hand.</footer>
</div>
<script>{_JS}</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m beamtimehero_cli.docgen",
        description="Render the tool catalog as a static HTML page.",
    )
    parser.add_argument(
        "-o", "--output", default="docs/tool_catalog.html",
        help="Output path (default: docs/tool_catalog.html, relative to cwd).",
    )
    args = parser.parse_args(argv)

    page = render()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)

    cat = collect()
    print(
        f"wrote {out} — {len(cat['tools'])} tools, "
        f"{len(cat['branches'])} branches, {len(cat['refs'])} ref docs"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
