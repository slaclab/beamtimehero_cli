"""The names of the CLI's branches, and nothing else.

Split out of ``cli/__main__.py`` so a consumer can ask "is this name
free?" or "what does this branch do?" without importing the parser —
which pulls in ``config``, the tool catalog and matplotlib. Two things
made that worth a module of its own:

* An agent surface has to reject its own name if it collides with a
  canonical branch, and that check happens while validating a spec,
  before any catalogue exists.
* ``chemcatal`` sets environment variables before importing anything that
  reads ``beamtimehero_cli.config``, so a module it imports early must
  stay import-light. This one imports nothing at all.

Keep it that way: no imports, no computed values that need the catalog.
"""
from __future__ import annotations


# The ten trees the library itself puts tools on. ``categorize()`` can
# only ever return one of these as a first segment (plus nested paths
# below them, e.g. ``("s3df", "psql")``).
#
# ``research`` is a tree of its own with a single leaf on it, which looks
# like over-structuring until you read it as a permission boundary: an
# agent surface carries whole branches, so a tool that shares a branch
# with anything else is carried by everything that carries that branch.
# The research sandbox reads the open web and returns text an agent will
# act on, so it has to be grantable to one agent without being granted to
# the five that carry ``tool``.
CANONICAL_TREES: frozenset[str] = frozenset({
    "tool", "db", "spec-read", "spec-write", "spec-file",
    "s3df", "slack", "xrs", "exafs", "research",
})

# Everything a top-level name may not be. The two extras are not tool
# trees: ``ref`` serves the bundled reference docs and ``catalog`` exports
# the schemas as JSON. They are still occupied names, so an agent surface
# or a profile called either one would shadow them in ``--help``.
RESERVED_TOP_LEVEL: frozenset[str] = CANONICAL_TREES | {"ref", "catalog"}

# Branch path → the one-line help argparse renders for it. Iteration order
# is the order the branches are created in, which is the order they appear
# in ``beamtimehero --help`` — so this is a display decision, not a set.
TREE_HELPS: dict[tuple[str, ...], str] = {
    ("tool",): "Non-SPEC tools: data queries, analysis, plotting, file I/O.",
    ("db",): "Action-log queries.",
    ("spec-read",): "SPEC-bound reads (motor positions, beam status). No mutation.",
    ("spec-write",): "SPEC-bound mutations. Every leaf requires --justification.",
    ("spec-file",): "Scan tools that read SPEC files directly (file-cache backend).",
    ("s3df",): "S3DF-deployment tools (Postgres metadata + pickle scan data).",
    ("s3df", "psql"): "Direct Postgres queries (raw SQL, command/log queries).",
    ("slack",): "Slack messaging tools.",
    ("xrs",): "X-ray Raman (XRS) analysis: energy-loss reduction + interpretation.",
    ("exafs",): "EXAFS k-space analysis: chi(k) extraction, Fourier transforms.",
    ("research",): "Sandboxed literature/analysis research. Returns untrusted third-party text.",
}


__all__ = ["CANONICAL_TREES", "RESERVED_TOP_LEVEL", "TREE_HELPS"]
