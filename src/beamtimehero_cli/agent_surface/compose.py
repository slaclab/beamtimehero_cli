"""The algebra: five pure steps from a catalogue to a carried tool set.

Every consumer that ever restricted this CLI performed the same five
steps, in the same order, in prose:

    combine → select → filter_writes → rename → (namespace | wrap_dispatch)

and each one of them performed at least one of those steps differently
from the others. ``autonomous`` filtered writes by "does the schema
require ``justification``", which classified four of its own audited
tools as harmless. ``chemcat`` "selected" by deleting entries from the
library's global profile registry and putting them back in a ``finally``.
Both stamped roles by walking argparse privates afterwards, which is how
five of nine branches ended up unstamped.

So the steps are functions here, and they are pure: ``tuple`` in,
``tuple`` out, no globals read, no argparse touched — except in the last
two, which exist precisely to do the two impure things (build a parser,
build an executor) once and identically for everyone.

Order is preserved throughout, and it is catalogue order: what
``beamtimehero --help`` lists is what a surface lists, so the generated
manifest of a surface diffs against the catalogue rather than against a
sort nobody chose.
"""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Callable, Iterable, Mapping, Optional

from beamtimehero_cli.agent_surface.catalogue import Catalogue, ResolvedTool, kebab
from beamtimehero_cli.agent_surface.guard import motor_guard
from beamtimehero_cli.agent_surface.spec import ToolPath

if TYPE_CHECKING:  # pragma: no cover - typing only
    import argparse

    from beamtimehero_cli.agent_surface.spec import AgentSurface


def combine(catalogue: Catalogue) -> tuple[ResolvedTool, ...]:
    """Every tool in the catalogue, in catalogue order."""
    return tuple(catalogue.index().values())


def select(
    tools: Iterable[ResolvedTool],
    *,
    branches: Iterable[str],
    explicit: Iterable[ToolPath],
) -> tuple[ResolvedTool, ...]:
    """Keep the tools on ``branches``, plus those named in ``explicit``.

    A branch matches on the *top-level* segment, so carrying ``s3df``
    carries ``s3df psql`` with it. That is the only behaviour any
    consumer wanted, and the alternative (a branch that stops at its own
    depth) would have made ``s3df`` mean two different things depending
    on whether the psql leaves were registered yet.
    """
    wanted_branches = frozenset(branches)
    wanted_paths = frozenset(tuple(p) for p in explicit)
    return tuple(
        tool for tool in tools
        if tool.tree[0] in wanted_branches or tool.path in wanted_paths
    )


def filter_writes(
    tools: Iterable[ResolvedTool],
    write_tools: Iterable[str],
) -> tuple[ResolvedTool, ...]:
    """Drop every mutating tool whose name is not in ``write_tools``.

    Dropped, not refused-at-call-time: a tool an agent can see is a tool
    an agent will try, and a surface whose ``--help`` advertises moves it
    is not allowed to make trains the model to ignore its own scope.

    ``mutates`` is the declared lineage flag. The old rule — "the schema
    requires ``justification``" — made a tool's safety class a side
    effect of how its arguments were spelled.
    """
    allowed = frozenset(write_tools)
    return tuple(
        tool for tool in tools
        if not tool.mutates or tool.name in allowed
    )


def rename(
    tools: Iterable[ResolvedTool],
    *,
    layout: str,
    aliases: Optional[Mapping[str, ToolPath]] = None,
) -> tuple[ResolvedTool, ...]:
    """Set each tool's ``cli_name``: an alias if it has one, else kebab.

    Aliases are flat-only and keyed the way a reader writes them (leaf
    name → path), so they are inverted here rather than in the spec.
    """
    by_path: dict[ToolPath, str] = {}
    if layout == "flat":
        for alias, path in (aliases or {}).items():
            by_path[tuple(path)] = alias
    return tuple(
        dataclasses.replace(tool, cli_name=by_path.get(tool.path, kebab(tool.name)))
        for tool in tools
    )


