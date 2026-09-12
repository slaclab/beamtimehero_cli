"""A snapshot of "what tools exist", taken once and passed around.

The three registries a tool needs to be whole — its definition, its
lineage entry, its handler — are module globals that ``register_tools``
mutates in place. That is right for the registries and wrong for
composition: a surface built from globals is a surface whose meaning
depends on when it was built. So composition takes a ``Catalogue``: a
plain snapshot, taken after registration, that the pure functions in
``compose.py`` read and nothing writes.

It also puts the two lookups a surface needs behind names that say what
they answer. ``index()`` is keyed by path, because the path is the
identity. ``mutates(name)`` is keyed by *name*, because lineage is, and
pretending otherwise here would hide a real limitation — see its
docstring.

Module-level imports stay light on purpose: reaching the library's own
catalogue means importing the science stack, so that happens inside
``default()``, not at import.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from beamtimehero_cli.agent_surface.spec import ToolPath


def kebab(name: str) -> str:
    """``get_latest_scan`` → ``get-latest-scan``: the CLI spelling."""
    return name.replace("_", "-")


@dataclass(frozen=True)
class ResolvedTool:
    """One tool, with everything a surface needs to place and call it.

    Identity is ``path``. Two tools may share ``name`` (``list_scans``
    lives on ``spec-file`` and on ``s3df``) and they are not the same
    tool: different handlers, different backends, different answers.
    """

    #: ``(tree, ..., name)`` — the canonical identity.
    path: ToolPath
    #: The catalogue name, as lineage and ``write_tools`` spell it.
    name: str
    #: ``path[:-1]``, carried separately because it is what
    #: ``execute_tool``/``_tool_category`` take.
    tree: ToolPath
    #: The JSON-schema definition the agent sees.
    definition: dict
    #: The declared ``mutates`` flag, not an inference from the schema.
    mutates: bool
    #: The dispatch entry, or ``None`` for a tool described in the
    #: catalogue but dispatched by someone else's executor.
    handler: Optional[Callable]
    #: The leaf name argparse will use. ``kebab(name)`` unless a flat
    #: surface aliased it.
    cli_name: str


class Catalogue:
    """Definitions, lineage and dispatch as they stood at one moment."""

    def __init__(
        self,
        definitions: Iterable[dict],
        lineage: dict,
        dispatch: dict,
        known_motors: Optional[Iterable[str]] = None,
    ) -> None:
        self.definitions: list[dict] = list(definitions)
        self.lineage: dict = dict(lineage)
        self.dispatch: dict = dict(dispatch)
        #: ``None`` means "no motor source was supplied", which is not the
        #: same as "no motors exist" — the distinction is what lets a
        #: surface be validated off the beamline host. Never guessed here:
        #: a consumer opts in by passing ``spec_config.known_motors()`` or
        #: ``spec_config.mock_motors()``.
        self.known_motors: Optional[frozenset[str]] = (
            None if known_motors is None else frozenset(known_motors)
        )
        self._index: Optional[dict[ToolPath, ResolvedTool]] = None

    @classmethod
    def default(cls, *, known_motors: Optional[Iterable[str]] = None) -> "Catalogue":
        """Snapshot the library's registries as they stand right now.

        Call it *after* every ``register_tools`` call, which in practice
        means from the consumer's entry point rather than at its import
        time. Calling it neither reads a motor source nor touches the
        beamline: ``known_motors`` stays ``None`` unless asked for.
        """
        from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS
        from beamtimehero_cli.tool_catalog.lineage import TOOL_LINEAGE
        from beamtimehero_cli.tool_catalog.tools_core import DISPATCH

        return cls(
            TOOL_DEFINITIONS,
            TOOL_LINEAGE,
            DISPATCH,
            known_motors=known_motors,
        )

    def index(self) -> dict[ToolPath, ResolvedTool]:
        """``{path: ResolvedTool}`` for every definition, built once."""
        if self._index is None:
            from beamtimehero_cli.tool_catalog.categorize import categorize

            out: dict[ToolPath, ResolvedTool] = {}
            for tdef in self.definitions:
                name = (tdef.get("function") or {}).get("name")
                if not name:
                    continue
                tree = tuple(categorize(tdef))
                path = tree + (name,)
                out[path] = ResolvedTool(
                    path=path,
                    name=name,
                    tree=tree,
                    definition=tdef,
                    mutates=self.mutates(name),
                    handler=self.dispatch.get(path),
                    cli_name=kebab(name),
                )
            self._index = out
        return self._index

    def trees(self) -> frozenset[ToolPath]:
        """Every branch path that holds at least one tool.

        Includes nested paths (``("s3df", "psql")``). A surface's
        ``branches`` are top-level names, so the validator compares
        against ``{path[0] for path in trees()}``.
        """
        return frozenset(tool.tree for tool in self.index().values())

    def mutates(self, name: str) -> bool:
        """Whether the tool called ``name`` mutates the beamline.

        Keyed by name because ``TOOL_LINEAGE`` is, and that is a real
        constraint rather than a convenience: two tools sharing a name
        share one lineage entry, so a consumer cannot declare
        ``spec-file/list_scans`` non-mutating and ``s3df/list_scans``
        mutating. Today no shared name mutates (``tests/
        test_agent_surface.py`` asserts it), so the name-keyed write
        filter can never half-apply to one path of a pair. If that ever
        changes, this method — and ``write_tools`` — have to become
        path-keyed together.
        """
        entry = self.lineage.get(name) or {}
        return bool(entry.get("mutates"))


__all__ = ["Catalogue", "ResolvedTool", "kebab"]
