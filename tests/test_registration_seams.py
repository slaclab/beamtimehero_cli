"""The registration seams: what a consumer may add, and what must not move.

Consumers used to extend this catalog by reassigning library module
attributes — ``categorize.TOOL_LINEAGE = {...}``,
``cli.__main__.execute_tool = ...`` — because there was no other way in.
That works only while every reader happens to look up the attribute
rather than hold the object, which is not a property anyone can check.

These tests define the replacement and hold it to two promises:

1. **Registration is visible.** Lineage reaches ``categorize()``, a
   definition plus a handler reaches ``DISPATCH``, and the containers are
   the *same objects* afterwards, so anything already holding one sees the
   change.
2. **Not registering anything changes nothing.** The seams are additive;
   an unused seam must leave the parser it grew out of byte-identical.
   That is what makes this phase safe to ship before any consumer moves.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests._parser_snapshot import canonical, snapshot_parser

REPO_ROOT = Path(__file__).resolve().parent.parent

PROBE_DEF = {
    "type": "function",
    "function": {
        "name": "zz_probe",
        "description": "Registration-seam probe tool.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many rows."},
            },
            "required": [],
        },
    },
}

PROBE_LINEAGE = {
    "zz_probe": {
        "long_description": "Probe tool registered by a test.",
        "python_func": "tests.test_registration_seams.probe(args)",
        "spec_command": None,
        "mutates": False,
        "output": "The string 'probe'.",
        "source": "autonomy_db",
        "source_detail": "Nothing; it exists to be registered.",
        "depends_on": [],
    },
}


def probe(args: dict) -> tuple[str, list[str]]:
    return "probe", []


@pytest.fixture
def registry():
    """Restore every registry this module mutates.

    All six are module-level containers mutated in place, so a test that
    registers a tool and does not undo it leaks into the rest of the
    session — including into the parity tests below.
    """
    from beamtimehero_cli.tool_catalog import (
        _REGISTERED_DEFINITIONS,
        TOOL_DEFINITIONS,
        tools_core,
    )
    from beamtimehero_cli.tool_catalog.lineage import TOOL_LINEAGE

    saved_defs = list(TOOL_DEFINITIONS)
    saved_registered = list(_REGISTERED_DEFINITIONS)
    saved_lineage = dict(TOOL_LINEAGE)
    saved_handlers = dict(tools_core._HANDLERS)
    saved_branch = dict(tools_core._BRANCH_HANDLERS)
    saved_dispatch = dict(tools_core.DISPATCH)
    try:
        yield
    finally:
        TOOL_DEFINITIONS[:] = saved_defs
        _REGISTERED_DEFINITIONS[:] = saved_registered
        TOOL_LINEAGE.clear()
        TOOL_LINEAGE.update(saved_lineage)
        tools_core._HANDLERS.clear()
        tools_core._HANDLERS.update(saved_handlers)
        tools_core._BRANCH_HANDLERS.clear()
        tools_core._BRANCH_HANDLERS.update(saved_branch)
        tools_core.DISPATCH.clear()
        tools_core.DISPATCH.update(saved_dispatch)


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------

def test_registered_lineage_is_visible_to_categorize(registry):
    """The whole point: no monkey-patch needed for a tree to change."""
    from beamtimehero_cli.tool_catalog.categorize import categorize
    from beamtimehero_cli.tool_catalog.lineage import register_lineage

    # With no lineage entry the tool falls through to the default branch
    # rather than raising — a consumer definition may legitimately have none.
    assert categorize(PROBE_DEF) == ("tool",)

    register_lineage({"zz_probe": {**PROBE_LINEAGE["zz_probe"],
                                   "source": "spec_session", "mutates": True}})
    assert categorize(PROBE_DEF) == ("spec-write",)


def test_incomplete_lineage_entry_is_rejected_by_key(registry):
    from beamtimehero_cli.tool_catalog.lineage import TOOL_LINEAGE, register_lineage

    entry = dict(PROBE_LINEAGE["zz_probe"])
    del entry["mutates"]
    del entry["output"]
    with pytest.raises(ValueError) as excinfo:
        register_lineage({"zz_probe": entry})

    message = str(excinfo.value)
    assert "mutates" in message and "output" in message, (
        "the error must name every missing key, not just the first: "
        f"got {message!r}"
    )
    assert "zz_probe" not in TOOL_LINEAGE, "a rejected mapping must not half-merge"


def test_register_lineage_updates_in_place(registry):
    """``categorize.py`` binds the dict at import; a rebind would be invisible."""
    from beamtimehero_cli.tool_catalog import categorize as categorize_mod
    from beamtimehero_cli.tool_catalog.lineage import TOOL_LINEAGE, register_lineage

    assert categorize_mod.TOOL_LINEAGE is TOOL_LINEAGE
    register_lineage(PROBE_LINEAGE)
    assert categorize_mod.TOOL_LINEAGE["zz_probe"]["source"] == "autonomy_db"


# ---------------------------------------------------------------------------
# Definitions + handlers -> DISPATCH
# ---------------------------------------------------------------------------

def test_definitions_and_handlers_reach_the_same_dispatch_object(registry):
    from beamtimehero_cli.tool_catalog import (
        TOOL_DEFINITIONS,
        execute_tool,
        register_tools,
        tools_core,
    )

    before = tools_core.DISPATCH
    register_tools(
        definitions=[PROBE_DEF], lineage=PROBE_LINEAGE, handlers={"zz_probe": probe},
    )

    assert tools_core.DISPATCH is before, (
        "DISPATCH was rebound; every consumer holding the old object still "
        "has the old table"
    )
    assert ("db", "zz_probe") in before, (
        f"registered tool is not in DISPATCH: "
        f"{sorted(k for k in before if k[-1] == 'zz_probe')}"
    )
    assert PROBE_DEF in TOOL_DEFINITIONS
    # And it is reachable through the shipped executor, not just present.
    assert execute_tool(("db",), "zz_probe", {}) == ("probe", [])


def test_duplicate_tool_path_is_refused(registry):
    from beamtimehero_cli.tool_catalog import register_definitions

    clash = {
        "type": "function",
        "function": {
            "name": "list_scans",
            "description": "Would shadow the spec-file leaf.",
            "parameters": {"type": "object", "properties": {}},
        },
        "tree": "spec-file",
    }
    with pytest.raises(ValueError, match="spec-file/list_scans"):
        register_definitions([clash])


def test_duplicate_name_on_a_new_branch_is_allowed(registry):
    """Six names already exist on two branches; that must stay legal."""
    from beamtimehero_cli.tool_catalog import register_definitions, tools_core

    other = {
        "type": "function",
        "function": {
            "name": "list_scans",
            "description": "A third backend for the same leaf name.",
            "parameters": {"type": "object", "properties": {}},
        },
        "tree": "zz-branch",
    }
    register_definitions([other])
    tools_core.register_handlers({("zz-branch", "list_scans"): probe})
    assert tools_core.DISPATCH[("zz-branch", "list_scans")] is probe


def test_register_tools_classifies_before_it_path_checks(registry):
    """Two consumer tools sharing a name, on different trees, in one call.

    ``register_definitions`` dedupes by ``categorize(d) + (name,)``, so the
    order inside ``register_tools`` is load-bearing. One of these takes its
    tree from the *incoming* lineage (``source: "autonomy_db"`` -> ``db``),
    the other pins ``tree`` explicitly. Register definitions before that
    lineage exists and the first one has no lineage to classify by, falls
    through to ``("tool",)``, collides with the second, and the whole call
    is refused with a duplicate it does not actually have.

    ``autonomous`` is this shape: its lineage is what puts its four CAT-8
    tools on ``db``, and it registers definitions and lineage together.
    """
    from beamtimehero_cli.tool_catalog import register_tools, tools_core
    from beamtimehero_cli.tool_catalog.categorize import categorize

    from_lineage = dict(PROBE_DEF)
    explicit = {**PROBE_DEF, "tree": "tool"}

    register_tools(
        definitions=[from_lineage, explicit],
        lineage=PROBE_LINEAGE,
        handlers={("db", "zz_probe"): probe, ("tool", "zz_probe"): probe},
    )

    assert categorize(from_lineage) == ("db",), "lineage did not classify it"
    assert categorize(explicit) == ("tool",), "explicit tree did not win"
    assert tools_core.DISPATCH[("db", "zz_probe")] is probe
    assert tools_core.DISPATCH[("tool", "zz_probe")] is probe


def test_name_keyed_handler_overrides_only_the_unbranched_path(registry):
    """Override precedence is the existing rule, not a second one.

    A name-keyed handler covers every path for that name *except* one
    already claimed in ``_BRANCH_HANDLERS`` — which is exactly the
    flatten rule consumers hand-code today.
    """
    from beamtimehero_cli.tool_catalog import tools_core

    s3df_before = tools_core.DISPATCH[("s3df", "list_scans")]
    tools_core.register_handlers({"list_scans": probe, "measure_beam_size": probe})

    assert tools_core.DISPATCH[("spec-file", "list_scans")] is probe
    assert tools_core.DISPATCH[("spec-write", "measure_beam_size")] is probe
    assert tools_core.DISPATCH[("s3df", "list_scans")] is s3df_before, (
        "the s3df branch handler was replaced by a name-keyed registration"
    )


def test_tuple_keyed_handler_overrides_one_branch(registry):
    from beamtimehero_cli.tool_catalog import tools_core

    spec_file_before = tools_core.DISPATCH[("spec-file", "list_scans")]
    tools_core.register_handlers({("s3df", "list_scans"): probe})

    assert tools_core.DISPATCH[("s3df", "list_scans")] is probe
    assert tools_core.DISPATCH[("spec-file", "list_scans")] is spec_file_before


def test_non_callable_handler_is_refused(registry):
    from beamtimehero_cli.tool_catalog import tools_core

    with pytest.raises(ValueError, match="not callable"):
        tools_core.register_handlers({"zz_probe": "not a function"})


# ---------------------------------------------------------------------------
# make_executor
# ---------------------------------------------------------------------------

def test_make_executor_envelopes_are_byte_equal_to_execute_tool():
    from beamtimehero_cli.tool_catalog.executor import execute_tool, make_executor
    from beamtimehero_cli.tool_catalog.tools_core import DISPATCH

    made = make_executor(DISPATCH)

    # Unknown tool, on a real branch and on a made-up one.
    for tree, name in ((("db",), "nope"), (("zz",), "list_scans"), ("tool", "nope")):
        assert made(tree, name, {}) == execute_tool(tree, name, {})

    # A failing tool. Same handler both sides, so only the envelope differs.
    def boom(args):
        raise RuntimeError("kaboom")

    table = {("db", "zz_boom"): boom}
    assert make_executor(table)(("db",), "zz_boom", {})[0] == (
        "Tool error (db/zz_boom): kaboom"
    )


def test_before_hook_short_circuits_and_after_hook_rewrites():
    from beamtimehero_cli.tool_catalog.executor import make_executor

    calls: list[tuple] = []

    def handler(args):
        calls.append(("ran", args))
        return "raw", ["img"]

    table = {("db", "zz_probe"): handler}

    def refuse(tree, name, args):
        if args.get("blocked"):
            return f"Refused: {name} blocked on {'/'.join(tree)}"
        return None

    def annotate(tree, name, text):
        return text + "!"

    ex = make_executor(table, before=(refuse,), after=(annotate,))

    assert ex(("db",), "zz_probe", {"blocked": True}) == (
        "Refused: zz_probe blocked on db", [],
    )
    assert calls == [], "a refusing before-hook must not reach the handler"

    assert ex(("db",), "zz_probe", {}) == ("raw!", ["img"])
    assert calls == [("ran", {})]


def test_observer_after_hook_leaves_the_text_alone():
    from beamtimehero_cli.tool_catalog.executor import make_executor

    seen: list[str] = []
    ex = make_executor(
        {("db", "zz_probe"): lambda args: ("raw", [])},
        after=(lambda tree, name, text: seen.append(text),),
    )
    assert ex(("db",), "zz_probe", {}) == ("raw", [])
    assert seen == ["raw"]


# ---------------------------------------------------------------------------
# Nothing moves until someone uses a seam
# ---------------------------------------------------------------------------

def _canonical_parser() -> str:
    from beamtimehero_cli.cli.api import build_parser
    return canonical(snapshot_parser(build_parser()))


def test_unused_seams_leave_the_parser_byte_identical():
    """Importing every seam and calling none of them changes nothing."""
    before = _canonical_parser()

    import beamtimehero_cli.cli.api  # noqa: F401
    import beamtimehero_cli.cli.trees  # noqa: F401
    from beamtimehero_cli.tool_catalog import (  # noqa: F401
        register_definitions,
        register_lineage,
        register_tools,
    )
    from beamtimehero_cli.tool_catalog.executor import make_executor  # noqa: F401
    from beamtimehero_cli.tool_catalog.tools_core import register_handlers  # noqa: F401

    assert _canonical_parser() == before


def test_default_catalog_subtrees_equal_the_explicit_full_call():
    """The new keyword arguments default to the old behaviour exactly."""
    from beamtimehero_cli.cli.api import ToolParser, build_catalog_subtrees
    from beamtimehero_cli.cli.trees import TREE_HELPS
    from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS

    def built(**kwargs) -> dict:
        parser = ToolParser(prog="p")
        subs = parser.add_subparsers(dest="tree", metavar="<tree>")
        build_catalog_subtrees(subs, TOOL_DEFINITIONS, **kwargs)
        return snapshot_parser(parser)

    assert built() == built(agent_role=None, trees=set(TREE_HELPS))


def test_build_catalog_subtrees_returns_its_branches():
    from beamtimehero_cli.cli.api import ToolParser, build_catalog_subtrees
    from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS

    parser = ToolParser(prog="p")
    subs = parser.add_subparsers(dest="tree", metavar="<tree>")
    branches = build_catalog_subtrees(subs, TOOL_DEFINITIONS)

    assert ("spec-write",) in branches and ("s3df", "psql") in branches
    assert "move-motor" in branches[("spec-write",)].choices


def test_agent_role_is_stamped_on_every_leaf_and_only_when_asked():
    """The gap this closes: four of nine branches used to carry the role."""
    from beamtimehero_cli.cli.api import ToolParser, build_catalog_subtrees
    from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS

    def leaves(node: dict) -> list[dict]:
        if not node["children"]:
            return [node]
        return [leaf for child in node["children"].values() for leaf in leaves(child)]

    def built(**kwargs) -> dict:
        parser = ToolParser(prog="p")
        subs = parser.add_subparsers(dest="tree", metavar="<tree>")
        build_catalog_subtrees(subs, TOOL_DEFINITIONS, **kwargs)
        return snapshot_parser(parser)

    stamped = [
        leaf for leaf in leaves(built(agent_role="blaligner"))
        if leaf["defaults"].get("_tool_name")
    ]
    assert len(stamped) > 100
    assert all(leaf["defaults"]["_agent_role"] == "blaligner" for leaf in stamped)

    plain = [
        leaf for leaf in leaves(built())
        if leaf["defaults"].get("_tool_name")
    ]
    assert len(plain) == len(stamped)
    assert not any("_agent_role" in leaf["defaults"] for leaf in plain), (
        "the unrestricted parser must not gain an _agent_role key"
    )


def test_trees_limits_which_empty_branches_are_precreated():
    from beamtimehero_cli.cli.api import ToolParser, build_catalog_subtrees
    from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS

    parser = ToolParser(prog="p")
    subs = parser.add_subparsers(dest="tree", metavar="<tree>")
    build_catalog_subtrees(
        subs, TOOL_DEFINITIONS,
        trees={("spec-read",), ("spec-write",)},
    )
    # Branches carrying tools still appear — tools create their own branch.
    # The point of `trees` is which *empty* ones are pre-created.
    assert "spec-read" in subs.choices and "spec-write" in subs.choices


def test_run_tool_leaf_payload_excludes_parser_bookkeeping():
    import argparse

    from beamtimehero_cli.cli.api import run_tool_leaf

    seen: list[dict] = []

    def spy(tree, name, payload):
        seen.append(payload)
        return "ok", []

    args = argparse.Namespace(
        tree="spec-file", leaf="list-scans", leaf_s3df=None, subtree="spec-file",
        list_profiles=False, _tool_name="list_scans", _tool_category=("spec-file",),
        _agent_role="blaligner", limit=5, file_name=None,
    )
    assert run_tool_leaf(args, executor=spy) == 0
    assert seen == [{"limit": 5}], (
        "only real tool arguments may reach the handler; a leaked _agent_role "
        "or subtree becomes an unexpected keyword in every handler"
    )


def test_run_tool_leaf_defaults_to_the_module_executor(monkeypatch):
    """Resolved at call time, which is what the old monkey-patch relied on."""
    import argparse

    from beamtimehero_cli.cli import __main__ as parser_module

    calls: list[tuple] = []
    monkeypatch.setattr(
        parser_module, "execute_tool",
        lambda tree, name, payload: (calls.append((tree, name)), ("ok", []))[1],
    )
    args = argparse.Namespace(_tool_name="list_scans", _tool_category=("spec-file",))
    assert parser_module.run_tool_leaf(args) == 0
    assert calls == [(("spec-file",), "list_scans")]


# ---------------------------------------------------------------------------
# Import order
# ---------------------------------------------------------------------------

_ORDER_PROGRAM = """
import json, sys