def namespace(
    tools: Iterable[ResolvedTool],
    *,
    spec: "AgentSurface",
    subs: "argparse._SubParsersAction",
    agent_role: Optional[str] = None,
) -> "argparse._SubParsersAction":
    """Build the surface's branch under ``subs``; return its subparsers.

    Returning the branch's own ``add_subparsers`` action is what lets a
    consumer hang a local subtree (``steering``) beside the generated
    ones without rebuilding anything or reaching into privates.

    The two layouts differ in more than cosmetics. ``nested`` delegates
    to ``build_catalog_subtrees``, so the branch is the canonical tree
    structure with a smaller tool list — byte-identical to what the
    unrestricted parser builds for those tools. ``flat`` builds one leaf
    per tool directly, which is the only way a single-word invocation
    (``cc read-scan``) can exist.

    ``ref`` is mounted *inside* a nested branch because that branch is
    the agent's whole world: it never sees the root parser, so docs have
    to be reachable from where it stands. A flat surface's root keeps
    ``ref`` beside the branch instead (see ``SurfaceBuild.make_parser``),
    which is what the one flat consumer already does.
    """
    from beamtimehero_cli.cli.api import add_arg, build_catalog_subtrees, build_ref_subtree
    from beamtimehero_cli.cli.trees import TREE_HELPS

    tools = tuple(tools)
    branch = subs.add_parser(
        spec.name, help=(spec.description or "").strip() or None,
    )

    if spec.layout == "nested":
        branch_subs = branch.add_subparsers(dest="subtree", metavar="<tree>")
        if spec.include_ref:
            build_ref_subtree(branch_subs)
        carried = frozenset(spec.branches) | {tool.tree[0] for tool in tools}
        build_catalog_subtrees(
            branch_subs,
            [tool.definition for tool in tools],
            agent_role=agent_role,
            trees={path for path in TREE_HELPS if path[0] in carried},
        )
        return branch_subs

    leaves = branch.add_subparsers(dest="leaf", metavar="<command>")
    for tool in tools:
        fn = tool.definition.get("function") or {}
        params = fn.get("parameters") or {}
        properties = params.get("properties") or {}
        required = set(params.get("required") or [])
        leaf = leaves.add_parser(
            tool.cli_name, help=(fn.get("description") or "").strip(),
        )
        # ``_tool_category`` is the canonical tree, not the surface: the
        # leaf is a rename, not a new tool, and it must dispatch to the
        # same handler the canonical path reaches.
        defaults = {"_tool_name": tool.name, "_tool_category": tool.tree}
        if agent_role is not None:
            defaults["_agent_role"] = agent_role
        leaf.set_defaults(**defaults)
        for key, prop in properties.items():
            add_arg(leaf, key, prop or {}, key in required)
    return leaves


def wrap_dispatch(
    tools: Iterable[ResolvedTool],
    *,
    motors: Iterable[str],
    surface_name: str,
    before: Iterable[Callable] = (),
    after: Iterable[Callable] = (),
) -> tuple[dict, Callable]:
    """Build the surface's dispatch table and its guarded executor.

    The table holds only the carried paths, so an unknown tool is
    answered by the executor's own ``Unknown tool: a/b`` envelope rather
    than by a permission check someone has to remember to write. Tools
    with no handler (described in the catalogue, dispatched elsewhere)
    are left out: a key mapping to ``None`` would fail as a ``TypeError``
    inside the executor instead of as a missing tool.

    The motor guard goes in front of the consumer's own hooks, matching
    the order the old CLI-level check had: refuse the motor before
    validating the arguments of a call that is not going to happen.
    """
    table = {tool.path: tool.handler for tool in tools if tool.handler is not None}
    from beamtimehero_cli.tool_catalog.executor import make_executor

    hooks = (motor_guard(motors, surface_name),) + tuple(before)
    return table, make_executor(table, before=hooks, after=tuple(after))


__all__ = [
    "combine",
    "filter_writes",
    "namespace",
    "rename",
    "select",
    "wrap_dispatch",
]
