"""beamtimehero_cli tool catalog.

Public surface:

  * ``TOOL_DEFINITIONS`` — JSON-schema definitions for every tool.
  * ``TOOL_CATEGORIES`` — UI groupings.
  * ``CLI_TOOL_DEFINITION`` — single-tool wrapper for progressive discovery mode.
  * ``execute_tool(tree, name, args)`` — dispatch to a tool's Python implementation.
  * ``register_tools(...)`` — add out-of-tree tools to all three registries.

The three registries a tool needs to be whole are the definition (what
the agent sees), the lineage entry (what the tool *is* — including
whether it mutates) and the handler (what runs). ``register_tools`` fills
all three; the single-registry functions exist for consumers that only
need one.

Every one of them mutates the shipped container in place rather than
rebinding a module attribute. That is the whole point: ``categorize.py``
binds ``TOOL_LINEAGE`` at import, ``executor.py`` reads
``tools_core.DISPATCH``, and consumers hold their own references — so a
rebind is invisible to half the readers, which is exactly why consumers
resorted to monkey-patching library module attributes.
"""
from __future__ import annotations

from beamtimehero_cli.tool_catalog.cli_tool import CLI_TOOL_DEFINITION
from beamtimehero_cli.tool_catalog.definitions import (
    AUTONOMY_TOOL_CATEGORIES as _BASE_CATEGORIES,
    AUTONOMY_TOOL_DEFINITIONS as _BASE_TOOLS,
)
from beamtimehero_cli.tool_catalog.executor import execute_tool
from beamtimehero_cli.tool_catalog.lineage import register_lineage

TOOL_DEFINITIONS: list[dict] = list(_BASE_TOOLS)
TOOL_CATEGORIES = list(_BASE_CATEGORIES)

# Definitions a consumer registered, kept separately from the merged
# ``TOOL_DEFINITIONS`` so ``tools_core._build_dispatch()`` can rebuild
# from "library defs + registered defs" without depending on whether the
# consumer registered before or after ``tools_core`` was first imported.
_REGISTERED_DEFINITIONS: list[dict] = []


def register_definitions(defs) -> None:
    """Append out-of-tree tool definitions to ``TOOL_DEFINITIONS``.

    Rejects a duplicate ``(tree, ..., name)`` path with a ``ValueError``.
    A duplicate *name* is fine and common — ``spec-file/list_scans`` and
    ``s3df/list_scans`` are different tools — so the path is what has to
    be unique. Colliding paths would give argparse two leaves with the
    same name on one branch, where the second silently shadows the first.
    """
    from beamtimehero_cli.tool_catalog.categorize import categorize

    incoming = list(defs)
    if not incoming:
        return

    def _path(tdef: dict) -> tuple[str, ...]:
        name = (tdef.get("function") or {}).get("name") or ""
        return tuple(categorize(tdef)) + (name,)

    taken = {_path(d) for d in TOOL_DEFINITIONS}
    problems: list[str] = []
    for tdef in incoming:
        name = (tdef.get("function") or {}).get("name")
        if not name:
            problems.append("a definition has no function.name")
            continue
        path = _path(tdef)
        if path in taken:
            problems.append(f"duplicate tool path {'/'.join(path)}")
            continue
        taken.add(path)
    if problems:
        raise ValueError(
            "cannot register definitions ({}):\n  {}".format(
                len(problems), "\n  ".join(problems)
            )
        )

    TOOL_DEFINITIONS.extend(incoming)
    _REGISTERED_DEFINITIONS.extend(incoming)


def register_tools(
    *,
    definitions=(),
    lineage: dict | None = None,
    handlers: dict | None = None,
) -> None:
    """Register out-of-tree tools: definitions, lineage, handlers.

    One call is the supported way to extend the catalog. Order is fixed
    (definitions, lineage, handlers) and handlers go last because that is
    the step that rebuilds ``DISPATCH``, so the table is built once,
    against the final definition list and the final lineage.
    """
    if definitions:
        register_definitions(definitions)
    if lineage:
        register_lineage(lineage)
    if handlers:
        from beamtimehero_cli.tool_catalog.tools_core import register_handlers
        register_handlers(handlers)


__all__ = [
    "CLI_TOOL_DEFINITION",
    "TOOL_CATEGORIES",
    "TOOL_DEFINITIONS",
    "execute_tool",
    "register_definitions",
    "register_lineage",
    "register_tools",
]
