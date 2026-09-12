"""One call in, six artifacts out.

``build_surface(spec)`` is the composition point. Everything a consumer
used to hand-write and hand-synchronise — the parser branch, the schema
export, the restricted dispatch table, the guarded executor, the shell
permission pattern, the prompt paragraph listing motors and write tools —
is derived here from the single declaration in ``spec``. That is the
whole claim of the design: there is one place where "what this agent may
do" is written down, and the six things that have to agree with it are
generated rather than transcribed.

``build_surface`` never reads ``PROFILES``. The old profile registry is a
global dict that consumers mutated (delete-then-restore in a ``finally``)
to keep the library's built-in profiles out of an agent's parser. A
surface takes its tools from its own frozen spec, so there is nothing
global to hide and nothing to restore.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from beamtimehero_cli.agent_surface import compose
from beamtimehero_cli.agent_surface.catalogue import Catalogue, ResolvedTool
from beamtimehero_cli.agent_surface.spec import (
    AgentSurface,
    SurfaceError,
    ToolPath,
    check_against_catalogue,
)
from beamtimehero_cli.agent_surface.snapshot import snapshot_parser

#: The fence a generated prompt fragment is pasted between. Exported so
#: every consumer's render script spells it the same way — a marker that
#: differs by one character is a block that silently never updates.
MARKER_BEGIN = "<!-- surface:begin -->"
MARKER_END = "<!-- surface:end -->"

#: The manifest format. Bump it when a reader would have to change.
MANIFEST_VERSION = 1


def build_surface(
    spec: AgentSurface,
    catalogue: Optional[Catalogue] = None,
    *,
    before: Iterable[Callable] = (),
    after: Iterable[Callable] = (),
) -> "SurfaceBuild":
    """Resolve ``spec`` against ``catalogue`` and return the build.

    ``catalogue`` defaults to ``Catalogue.default()`` — the library's
    registries as they stand now, with no motor source, so a default
    build validates everything except motor names and says so via
    ``motors_validated``.

    ``before``/``after`` are the consumer's own executor hooks (argument
    validation, scan capture). The motor guard is installed in front of
    them by ``wrap_dispatch``; it is not optional, because a surface that
    declares motors and does not enforce them is the bug this replaces.

    Raises :class:`SurfaceError` listing every problem at once.
    """
    cat = catalogue if catalogue is not None else Catalogue.default()

    problems = check_against_catalogue(spec, cat)
    if problems:
        raise SurfaceError(problems, f"cannot build agent surface {spec.name!r}")

    everything = compose.combine(cat)
    selected = compose.select(
        everything,
        branches=frozenset(spec.branches),
        explicit=frozenset(tuple(p) for p in spec.tools),
    )
    kept = compose.filter_writes(selected, spec.write_tools)
    tools = compose.rename(kept, layout=spec.layout, aliases=spec.aliases)

    table, executor = compose.wrap_dispatch(
        tools,
        motors=spec.motors,
        surface_name=spec.name,
        before=before,
        after=after,
    )

    known_trees = frozenset({spec.name}) | (
        frozenset({"ref"}) if spec.include_ref else frozenset()
    )

    return SurfaceBuild(
        spec=spec,
        tools=tools,
        dispatch=table,
        executor=executor,
        known_trees=known_trees,
        motors_validated=cat.known_motors is not None,
    )


@dataclass
class SurfaceBuild:
    """A resolved surface, and the artifacts derived from it."""

    spec: AgentSurface
    #: The carried tools, in catalogue order, with ``cli_name`` resolved.
    tools: tuple[ResolvedTool, ...]
    #: ``{path: handler}`` — only the carried tools.
    dispatch: dict
    #: An executor over ``dispatch``, motor-guarded, plus the consumer's
    #: hooks. Hand this to ``run_tool_leaf(args, executor=...)`` or call
    #: it directly; both are guarded.
    executor: Callable
    #: Top-level names the surface's parser accepts, for
    #: ``run_with(known_trees=...)``. A consumer unions its local leaves
    #: onto this.
    known_trees: frozenset
    #: Whether ``motors`` was checked against a real motor list, or only
    #: recorded. There is no enumerable motor list off the beamline host,
    #: so ``False`` is the normal answer away from the beamline and the
    #: manifest says which happened.
    motors_validated: bool
    #: Set by ``make_parser``: the root subparsers action, so a consumer
    #: can register local leaves beside the surface branch.
    root_subparsers: Optional[Any] = field(default=None)
    #: Set by ``attach``: the surface branch's own subparsers action, so
    #: a consumer can hang a local subtree (``steering``) inside it.
    branch_subparsers: Optional[Any] = field(default=None)

    # -- 1. the parser ---------------------------------------------------

    def attach(self, subs: "argparse._SubParsersAction") -> "argparse._SubParsersAction":
        """Build the surface branch under ``subs``; return its subparsers.

        ``exclusive`` is enforced here rather than in the spec, because
        this is the first moment the question is answerable: it is about
        what else is in *this* parser. It means "no canonical tree may
        coexist", not "this branch and ``ref`` only" — the one exclusive
        consumer also carries ten local leaves of its own, and ``ref`` is
        not a tool tree.
        """
        if self.spec.exclusive:
            collision = set(subs.choices) & _canonical_trees()
            if collision:
                raise SurfaceError(
                    [
                        f"canonical tree {name!r} is already on this parser"
                        for name in sorted(collision)
                    ],
                    f"agent surface {self.spec.name!r} is exclusive",
                )

        self.branch_subparsers = compose.namespace(
            self.tools, spec=self.spec, subs=subs, agent_role=self.spec.name,
        )
        return self.branch_subparsers

    def make_parser(
        self,
        *,
        prog: str,
        description: str = "",
        parser_class=None,
    ) -> argparse.ArgumentParser:
        """A whole parser holding ``ref`` and the surface branch.

        Nothing else: the point of an exclusive surface is that the
        canonical trees are not merely filtered out of ``--help`` but
        absent from the parser, so ``chemcatal-bth spec-write ...`` is a
        parse error rather than a permission decision.

        ``root_subparsers`` is left on the build afterwards, which is how
        a consumer adds its own local leaves without this method growing
        parameters for them.

        ``parser_class`` defaults to ``ToolParser`` (the one that answers
        a parse error with a JSON envelope instead of argparse's usage
        text). It is resolved on the call rather than in the signature so
        that importing this module does not import the parser, and with
        it ``config``.
        """
        if parser_class is None:
            from beamtimehero_cli.cli.api import ToolParser

            parser_class = ToolParser

        parser = parser_class(prog=prog, description=description)
        self.root_subparsers = parser.add_subparsers(dest="tree", metavar="<tree>")
        if self.spec.include_ref:
            from beamtimehero_cli.cli.api import build_ref_subtree

            build_ref_subtree(self.root_subparsers)
        self.attach(self.root_subparsers)
        return parser

    # -- 2. the schemas --------------------------------------------------

    def schemas(self) -> list[dict]:
        """The carried definitions, in surface order, each with its path.

        ``x-cli-path`` is the invocation, not the canonical path: an
        agent harness that registers these schemas needs to know what to
        type, and on a flat surface that is ``cc read-scan`` while the
        tool is ``spec-file/read_scan``. The ``x-`` prefix is the JSON
        Schema convention for an extension keyword, so a validator
        ignores it.
        """
        return [
            {**tool.definition, "x-cli-path": self.cli_path(tool)}
            for tool in self.tools
        ]

    # -- 3. dispatch / executor are attributes ---------------------------

    # -- 4. the shell permission -----------------------------------------

    def bash_pattern(self, prog: str = "beamtimehero") -> str:
        """The claude-code permission line for this surface.

        Colon form. The two spellings in use — ``Bash(beamtimehero
        blaligner:*)`` and ``Bash(beamtimehero blaligner *)`` — were
        split across six agent files with no rule about which, and one
        test hardcoded the space form. One spelling, generated.
        """
        return f"Bash({prog} {self.spec.name}:*)"

    # -- 5. the prompt fragment ------------------------------------------

    def prompt_fragment(self, prog: str = "beamtimehero") -> str:
        """The markdown an agent's prompt pastes between the markers.

        Sorted throughout, so regenerating it after an unrelated
        catalogue change produces no diff. Write tools are listed
        separately from reads because that is the distinction the agent
        has to act on: those are the calls that need a justification and
        end up in the audit log.
        """
        spec = self.spec
        lines = [f"## `{prog} {spec.name}`", ""]
        if spec.description:
            lines += [spec.description.strip(), ""]
        if spec.layout == "nested":
            lines.append(
                f"Invoke every tool as `{prog} {spec.name} <tree> <command>`; "
                "append `--help` at any depth to discover arguments."
            )
        else:
            lines.append(
                f"Invoke every tool as `{prog} {spec.name} <command>`; "
                "append `--help` to discover arguments."
            )
        lines.append("")
        if spec.phase:
            lines += [f"Phase: `{spec.phase}`", ""]
        if spec.motors:
            lines += [
                "Allowed motors: " + ", ".join(f"`{m}`" for m in sorted(spec.motors)),
                "",
            ]
        else:
            lines += ["No motors are allowed on this surface.", ""]

        writes = sorted(
            (tool for tool in self.tools if tool.mutates),
            key=lambda t: self.cli_path(t),
        )
        reads = [tool for tool in self.tools if not tool.mutates]

        lines.append("### Write tools")
        lines.append("")
        if writes:
            lines.append(
                "Each requires `--justification` and is recorded in the action log."
            )
            lines.append("")
            lines += [self._bullet(tool, prog) for tool in writes]
        else:
            lines.append("None — this surface cannot mutate the beamline.")
        lines.append("")

        lines.append("### Read tools")
        lines.append("")
        by_tree: dict[ToolPath, list[ResolvedTool]] = {}
        for tool in reads:
            by_tree.setdefault(tool.tree, []).append(tool)
        for tree in sorted(by_tree):
            lines.append(f"**{'/'.join(tree)}**")
            lines.append("")
            for tool in sorted(by_tree[tree], key=lambda t: self.cli_path(t)):
                lines.append(self._bullet(tool, prog))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _bullet(self, tool: ResolvedTool, prog: str) -> str:
        return f"- `{prog} {self.cli_path(tool)}` — {_summary(tool.definition)}"

    # -- 6. the manifest --------------------------------------------------

    def manifest(self) -> dict:
        """The whole surface as JSON: the declaration and what it resolved to.

        Checked in beside the consumer's spec and asserted equal by a
        test, so the acceptance rule for a migration ("the generated
        surface equals the current one") has a value to compare and a
        later widening of an agent's reach shows up as a reviewable diff
        rather than as behaviour.

        Deterministic by construction: sorted leaves, sorted sets, sorted
        argument rows, paths rendered as strings.
        """
        return {
            "manifest_version": MANIFEST_VERSION,
            "surface": self.spec.model_dump(mode="json"),
            "resolved": {
                "leaves": sorted(
                    (
                        {
                            "cli_path": self.cli_path(tool),
                            "tool": "/".join(tool.path),
                            "mutates": tool.mutates,
                            "args": _arg_rows(tool.definition),
                        }
                        for tool in self.tools
                    ),
                    key=lambda leaf: leaf["cli_path"],
                ),
                "known_trees": sorted(self.known_trees),
            },
            "bash_pattern": self.bash_pattern(),
            "motors_validated": self.motors_validated,
        }

    # -- dispatch helpers ------------------------------------------------

    def cli_path(self, tool: ResolvedTool) -> str:
        """What a caller types, minus the program name."""
        if self.spec.layout == "nested":
            return " ".join((self.spec.name,) + tool.tree + (tool.cli_name,))
        return f"{self.spec.name} {tool.cli_name}"

    def claims(self, args: argparse.Namespace) -> bool:
        """Whether this surface owns the branch the args landed on."""
        return getattr(args, "tree", None) == self.spec.name

    def subtree_of(self, args: argparse.Namespace) -> Optional[str]:
        """The nested branch the args landed on (``steering``, ``spec-write``).

        ``None`` on a flat surface, which has no second level.
        """
        return getattr(args, "subtree", None)

    def run_leaf(self, args: argparse.Namespace) -> int:
        """Run a catalog leaf through *this surface's* executor."""
        from beamtimehero_cli.cli.api import run_tool_leaf

        return run_tool_leaf(args, executor=self.executor)

    def run(self, parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
        """Dispatch anything the upstream dispatcher knows how to handle."""
        from beamtimehero_cli.cli.api import dispatch

        return dispatch(parser, args, executor=self.executor)


def _canonical_trees() -> frozenset:
    from beamtimehero_cli.cli.trees import CANONICAL_TREES

    return CANONICAL_TREES


def _summary(definition: dict) -> str:
    """First sentence of a tool description, on one line."""
    text = " ".join(((definition.get("function") or {}).get("description") or "").split())
    if not text:
        return ""
    head, sep, _rest = text.partition(". ")
    return head + ("." if sep else "")


def _arg_rows(definition: dict) -> list[dict]:
    """The leaf's flags, as argparse will actually build them.

    Built by running ``add_arg`` over the schema into a throwaway parser
    and reading it back with ``snapshot_parser``, rather than by
    re-deriving the JSON-schema-to-flag mapping. A second copy of that
    mapping would be a second thing to keep in step with ``add_arg``, and
    the manifest's job is to record what the CLI does.
    """
    fn = definition.get("function") or {}
    params = fn.get("parameters") or {}
    properties = params.get("properties") or {}
    required = set(params.get("required") or [])

    from beamtimehero_cli.cli.api import add_arg

    probe = argparse.ArgumentParser(add_help=False)
    for key, prop in properties.items():
        add_arg(probe, key, prop or {}, key in required)

    return [
        {
            "flag": opt["flags"][0],
            "dest": opt["dest"],
            "required": opt["required"],
            "type": opt["type"],
            "choices": opt["choices"],
            "default": opt["default"],
        }
        for opt in snapshot_parser(probe)["options"]
    ]


__all__ = [
    "MANIFEST_VERSION",
    "MARKER_BEGIN",
    "MARKER_END",
    "SurfaceBuild",
    "build_surface",
]