PROBE_DEF = json.loads(%(probe_def)r)
PROBE_LINEAGE = json.loads(%(probe_lineage)r)


def probe(args):
    return "probe", []


def register():
    from beamtimehero_cli.tool_catalog import register_tools
    register_tools(
        definitions=[PROBE_DEF], lineage=PROBE_LINEAGE, handlers={"zz_probe": probe},
    )


def observe():
    from beamtimehero_cli.cli.api import build_parser
    from beamtimehero_cli.tool_catalog.tools_core import DISPATCH
    from tests._parser_snapshot import snapshot_parser
    return {
        "parser": snapshot_parser(build_parser()),
        "dispatch": sorted("/".join(k) for k in DISPATCH),
    }


if sys.argv[1] == "register-first":
    register()
    out = observe()
else:
    observe()          # force the whole parser + dispatch table to exist
    register()
    out = observe()

print(json.dumps(out, sort_keys=True, indent=1, default=str))
""" % {
    "probe_def": json.dumps(PROBE_DEF),
    "probe_lineage": json.dumps(PROBE_LINEAGE),
}


def _run_order(order: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", _ORDER_PROGRAM, order],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"{order} subprocess failed:\n{result.stdout}\n{result.stderr}"
    )
    return result.stdout


def test_registration_before_or_after_import_gives_the_same_surface():
    """Two subprocesses, opposite orders, identical result.

    In-process this is untestable — the modules are already imported. It
    matters because a consumer's ``register_tools`` call sits at import
    time in its own package, and which of the two runs first depends on
    an import graph nobody controls. If the answer differed, the parser a
    consumer gets would depend on the order its modules happened to load.
    """
    assert _run_order("register-first") == _run_order("import-first")
    assert "db/zz_probe" in _run_order("register-first")
