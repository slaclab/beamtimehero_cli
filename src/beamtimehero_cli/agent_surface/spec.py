"""What a restricted agent surface *is* — declared, not assembled.

An ``AgentSurface`` is the whole of what a consumer used to write three
times over: which branches an agent may see, which mutating tools it may
call, which motors it may address, whether its branch may coexist with the
canonical trees. Before this existed, `autonomous` kept the write set in
``agent_roles.py``, the motor set in the same dict but enforced in
``scripts/beamtimehero``, and the shell permission in a markdown
front-matter line that no test read. Three spellings of one fact, agreeing
by hand.

Two kinds of check live here and they are deliberately separated:

*Structural* checks need no catalogue — the name rule, "aliases only make
sense on a flat layout", "a tool path has at least a tree and a name".
They run on construction, always, and surface as a pydantic
``ValidationError``.

*Catalogue-dependent* checks need to know which tools exist, which of them
mutate, and (optionally) which motors the station has. They run in
:func:`beamtimehero_cli.agent_surface.build.build_surface`, or eagerly via
``AgentSurface.model_validate(data, context={"catalogue": cat})``, and
raise :class:`SurfaceError` carrying *every* problem at once. One typo in
a fifty-nine-entry tool list should not cost fifty-nine edit-run cycles.

This module is the one piece of ``agent_surface`` that is imported
eagerly by the package ``__init__``, so it may import pydantic and
``cli.trees`` (which imports nothing) and nothing else. In particular it
must never reach ``beamtimehero_cli.config``: ``chemcatal`` sets
environment variables *after* importing the surface it intends to build
and *before* the catalogue exists.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Iterable, Literal, Optional, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from beamtimehero_cli.cli.trees import RESERVED_TOP_LEVEL

#: A tool's identity: its canonical ``(tree, ..., name)`` path.
#:
#: The *name* is not the identity. ``TOOL_DEFINITIONS`` holds 132 entries
#: under 126 names — ``list_scans`` exists on both ``spec-file`` and
#: ``s3df``, and they are different tools with different backends. A
#: surface that carried names would be ambiguous by construction, so
#: everything here is keyed by path. Rendered as ``"spec-file/list_scans"``
#: wherever a human or a JSON document has to read it.
ToolPath = tuple[str, ...]

#: Matches the rendered form. Kept next to the type it parses.
_PATH_SEP = "/"

NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class SurfaceError(ValueError):
    """A surface that cannot be built, with every reason listed.

    ``ValueError`` so that a caller who only wants "it did not work"
    catches it without importing this module, and so pydantic accepts it
    from inside a validator. ``.problems`` is the part worth reading.
    """

    def __init__(self, problems: Iterable[str], prefix: str = "invalid agent surface"):
        self.problems = list(problems)
        body = "\n  ".join(self.problems)
        super().__init__(f"{prefix} ({len(self.problems)} problem(s)):\n  {body}")


def _as_path(value: Any) -> Any:
    """Accept ``"spec-file/list_scans"`` as well as ``("spec-file", ...)``.

    The serialised form is the string, so accepting it on the way in is
    what makes a manifest round-trip through ``model_validate``.
    """
    if isinstance(value, str):
        return tuple(seg for seg in value.split(_PATH_SEP) if seg)
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return value


_Path = Annotated[
    ToolPath,
    BeforeValidator(_as_path, json_schema_input_type=Union[str, tuple[str, ...]]),
]


class AgentSurface(BaseModel):
    """The declaration of one agent's CLI surface.

    Frozen and ``extra="forbid"``: a surface is a fact about a deployment
    that generated artifacts are derived from, so a typo in a field name
    must fail at import rather than silently widen (or narrow) what an
    agent can reach. A misspelled ``write_tool`` that was quietly ignored
    would read, from the outside, exactly like a working allow-list.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Top-level CLI branch name. ``beamtimehero <name> ...``.
    name: str
    #: One line of help for the branch.
    description: str = ""
    #: ``nested`` keeps the canonical trees under the branch
    #: (``beamtimehero blaligner spec-write move-motor``); ``flat`` puts
    #: one kebab leaf per tool directly under it (``chemcatal-bth cc
    #: read-scan``). Both layouts exist in production; neither is a
    #: migration of the other.
    layout: Literal["nested", "flat"] = "nested"
    #: Whole top-level trees to carry. Nested sub-branches (``s3df.psql``)
    #: follow their parent — there is no way to carry ``s3df`` without its
    #: psql leaves, and no consumer wanted one.
    branches: tuple[str, ...] = ()
    #: Individual tool paths to carry, on top of ``branches``. A frozen
    #: list: upstream adding a tool to a tree never widens a surface that
    #: enumerates its tools, which is the property chemcat's curation
    #: policy depends on.
    tools: tuple[_Path, ...] = ()
    #: Tool *names* allowed to mutate. Every other mutating tool that
    #: ``branches``/``tools`` would have carried is dropped from the
    #: surface entirely — not carried-but-refused.
    #:
    #: Names, not paths, because lineage (and therefore ``mutates``) is
    #: name-keyed. See ``Catalogue.mutates``.
    write_tools: frozenset[str] = frozenset()
    #: Motors this surface may address. Enforced in the executor, so a
    #: harness that calls the executor directly is guarded too.
    motors: frozenset[str] = frozenset()
    #: ``True`` means no canonical tree may sit beside this branch in the
    #: same parser. It does *not* mean "this branch and ref only":
    #: chemcat's parser carries ten local leaves of its own.
    exclusive: bool = False
    #: Mount ``ref`` (the bundled reference docs).
    include_ref: bool = True
    #: ``flat`` only: kebab leaf name → tool path, for the cases where
    #: ``kebab(name)`` is the wrong CLI spelling. Empty is normal.
    aliases: dict[str, _Path] = {}
    #: Recorded for the manifest and the prompt fragment. Not enforced —
    #: phase enforcement lives in the beamline's own phase machinery.
    phase: Optional[str] = None

    # -- serialisation: stable bytes, so a checked-in manifest diffs -----

    @field_serializer("tools")
    def _ser_tools(self, value: tuple[ToolPath, ...]) -> list:
        return [_PATH_SEP.join(p) for p in value]

    @field_serializer("branches")
    def _ser_branches(self, value: tuple[str, ...]) -> list:
        return list(value)

    @field_serializer("write_tools", "motors")
    def _ser_sets(self, value: frozenset[str]) -> list:
        return sorted(value)

    @field_serializer("aliases")
    def _ser_aliases(self, value: dict[str, ToolPath]) -> dict:
        return {k: _PATH_SEP.join(v) for k, v in sorted(value.items())}

    # -- structural validation (no catalogue) ---------------------------

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not NAME_RE.match(value):
            raise ValueError(
                f"surface name {value!r} must match {NAME_RE.pattern} — it becomes "
                "a CLI branch name and a shell-permission pattern"
            )
        if value in RESERVED_TOP_LEVEL:
            raise ValueError(
                f"surface name {value!r} collides with a reserved top-level name "
                f"({', '.join(sorted(RESERVED_TOP_LEVEL))}); it would shadow that "
                "branch in --help"
            )
        return value

    @model_validator(mode="after")
    def _check_structure(self, info) -> "AgentSurface":
        problems: list[str] = []

        if self.aliases and self.layout != "flat":
            problems.append(
                "aliases only apply to a flat layout: a nested surface reaches "
                "its tools through the canonical trees, so there is nothing to "
                "rename"
            )
        for path in self.tools:
            if len(path) < 2:
                problems.append(
                    f"tool path {_PATH_SEP.join(path)!r} needs at least a tree "
                    "and a name"
                )
        for alias, path in sorted(self.aliases.items()):
            if len(path) < 2:
                problems.append(
                    f"alias {alias!r} points at {_PATH_SEP.join(path)!r}, which "
                    "needs at least a tree and a name"
                )
        if problems:
            raise SurfaceError(problems, f"invalid agent surface {self.name!r}")

        catalogue = (info.context or {}).get("catalogue") if info.context else None
        if catalogue is not None:
            problems = check_against_catalogue(self, catalogue)
            if problems:
                raise SurfaceError(problems, f"invalid agent surface {self.name!r}")
        return self

    @classmethod
    def model_validate(cls, obj, **kwargs) -> "AgentSurface":
        """As pydantic's, except that a :class:`SurfaceError` stays one.

        pydantic converts any ``ValueError`` raised inside a validator
        into a ``ValidationError``, which would drop ``.problems`` — the
        whole point of collecting them. The original exception survives
        in the error's ``ctx``, so unwrap it here and the two entry
        points (this and ``build_surface``) raise the same thing.
        """
        try:
            return super().model_validate(obj, **kwargs)
        except ValidationError as exc:
            for err in exc.errors():
                original = (err.get("ctx") or {}).get("error")
                if isinstance(original, SurfaceError):
                    raise original from None
            raise


