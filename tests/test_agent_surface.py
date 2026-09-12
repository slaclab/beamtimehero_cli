"""The agent-surface API: validation, parity, artifacts, invariants.

Four kinds of test live here, and they are testing four different
promises.

*Validation* — a surface that disagrees with the catalogue must be
refused, and refused with every reason at once. The failure mode being
avoided is not "it crashes"; it is a `write_tools` entry that was
silently ignored, which from the outside is indistinguishable from an
allow-list that works.

*Parity* — what `build_surface` generates must be byte-identical to what
the consumers hand-build today, because that is the acceptance rule for
deleting their code. Both parity tests compute the "old" side
independently (the old write-filter rule, the old profile builder) rather
than calling the new code twice.

*Artifacts* — the six derived things, each asserted for the property that
made it worth generating: the permission pattern for its spelling, the
prompt for its sort order, the manifest for determinism and round-trip.

*Invariants* — the facts the design rests on. That no tool name existing
on two trees is mutating, so a name-keyed write filter cannot half-apply
to one path of a pair. That no `motor*` argument escapes the guard's key
tuple. That importing the package does not import `config`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from beamtimehero_cli.agent_surface import (
    MOTOR_ARG_KEYS,
    AgentSurface,
    Catalogue,
    SurfaceError,
    build_surface,
)
from beamtimehero_cli.agent_surface.snapshot import snapshot_parser, strip_keys
from beamtimehero_cli.cli.api import (
    ToolParser,
    build_catalog_subtrees,
    build_ref_subtree,
    run_tool_leaf,
)
# The one import in this file that does not come from `cli.api`.
# `build_profile_subtrees` is the mechanism a flat surface replaces, so it is
# deliberately absent from the supported surface; the parity test below needs
# it to compute the "old" side, and nothing else should.
from beamtimehero_cli.cli.__main__ import build_profile_subtrees
from beamtimehero_cli.cli.trees import CANONICAL_TREES, TREE_HELPS
from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Every canonical tree, which is what `build_catalog_subtrees` pre-creates
#: under a role branch today — so a nested surface declaring all nine is
#: the one that has to come out byte-identical.
ALL_BRANCHES = (
    "tool", "db", "spec-read", "spec-write", "spec-file",
    "s3df", "slack", "xrs", "exafs",
)


@pytest.fixture(scope="module")
def catalogue() -> Catalogue:
    return Catalogue.default()


@pytest.fixture(scope="module")
def mock_catalogue() -> Catalogue:
    """A catalogue with a motor source, so motor names are checked."""
    from beamtimehero_cli.spec_data.spec_config import mock_motors

    return Catalogue.default(known_motors=mock_motors())


def _aligner(**overrides) -> AgentSurface:
    """The shape `autonomous` uses: whole trees, two writes, three motors."""
    kwargs = dict(
        name="blaligner",
        description="Agent scope: blaligner.",
        layout="nested",
        branches=ALL_BRANCHES,
        write_tools={"move_motor", "run_motor_scan"},
        motors={"Sx", "Sy", "Sz"},
        phase="sample_alignment",
    )
    kwargs.update(overrides)
    return AgentSurface(**kwargs)


def _root() -> tuple[ToolParser, argparse._SubParsersAction]:
    parser = ToolParser(prog="p")
    return parser, parser.add_subparsers(dest="tree", metavar="<tree>")


# ---------------------------------------------------------------------------
# Structural validation — no catalogue needed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(CANONICAL_TREES | {"ref", "catalog"}))
def test_surface_name_may_not_shadow_a_reserved_branch(name):
    with pytest.raises(Exception) as exc:
        AgentSurface(name=name)
    assert "reserved" in str(exc.value)


@pytest.mark.parametrize("name", ["Blaligner", "bl_aligner", "9aligner", "", "bl aligner"])
def test_surface_name_must_be_a_cli_word(name):
    with pytest.raises(Exception) as exc:
        AgentSurface(name=name)
    assert "must match" in str(exc.value)


def test_aliases_require_a_flat_layout():
    with pytest.raises(Exception) as exc:
        AgentSurface(
            name="zz", layout="nested",
            aliases={"read-scan": ("spec-file", "read_scan")},
        )
    assert "flat layout" in str(exc.value)


def test_a_tool_path_needs_a_tree_and_a_name():
    with pytest.raises(Exception) as exc:
        AgentSurface(name="zz", tools=("read_scan",))
    assert "at least a tree and a name" in str(exc.value)


def test_unknown_field_is_refused():
    with pytest.raises(Exception) as exc:
        AgentSurface(name="zz", write_tool={"move_motor"})
    assert "write_tool" in str(exc.value)


def test_spec_is_frozen():
    spec = _aligner()
    with pytest.raises(Exception):
        spec.name = "other"


# ---------------------------------------------------------------------------
# Catalogue validation — every problem in one refusal
# ---------------------------------------------------------------------------

def test_unknown_paths_are_all_listed_at_once(catalogue):
    spec = AgentSurface(
        name="zz",
        branches=("tool", "nosuchtree"),
        tools=("spec-file/no_such_tool", "nosuchtree/other", "tool/read_file"),
    )
    with pytest.raises(SurfaceError) as exc:
        build_surface(spec, catalogue)

    problems = exc.value.problems
    assert any("nosuchtree" in p and "branch" in p for p in problems)
    assert any("spec-file/no_such_tool" in p for p in problems)
    assert any("nosuchtree/other" in p for p in problems)
    # The point of collecting: one run reports all three, and says nothing
    # about the path that is fine.
    assert len(problems) == 3
    assert not any("tool/read_file" in p for p in problems)


def test_write_tool_must_exist_must_mutate_and_must_be_carried(catalogue):
    spec = AgentSurface(
        name="zz",
        branches=("spec-read",),
        write_tools={"no_such_tool", "read_motor_position", "move_motor"},
    )
    with pytest.raises(SurfaceError) as exc:
        build_surface(spec, catalogue)

    problems = "\n".join(exc.value.problems)
    assert "'no_such_tool' is not a tool in this catalogue" in problems
    assert "'read_motor_position' does not mutate" in problems
    assert "'move_motor' is not carried by this surface" in problems


def test_unknown_motor_is_refused_when_a_source_was_given(mock_catalogue):
    with pytest.raises(SurfaceError) as exc:
        build_surface(_aligner(motors={"Sx", "no_such_motor"}), mock_catalogue)
    assert exc.value.problems == ["unknown motor 'no_such_motor'"]


def test_unknown_motor_is_recorded_not_refused_without_a_source(catalogue):
    """Off the beamline host there is no motor list, so this must build.

    The library cannot enumerate motors: `get_motor_config()` returns the
    raw text of a file that exists only on the beamline host. Refusing
    would make every surface unbuildable in CI; silently claiming the
    names were checked would be a lie. So it builds and says which
    happened.
    """
    assert catalogue.known_motors is None
    build = build_surface(_aligner(motors={"Sx", "no_such_motor"}), catalogue)
    assert build.motors_validated is False
    assert build.manifest()["motors_validated"] is False


def test_motors_validated_is_true_when_the_source_agrees(mock_catalogue):
    build = build_surface(_aligner(), mock_catalogue)
    assert build.motors_validated is True
    assert build.manifest()["motors_validated"] is True


def test_carrying_a_motor_tool_with_no_motors_is_refused(catalogue):
    spec = AgentSurface(
        name="zz", tools=("spec-read/read_motor_position",), motors=frozenset(),
    )
    with pytest.raises(SurfaceError) as exc:
        build_surface(spec, catalogue)
    assert "declares no motors" in exc.value.problems[0]
    assert "spec-read/read_motor_position" in exc.value.problems[0]


def test_a_dropped_write_tool_does_not_demand_motors(catalogue):
    """`move_motor` takes a motor, but an unlisted write tool is gone.

    The motor check runs after the write filter for exactly this reason:
    a read-only surface that carries the `spec-write` branch carries none
    of it, so demanding a motor set would be demanding one for tools the
    agent cannot see.
    """
    build = build_surface(
        AgentSurface(name="zz", branches=("spec-write",), write_tools=frozenset()),
        catalogue,
    )
    assert build.tools == ()


def test_flat_leaf_collision_is_refused(catalogue):
    spec = AgentSurface(
        name="zz", layout="flat",
        tools=("spec-file/list_scans", "s3df/list_scans"),
    )
    with pytest.raises(SurfaceError) as exc:
        build_surface(spec, catalogue)
    assert "'list-scans' would be built twice" in exc.value.problems[0]


def test_an_alias_resolves_the_flat_collision(catalogue):
    spec = AgentSurface(
        name="zz", layout="flat",
        tools=("spec-file/list_scans", "s3df/list_scans"),
        aliases={"list-scans-s3df": ("s3df", "list_scans")},
    )
    build = build_surface(spec, catalogue)
    assert sorted(t.cli_name for t in build.tools) == ["list-scans", "list-scans-s3df"]


def test_validation_through_model_validate_keeps_the_problem_list(catalogue):
    """The context route raises the same thing `build_surface` does."""
    with pytest.raises(SurfaceError) as exc:
        AgentSurface.model_validate(
            {"name": "zz", "tools": ["spec-file/no_such_tool"]},
            context={"catalogue": catalogue},
        )
    assert exc.value.problems == ["unknown tool path 'spec-file/no_such_tool'"]


# ---------------------------------------------------------------------------
# Selection and the write filter
# ---------------------------------------------------------------------------

def test_write_filter_drops_move_motor_when_unlisted(catalogue):
    carried = {"/".join(t.path) for t in build_surface(_aligner(), catalogue).tools}
    assert "spec-write/move_motor" in carried
    assert "spec-write/run_motor_scan" in carried
    assert "spec-write/move_motor_relative" not in carried

    without = build_surface(_aligner(write_tools=frozenset()), catalogue)
    assert not any(t.mutates for t in without.tools)
    assert all(t.tree[0] != "spec-write" for t in without.tools)


def test_the_write_filter_is_the_only_thing_that_drops_anything(catalogue):
    """Carrying every branch carries everything but the unlisted writes."""
    build = build_surface(_aligner(), catalogue)
    mutating = sum(1 for t in build.tools if t.mutates)
    assert mutating == 2
    assert len(build.tools) == len(TOOL_DEFINITIONS) - 40 + 2


def test_a_branch_carries_its_nested_children(catalogue):
    build = build_surface(
        AgentSurface(name="zz", branches=("s3df",)), catalogue,
    )
    trees = {t.tree for t in build.tools}
    assert ("s3df",) in trees and ("s3df", "psql") in trees


def test_explicit_tools_are_not_widened_by_a_branch(catalogue):
    build = build_surface(
        AgentSurface(name="zz", tools=("tool/read_file", "tool/list_files")),
        catalogue,
    )
    assert {"/".join(t.path) for t in build.tools} == {
        "tool/read_file", "tool/list_files",
    }


# ---------------------------------------------------------------------------
# Parity: the generated branch equals the hand-built one
# ---------------------------------------------------------------------------

def test_nested_attach_equals_build_catalog_subtrees_filtered(catalogue):
    """The acceptance rule for deleting `_build_role_branch`.

    The "old" side is computed the old way on purpose: the write filter
    is the pre-`mutates` rule (does the schema require `justification`),
    and `trees` is left at its default so the generated side has to
    derive the same full set from `branches`.
    """
    spec = _aligner()

    def old_filter(tdef: dict) -> bool:
        fn = tdef.get("function") or {}
        required = set(((fn.get("parameters") or {}).get("required")) or [])
        if "justification" not in required:
            return True
        return fn.get("name") in spec.write_tools

    hand_parser, hand_subs = _root()
    branch = hand_subs.add_parser(spec.name, help=spec.description)
    bsubs = branch.add_subparsers(dest="subtree", metavar="<tree>")
    build_ref_subtree(bsubs)
    build_catalog_subtrees(
        bsubs,
        [t for t in TOOL_DEFINITIONS if old_filter(t)],
        agent_role=spec.name,
    )

    gen_parser, gen_subs = _root()
    build_surface(spec, catalogue).attach(gen_subs)

    assert snapshot_parser(gen_parser) == snapshot_parser(hand_parser)


def test_flat_attach_equals_build_profile_subtrees(catalogue):
    """The acceptance rule for deleting `CC_PROFILE`.

    `_agent_role` is stripped: the old profile builder stamped no role at
    all, and stamping every leaf is the fix rather than a difference to
    preserve. It is asserted separately below.
    """
    from beamtimehero_cli.cli.profiles import PROFILES, register_profile

    aliases = {
        "read-scan": ("spec-file", "read_scan"),
        "list-scans": ("spec-file", "list_scans"),
        "plot-data": ("tool", "plot_data"),
        "search-logs": ("tool", "search_logs"),
    }
    description = "Curated flat surface."

    register_profile({
        "name": "cc-parity", "description": description, "aliases": aliases,
    })
    try:
        hand_parser, hand_subs = _root()
        build_profile_subtrees(hand_subs, TOOL_DEFINITIONS)
    finally:
        del PROFILES["cc-parity"]

    spec = AgentSurface(
        name="cc-parity", description=description, layout="flat",
        tools=tuple(aliases.values()),
    )
    gen_parser, gen_subs = _root()
    build_surface(spec, catalogue).attach(gen_subs)

    hand = strip_keys(snapshot_parser(hand_parser)["children"]["cc-parity"], ["_agent_role"])
    gen = strip_keys(snapshot_parser(gen_parser)["children"]["cc-parity"], ["_agent_role"])
    assert gen == hand


def test_every_leaf_carries_the_agent_role(catalogue):
    """The gap this closes: four of nine branches used to be stamped."""
    parser, subs = _root()
    build_surface(_aligner(), catalogue).attach(subs)

    def leaves(node: dict):
        if not node["children"]:
            yield node
        for child in node["children"].values():
            yield from leaves(child)

    stamped = [
        leaf for leaf in leaves(snapshot_parser(parser))
        if leaf["defaults"].get("_tool_name")
    ]
    assert len(stamped) == 93
    assert all(leaf["defaults"]["_agent_role"] == "blaligner" for leaf in stamped)


# ---------------------------------------------------------------------------
# The parser artifacts
# ---------------------------------------------------------------------------

def test_exclusive_refuses_to_sit_beside_a_canonical_tree(catalogue):
    build = build_surface(
        AgentSurface(name="cc", layout="flat", exclusive=True,
                     tools=("tool/read_file",)),
        catalogue,
    )
    _parser, subs = _root()
    build_catalog_subtrees(subs, [])          # pre-creates the canonical trees
    with pytest.raises(SurfaceError) as exc:
        build.attach(subs)
    assert any("spec-write" in p for p in exc.value.problems)


def test_exclusive_tolerates_ref_and_local_leaves(catalogue):
    """Exclusive is about tool trees, not about being alone.

    The one exclusive consumer carries ten local leaves and `ref`; if
    exclusivity meant "this branch only" it would have had to opt out of
    the check it exists for.
    """
    build = build_surface(
        AgentSurface(name="cc", layout="flat", exclusive=True,
                     tools=("tool/read_file",)),
        catalogue,
    )
    _parser, subs = _root()
    build_ref_subtree(subs)
    subs.add_parser("list-beamtimes", help="local leaf")
    build.attach(subs)
    assert "cc" in subs.choices


def test_make_parser_holds_only_ref_and_the_branch(catalogue):
    build = build_surface(
        AgentSurface(name="cc", layout="flat", exclusive=True,
                     tools=("spec-file/read_scan",)),
        catalogue,
    )
    parser = build.make_parser(prog="chemcatal-bth", description="d")
    assert set(snapshot_parser(parser)["children"]) == {"ref", "cc"}
    assert build.root_subparsers is not None
    assert build.branch_subparsers is not None

    args = parser.parse_args(["cc", "read-scan", "--file-name", "f", "--scan-number", "1"])
    assert args._tool_name == "read_scan"
    assert args._tool_category == ("spec-file",)


def test_make_parser_without_ref(catalogue):
    build = build_surface(
        AgentSurface(name="cc", layout="flat", include_ref=False,
                     tools=("spec-file/read_scan",)),
        catalogue,
    )
    parser = build.make_parser(prog="p")
    assert set(snapshot_parser(parser)["children"]) == {"cc"}
    assert build.known_trees == frozenset({"cc"})


def test_a_nested_surface_precreates_only_its_own_branches(catalogue):
    """`trees` is derived from the spec, not left at the full default.

    Empty canonical branches used to be pre-created under every role, so
    `blaligner --help` advertised `slack` and `s3df` with nothing under
    them. A surface that declares two branches shows two.
    """
    build = build_surface(
        _aligner(branches=("spec-read", "spec-file"), write_tools=frozenset()),
        catalogue,
    )
    _parser, subs = _root()
    bsubs = build.attach(subs)
    assert set(bsubs.choices) == {"ref", "spec-read", "spec-file"}
    assert {path[0] for path in TREE_HELPS} - set(bsubs.choices) == {
        "tool", "db", "spec-write", "s3df", "slack", "xrs", "exafs",
    }


def test_attach_returns_the_branch_subparsers_for_a_local_subtree(catalogue):
    """What `autonomous` needs to keep hanging `steering` off the role."""
    _parser, subs = _root()
    bsubs = build_surface(_aligner(), catalogue).attach(subs)
    bsubs.add_parser("steering", help="local subtree")
    assert "steering" in bsubs.choices
    assert "spec-write" in bsubs.choices
    assert "ref" in bsubs.choices


# ---------------------------------------------------------------------------
# Dispatch and the guard
# ---------------------------------------------------------------------------

def test_restricted_dispatch_holds_exactly_the_carried_paths(catalogue):
    build = build_surface(_aligner(), catalogue)
    assert set(build.dispatch) == {t.path for t in build.tools}
    assert ("spec-write", "move_motor") in build.dispatch
    assert ("spec-write", "move_motor_relative") not in build.dispatch


def test_executor_answers_an_uncarried_tool_as_unknown(catalogue):
    build = build_surface(_aligner(), catalogue)
    text, images = build.executor(("spec-write",), "move_motor_relative", {})
    assert text == "Unknown tool: spec-write/move_motor_relative"
    assert images == []


def test_executor_refuses_a_motor_outside_the_set(catalogue):
    build = build_surface(_aligner(), catalogue)
    text, images = build.executor(
        ("spec-write",), "move_motor",
        {"motor": "energy", "position": 7100.0, "justification": "test"},
    )
    assert images == []
    assert json.loads(text) == {
        "ok": False,
        "error": "motor 'energy' not in blaligner allowed set",
        "agent_role": "blaligner",
        "allowed_motors": ["Sx", "Sy", "Sz"],
    }


def test_the_guard_runs_before_the_consumers_own_hooks(catalogue):
    """A refused move must not reach argument validation or the handler."""
    seen: list[tuple] = []
    build = build_surface(
        _aligner(), catalogue,
        before=(lambda tree, name, args: seen.append((tree, name)) or None,),
    )
    build.executor(("spec-write",), "move_motor", {"motor": "energy"})
    assert seen == []
    build.executor(("spec-write",), "move_motor", {"motor": "Sx"})
    assert seen == [(("spec-write",), "move_motor")]


def test_every_motor_argument_in_the_catalogue_is_guarded():
    """A new `motor*` property outside this tuple would be unguarded."""
    found: set[str] = set()
    for tdef in TOOL_DEFINITIONS:
        params = ((tdef.get("function") or {}).get("parameters")) or {}
        found |= {k for k in (params.get("properties") or {}) if k.startswith("motor")}
    assert found == set(MOTOR_ARG_KEYS), (
        "a motor argument exists that guard.MOTOR_ARG_KEYS does not name; "
        "add it there or the allow-list silently stops covering it"
    )


def test_run_leaf_payload_excludes_parser_bookkeeping(catalogue):
    """`subtree` and every `_`-prefixed default are not tool arguments."""
    build = build_surface(_aligner(), catalogue)
    _parser, subs = _root()
    parser = ToolParser(prog="p")
    root = parser.add_subparsers(dest="tree", metavar="<tree>")
    build.attach(root)
    args = parser.parse_args(
        ["blaligner", "spec-write", "move-motor",
         "--motor", "Sx", "--position", "1.5", "--justification", "why"]
    )

    captured: dict = {}

    def capture(tree, name, payload):
        captured["tree"] = tree
        captured["name"] = name
        captured["payload"] = payload
        return "ok", []

    assert run_tool_leaf(args, executor=capture) == 0
    assert captured["tree"] == ("spec-write",)
    assert captured["name"] == "move_motor"
    assert captured["payload"] == {
        "motor": "Sx", "position": 1.5, "justification": "why",
    }


def test_run_dispatches_ref_and_leaves_through_the_surface_executor(catalogue, capsys):
    build = build_surface(_aligner(), catalogue)
    parser = build.make_parser(prog="beamtimehero")
    args = parser.parse_args(["ref", "--list"])
    assert build.run(parser, args) == 0
    assert "agent-surfaces" in capsys.readouterr().out


def test_subtree_of_and_claims(catalogue):
    build = build_surface(_aligner(), catalogue)
    parser = build.make_parser(prog="beamtimehero")
    args = parser.parse_args(["blaligner", "spec-read", "get-beam-status"])
    assert build.claims(args) is True
    assert build.subtree_of(args) == "spec-read"
    assert build.claims(parser.parse_args(["ref", "--list"])) is False


# ---------------------------------------------------------------------------
# The generated artifacts
# ---------------------------------------------------------------------------

def test_bash_pattern_is_the_colon_form(catalogue):
    build = build_surface(_aligner(), catalogue)
    assert build.bash_pattern() == "Bash(beamtimehero blaligner:*)"
    assert build.bash_pattern(prog="chemcatal-bth") == "Bash(chemcatal-bth blaligner:*)"


def test_schemas_are_the_carried_definitions_with_their_cli_path(catalogue):
    build = build_surface(_aligner(), catalogue)
    schemas = build.schemas()
    assert len(schemas) == len(build.tools)
    by_name = {s["function"]["name"]: s for s in schemas}
    assert by_name["move_motor"]["x-cli-path"] == "blaligner spec-write move-motor"
    assert "move_motor_relative" not in by_name
    # The catalogue's own definitions must not have gained a key.
    assert all("x-cli-path" not in t.definition for t in build.tools)


def test_prompt_fragment_is_sorted_and_splits_writes_from_reads(catalogue):
    text = build_surface(_aligner(), catalogue).prompt_fragment()

    assert "Allowed motors: `Sx`, `Sy`, `Sz`" in text
    assert "Phase: `sample_alignment`" in text
    assert "beamtimehero blaligner spec-write move-motor" in text
    assert "move-motor-relative" not in text

    writes, _, reads = text.partition("### Read tools")
    assert "spec-write move-motor" in writes
    # Tree headings, and the bullets under each, in sorted order.
    headings = [line for line in reads.splitlines() if line.startswith("**")]
    assert headings == sorted(headings)
    for block in reads.split("**")[1:]:
        # Sorted by the command, not by the whole line: `plot-scan` sorts
        # before `plot-scan-stack`, but the descriptions that follow them do
        # not, and it is the command order a reader scans.
        commands = [
            line.split("`")[1]
            for line in block.splitlines() if line.startswith("- ")
        ]
        assert commands == sorted(commands)


def test_prompt_fragment_says_so_when_nothing_can_mutate(catalogue):
    text = build_surface(
        AgentSurface(name="cc", layout="flat", tools=("tool/read_file",)),
        catalogue,
    ).prompt_fragment(prog="chemcatal-bth")
    assert "cannot mutate the beamline" in text
    assert "No motors are allowed" in text
    assert "`chemcatal-bth cc read-file`" in text


def test_manifest_round_trips_the_spec(catalogue):
    spec = _aligner()
    manifest = build_surface(spec, catalogue).manifest()
    assert manifest["manifest_version"] == 1
    assert AgentSurface.model_validate(manifest["surface"]) == spec


def test_manifest_is_deterministic_and_json_serialisable(catalogue):
    a = build_surface(_aligner(), catalogue).manifest()
    b = build_surface(_aligner(), catalogue).manifest()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    leaves = a["resolved"]["leaves"]
    assert [leaf["cli_path"] for leaf in leaves] == sorted(
        leaf["cli_path"] for leaf in leaves
    )
    assert a["resolved"]["known_trees"] == ["blaligner", "ref"]
    assert a["bash_pattern"] == "Bash(beamtimehero blaligner:*)"


def test_manifest_records_each_leafs_arguments(catalogue):
    manifest = build_surface(_aligner(), catalogue).manifest()
    leaf = next(
        lf for lf in manifest["resolved"]["leaves"]
        if lf["tool"] == "spec-write/move_motor"
    )
    assert leaf["mutates"] is True
    assert leaf["args"] == [
        {"flag": "--justification", "dest": "justification", "required": True,
         "type": "str", "choices": None, "default": None},
        {"flag": "--motor", "dest": "motor", "required": True,
         "type": "str", "choices": None, "default": None},
        {"flag": "--position", "dest": "position", "required": True,
         "type": "float", "choices": None, "default": None},
    ]


# ---------------------------------------------------------------------------
# The JSON schema
# ---------------------------------------------------------------------------

def _check_schema(instance, schema: dict, where: str = "$") -> list[str]:
    """A minimal JSON-Schema check, covering the keywords this schema uses.

    `jsonschema` is not a dependency of this library and adding one for a
    single assertion would be the wrong trade. The keywords below are
    exactly what `AgentSurface.model_json_schema()` emits — `type`,
    `properties`, `required`, `additionalProperties`, `items`, `anyOf`,
    `enum`, `uniqueItems` — and the test below proves this function
    rejects as well as accepts.
    """
    problems: list[str] = []
    types = {
        "object": dict, "array": list, "string": str,
        "boolean": bool, "null": type(None),
    }

    if "anyOf" in schema:
        if not any(not _check_schema(instance, sub, where) for sub in schema["anyOf"]):
            problems.append(f"{where}: matches no branch of anyOf")
        return problems

    expected = schema.get("type")
    if expected and not isinstance(instance, types.get(expected, object)):
        return [f"{where}: expected {expected}, got {type(instance).__name__}"]
    if "enum" in schema and instance not in schema["enum"]:
        problems.append(f"{where}: {instance!r} not in {schema['enum']}")

    if expected == "object":
        properties = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if key not in instance:
                problems.append(f"{where}: missing required {key!r}")
        extra_schema = schema.get("additionalProperties")
        for key, value in instance.items():
            if key in properties:
                problems += _check_schema(value, properties[key], f"{where}.{key}")
            elif extra_schema is False:
                problems.append(f"{where}: additional property {key!r}")
            elif isinstance(extra_schema, dict):
                problems += _check_schema(value, extra_schema, f"{where}.{key}")
    elif expected == "array":
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(instance):
                problems += _check_schema(item, item_schema, f"{where}[{i}]")
        if schema.get("uniqueItems") and len(instance) != len(
            {json.dumps(i, sort_keys=True) for i in instance}
        ):
            problems.append(f"{where}: items are not unique")
    return problems


def test_the_schema_checker_rejects_as_well_as_accepts():
    """Guard the guard: a checker that accepts everything proves nothing."""
    from beamtimehero_cli.agent_surface.spec import AgentSurface as Model

    schema = Model.model_json_schema()
    assert _check_schema({"name": "zz"}, schema) == []
    assert _check_schema({}, schema)                          # missing name
    assert _check_schema({"name": 3}, schema)                 # wrong type
    assert _check_schema({"name": "zz", "nope": 1}, schema)   # extra key
    assert _check_schema({"name": "zz", "layout": "sideways"}, schema)


def test_manifest_surface_validates_against_the_checked_in_schema(catalogue):
    schema = json.loads((REPO_ROOT / "docs" / "agent_surface.schema.json").read_text())
    for spec in (
        _aligner(),
        AgentSurface(
            name="cc", layout="flat", exclusive=True,
            tools=("spec-file/read_scan", "s3df/list_scans"),
            aliases={"list-scans-s3df": ("s3df", "list_scans")},
        ),
    ):
        dump = build_surface(spec, catalogue).manifest()["surface"]
        assert _check_schema(dump, schema) == [], spec.name


# ---------------------------------------------------------------------------
# The snapshot helper
# ---------------------------------------------------------------------------

def test_snapshot_ignores_the_subparsers_dest():
    """Two parsers holding the same commands under different dests.

    `dest` is an internal Namespace key (`leaf`, `leaf_s3df`, `subtree`)
    that a parity test cannot act on and that differs for reasons nobody
    can see from the command line.
    """
    def built(dest: str) -> dict:
        parser = argparse.ArgumentParser(prog="p", add_help=False)
        subs = parser.add_subparsers(dest=dest, metavar="<x>")
        leaf = subs.add_parser("go", help="h")
        leaf.add_argument("--n", type=int)
        return snapshot_parser(parser)

    assert built("leaf") == built("subtree")


def test_snapshot_is_the_only_module_that_reads_argparse_privates():
    """The fence that makes the walk affordable.

    Type annotations naming `argparse._SubParsersAction` are unavoidable —
    argparse publishes no other name for it — so what is checked here is
    the *reads*: the undocumented containers whose shape could change in
    any Python release.
    """
    package = REPO_ROOT / "src" / "beamtimehero_cli" / "agent_surface"
    reads = ("._actions", "._defaults", "._choices_actions", "._subparsers",
             "._get_positional_actions", "._option_string_actions")
    offenders = {
        path.name for path in sorted(package.glob("*.py"))
        if any(token in path.read_text() for token in reads)
    }
    assert offenders == {"snapshot.py"}


def test_snapshot_keeps_the_dispatch_defaults_and_strip_keys_removes_them():
    parser = argparse.ArgumentParser(prog="p", add_help=False)
    subs = parser.add_subparsers(dest="leaf")
    leaf = subs.add_parser("go")
    leaf.set_defaults(_tool_name="go", _agent_role="zz", _noise="x")

    node = snapshot_parser(parser)["children"]["go"]
    assert node["defaults"] == {"_tool_name": "go", "_agent_role": "zz"}
    assert strip_keys(snapshot_parser(parser), ["_agent_role"])["children"]["go"][
        "defaults"
    ] == {"_tool_name": "go"}


# ---------------------------------------------------------------------------
# Invariants the design rests on
# ---------------------------------------------------------------------------

def test_names_are_not_unique_but_no_shared_name_mutates(catalogue):
    """Why `ToolPath` is the identity, and why name-keying is still safe.

    131 definitions under 125 names: six names exist on two trees each.
    Lineage — and therefore `mutates()` and `write_tools` — is keyed by
    *name*, so a name on two trees has one safety class for both. That is
    only sound while no such name mutates: if one did, naming it in
    `write_tools` would carry both paths, and omitting it would drop
    both, neither of which is what a consumer would mean.

    If this test ever fails, `write_tools` and `Catalogue.mutates` have to
    become path-keyed together.
    """
    from collections import Counter

    names = Counter(
        (tdef.get("function") or {}).get("name") for tdef in TOOL_DEFINITIONS
    )
    assert sum(names.values()) == 131
    assert len(names) == 125
    duplicated = {name for name, n in names.items() if n > 1}
    assert duplicated == {
        "get_latest_scan", "list_scans", "read_scan",
        "get_active_counter", "get_scan_deadtime", "plot_scan",
    }
    assert not [name for name in duplicated if catalogue.mutates(name)]

    # And the index really is keyed by path, so both of a pair survive.
    paths = {"/".join(p) for p in catalogue.index()}
    assert len(paths) == 131
    assert {"spec-file/list_scans", "s3df/list_scans"} <= paths


def test_exactly_the_spec_write_branch_mutates(catalogue):
    mutating = {
        "/".join(tool.path) for tool in catalogue.index().values() if tool.mutates
    }
    assert len(mutating) == 40
    assert {p.split("/")[0] for p in mutating} == {"spec-write"}


def test_importing_the_surface_api_does_not_import_config():
    """`chemcatal` sets environment variables after this import.

    `beamtimehero_cli.config` reads the environment at import time, so a
    module that declares a surface has to be importable before the
    environment is final. Only `spec.py` is eager; everything else
    resolves through the package `__getattr__`.
    """
    program = (
        "import json, sys\n"
        "from beamtimehero_cli.agent_surface import AgentSurface\n"
        "s = AgentSurface(name='zz')\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.startswith('beamtimehero_cli'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    modules = json.loads(result.stdout)
    assert "beamtimehero_cli.config" not in modules
    assert "beamtimehero_cli.tool_catalog" not in modules
    assert "beamtimehero_cli.cli.__main__" not in modules
    assert "beamtimehero_cli.agent_surface.spec" in modules
    # The lazy attributes are not resolved by the import either.
    assert "beamtimehero_cli.agent_surface.build" not in modules


def test_the_lazy_attributes_resolve_to_the_real_objects():
    import beamtimehero_cli.agent_surface as surface
    from beamtimehero_cli.agent_surface import build as build_module

    assert surface.build_surface is build_module.build_surface
    assert surface.SurfaceBuild is build_module.SurfaceBuild
    assert "build_surface" in dir(surface)
    with pytest.raises(AttributeError):
        surface.no_such_name


def test_build_surface_never_reads_the_profile_registry(catalogue):
    """Profiles are the mechanism this replaces; a surface must not touch it.

    Consumers restricted their parser by deleting entries from the
    library's global `PROFILES` and restoring them in a `finally`. A
    surface reads its own frozen spec, so there is nothing global to hide
    — and the placeholder `bl-aligner` profile stays registered and
    simply never appears in a surface's parser.
    """
    from beamtimehero_cli.cli.profiles import PROFILES

    package = REPO_ROOT / "src" / "beamtimehero_cli" / "agent_surface"
    forbidden = ("cli.profiles", "cli import profiles", "build_profile_subtrees(")
    offenders = [
        path.name for path in sorted(package.glob("*.py"))
        if any(token in path.read_text() for token in forbidden)
    ]
    assert offenders == []

    assert "bl-aligner" in PROFILES
    parser = build_surface(
        AgentSurface(name="cc", layout="flat", exclusive=True,
                     tools=("spec-file/read_scan",)),
        catalogue,
    ).make_parser(prog="p")
    assert set(snapshot_parser(parser)["children"]) == {"ref", "cc"}


# ---------------------------------------------------------------------------
# The motor sources
# ---------------------------------------------------------------------------

def test_known_motors_parses_the_captured_spec_config(monkeypatch):
    from beamtimehero_cli.spec_data import spec_config

    excerpt = REPO_ROOT / "tests" / "data" / "spec_config_motors.txt"
    monkeypatch.setattr(spec_config, "SPEC_CONFIG_PATH", excerpt)
    motors = spec_config.known_motors()

    assert motors is not None
    assert {"Sx", "Sy", "Sz", "Sr", "energy", "mono", "crystal", "pitcha"} <= motors
    # The mnemonic, not the controller, the flags or the descriptive name.
    assert "GALIL:6/0" not in motors and "0x003" not in motors
    # `MOTPAR:` lines begin with MOT and are not motors.
    assert not {m for m in motors if m.startswith(("misc_par", "read_mode", "MOTOR"))}
    # Counter lines are in the excerpt and must not leak in.
    assert not {"I0", "I1", "sec"} & motors
    assert len(motors) == 25


def test_known_motors_is_none_when_there_is_no_spec_config(monkeypatch, tmp_path):
    from beamtimehero_cli.spec_data import spec_config

    monkeypatch.setattr(spec_config, "SPEC_CONFIG_PATH", tmp_path / "nope")
    assert spec_config.known_motors() is None


def test_mock_motors_unions_the_override(monkeypatch):
    from beamtimehero_cli.spec_data import spec_config

    monkeypatch.delenv("SPEC_MOCK_MOTORS", raising=False)
    base = spec_config.mock_motors()
    assert {"Sx", "Sy", "Sz", "energy", "mono"} <= base

    monkeypatch.setenv("SPEC_MOCK_MOTORS", json.dumps({"zz_probe": 1.0}))
    assert "zz_probe" in spec_config.mock_motors()

    monkeypatch.setenv("SPEC_MOCK_MOTORS", "{not json")
    assert spec_config.mock_motors() == base


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------

def test_the_refdoc_is_registered_and_readable():
    from beamtimehero_cli import refdocs

    assert refdocs.has_doc("agent-surfaces")
    assert "agent-surfaces" in dict(refdocs.list_docs())
    text = refdocs.get_doc("agent-surfaces")
    assert "build_surface" in text and "motors_validated" in text