def check_against_catalogue(spec: AgentSurface, catalogue) -> list[str]:
    """Every way ``spec`` disagrees with ``catalogue``, in one list.

    Pure: returns problems, raises nothing. ``build_surface`` and the
    ``context={"catalogue": ...}`` validator both call it, so there is
    exactly one definition of "this surface is buildable".

    ``catalogue.known_motors is None`` means no motor source was supplied.
    The motor names are then recorded and not checked — there is no
    enumerable motor list in this library (``get_motor_config()`` returns
    raw text from a file that exists only on the beamline host), so
    demanding one would make every off-beamline build fail. The manifest
    records which of the two happened as ``motors_validated``.
    """
    from beamtimehero_cli.agent_surface import compose, guard

    problems: list[str] = []
    index = catalogue.index()

    known_trees = {path[0] for path in catalogue.trees()}
    for branch in spec.branches:
        if branch not in known_trees:
            problems.append(
                f"unknown branch {branch!r} (known: {', '.join(sorted(known_trees))})"
            )

    for path in spec.tools:
        if tuple(path) not in index:
            problems.append(f"unknown tool path {_PATH_SEP.join(path)!r}")
    for alias, path in sorted(spec.aliases.items()):
        if tuple(path) not in index:
            problems.append(
                f"alias {alias!r} points at unknown tool path {_PATH_SEP.join(path)!r}"
            )

    everything = compose.combine(catalogue)
    selected = compose.select(
        everything,
        branches=frozenset(spec.branches),
        explicit=frozenset(tuple(p) for p in spec.tools),
    )
    carried_names = {tool.name for tool in selected}
    all_names = {tool.name for tool in everything}

    for name in sorted(spec.write_tools):
        if name not in all_names:
            problems.append(f"write tool {name!r} is not a tool in this catalogue")
            continue
        if not catalogue.mutates(name):
            problems.append(
                f"write tool {name!r} does not mutate — listing it grants nothing "
                "and hides the fact that it is not audited"
            )
        if name not in carried_names:
            problems.append(
                f"write tool {name!r} is not carried by this surface (add its tree "
                "to branches, or its path to tools)"
            )

    kept = compose.filter_writes(selected, frozenset(spec.write_tools))

    if catalogue.known_motors is not None:
        for motor in sorted(spec.motors):
            if motor not in catalogue.known_motors:
                problems.append(f"unknown motor {motor!r}")

    if not spec.motors:
        for tool in kept:
            keys = guard.motor_arg_keys_of(tool.definition)
            if keys:
                problems.append(
                    f"carries {_PATH_SEP.join(tool.path)!r}, which takes "
                    f"{', '.join(keys)}, but declares no motors — an empty motor "
                    "set refuses every move, which is a surface nobody can use"
                )

    if spec.layout == "flat":
        by_cli: dict[str, list[str]] = {}
        for tool in compose.rename(kept, layout="flat", aliases=spec.aliases):
            by_cli.setdefault(tool.cli_name, []).append(_PATH_SEP.join(tool.path))
        for cli_name, paths in sorted(by_cli.items()):
            if len(paths) > 1:
                problems.append(
                    f"flat leaf {cli_name!r} would be built twice, from "
                    f"{' and '.join(sorted(paths))} — give one of them an alias"
                )

    return problems


__all__ = [
    "NAME_RE",
    "AgentSurface",
    "SurfaceError",
    "ToolPath",
    "check_against_catalogue",
]
