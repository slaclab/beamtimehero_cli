"""``mutates`` is the declared safety class. These tests are why it can be trusted.

A tool's safety class used to be inferred: "requires ``justification``"
meant "mutates the beamline". That made the most consequential fact about
a tool a side effect of how its arguments were spelled — deleting the flag
from a schema moved a motor-moving tool onto ``spec-read``, and a consumer
with no ``justification`` argument had no way to mark a tool mutating at all.

``lineage.py`` now declares it. Declaring a fact is only better than
inferring one if something checks the declaration, so:

* ``test_mutates_matches_justification`` keeps the flag and the schema in
  step, so the migration is provably a no-op on the shipped catalog.
* ``test_categorize_ignores_required`` proves the inference is really gone —
  it is the test that fails if someone puts the old rule back.
* ``test_mutating_tools_issue_action_commands`` reaches past both and asks
  ``tools_core.py`` what its handlers actually send to SPEC. That is the
  only check here grounded in behaviour rather than in two declarations
  agreeing with each other.
"""
from __future__ import annotations

import ast
import copy
import pathlib

import pytest

from beamtimehero_cli.spec_control import spec_cmd
from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS
from beamtimehero_cli.tool_catalog.categorize import categorize
from beamtimehero_cli.tool_catalog.lineage import TOOL_LINEAGE

TOOLS_CORE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "src" / "beamtimehero_cli" / "tool_catalog" / "tools_core.py"
)

#: The count is pinned on purpose. Every one of these requires a
#: justification, writes an action-log row before SPEC sees it, and can
#: move hardware; a silent change to the size of that set is exactly the
#: thing a review should be forced to look at.
MUTATING_COUNT = 40


def _requires_justification(tdef: dict) -> bool:
    params = (tdef.get("function") or {}).get("parameters") or {}
    return "justification" in set(params.get("required") or [])


def _mutating_names() -> set[str]:
    return {name for name, entry in TOOL_LINEAGE.items() if entry["mutates"]}


@pytest.mark.parametrize(
    "name", sorted({d["function"]["name"] for d in TOOL_DEFINITIONS})
)
def test_mutates_matches_justification(name):
    """The declared flag and the schema must agree, in both directions.

    Not a tautology: they are two independent statements now, in two
    files. When they disagree the answer is almost always that the
    schema is wrong, because ``mutates`` is what categorize() and every
    consumer write-filter reads.
    """
    declared = TOOL_LINEAGE[name]["mutates"]
    needs = any(
        _requires_justification(d)
        for d in TOOL_DEFINITIONS
        if d["function"]["name"] == name
    )
    assert declared == needs, (
        f"{name}: lineage says mutates={declared} but its schema "
        f"{'requires' if needs else 'does not require'} --justification. "
        "Every mutating tool must require a justification, and nothing else may."
    )


def test_mutating_set_is_the_spec_write_branch():
    on_branch = {
        d["function"]["name"] for d in TOOL_DEFINITIONS
        if categorize(d) == ("spec-write",)
    }
    assert _mutating_names() == on_branch
    assert len(on_branch) == MUTATING_COUNT, (
        f"{len(on_branch)} mutating tools, expected {MUTATING_COUNT}. If a "
        "tool was genuinely added or removed, update MUTATING_COUNT in the "
        "same commit and say so in the message."
    )


def test_no_duplicated_tool_name_is_mutating():
    """Six names exist on two branches each; lineage is keyed by name.

    So a name-keyed write filter (which is what every consumer has)
    cannot half-apply to one branch and not the other — as long as no
    duplicated name is mutating. If one ever is, the filter silently
    stops being tree-accurate and this test is the warning.
    """
    seen: dict[str, int] = {}
    for d in TOOL_DEFINITIONS:
        name = d["function"]["name"]
        seen[name] = seen.get(name, 0) + 1
    duplicated = {n for n, c in seen.items() if c > 1}
    assert duplicated, "expected the known name/path mismatch to still exist"
    assert not (duplicated & _mutating_names()), (
        f"a tool name on two branches is now mutating: "
        f"{sorted(duplicated & _mutating_names())}. Name-keyed write filters "
        "cannot express that; give the branches distinct names first."
    )


def _definition(name: str) -> dict:
    for d in TOOL_DEFINITIONS:
        if d["function"]["name"] == name:
            return copy.deepcopy(d)
    raise AssertionError(f"no definition for {name}")


def test_categorize_ignores_required():
    """The old inference is gone: ``required`` no longer classifies.

    Both directions, because only checking one leaves half the old rule
    in place and passing.
    """
    without = _definition("move_motor")
    required = without["function"]["parameters"]["required"]
    assert "justification" in required
    required.remove("justification")
    assert categorize(without) == ("spec-write",), (
        "move_motor stopped being a mutation because its schema lost a flag"
    )

    with_it = _definition("read_motor_position")
    params = with_it["function"]["parameters"]
    params["required"] = list(params.get("required") or []) + ["justification"]
    assert categorize(with_it) == ("spec-read",), (
        "read_motor_position was promoted to spec-write by a schema flag"
    )


# ---------------------------------------------------------------------------
# The cross-check: what the handlers actually send to SPEC
# ---------------------------------------------------------------------------

def _handler_commands() -> tuple[dict[str, set[str]], set[str]]:
    """Per tool name: the literal SPEC commands its handler issues.

    Static, by AST, so it cannot be fooled by the mock backend or by a
    test that patches ``audited_call``. Second return value is the set of
    handlers whose command is chosen at run time — those are unreadable
    statically and must declare ``spec_commands`` instead.
    """
    tree = ast.parse(TOOLS_CORE.read_text())
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    table = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "_HANDLERS":
            table = node.value
    assert isinstance(table, ast.Dict), "could not find the _HANDLERS dict literal"

    literals: dict[str, set[str]] = {}
    dynamic: set[str] = set()
    for key, value in zip(table.keys, table.values):
        name = key.value
        if isinstance(value, ast.Name):
            body = funcs.get(value.id)
            assert body is not None, f"_HANDLERS[{name!r}] -> unknown {value.id}"
        elif isinstance(value, ast.Lambda):
            body = value
        else:
            raise AssertionError(
                f"_HANDLERS[{name!r}] is a {type(value).__name__}; this scan "
                "only understands a module-level function or a lambda"
            )
        for call in ast.walk(body):
            if not (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "audited_call"
                    and call.args):
                continue
            first = call.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                literals.setdefault(name, set()).add(first.value)
            else:
                dynamic.add(name)
    return literals, dynamic


_LITERALS, _DYNAMIC = _handler_commands()


def test_the_ast_scan_found_the_handlers():
    """Guard the guard: a scan that finds nothing proves nothing."""
    assert len(_LITERALS) > 40, f"only {len(_LITERALS)} handlers issue a literal command"
    assert _DYNAMIC, "expected at least the two run-time-dispatching handlers"


def test_spec_commands_declared_exactly_for_dynamic_handlers():
    declared = {n for n, e in TOOL_LINEAGE.items() if "spec_commands" in e}
    assert declared == _DYNAMIC, (
        f"handlers choosing their SPEC command at run time: {sorted(_DYNAMIC)}; "
        f"lineage entries declaring spec_commands: {sorted(declared)}. These "
        "must be the same set — a dynamic handler with no spec_commands is a "
        "tool whose SPEC commands nothing can enumerate."
    )


def _commands_for(name: str) -> set[str]:
    return set(_LITERALS.get(name, set())) | set(TOOL_LINEAGE[name].get("spec_commands") or [])


@pytest.mark.parametrize("name", sorted(set(_LITERALS) | _DYNAMIC))
def test_every_issued_command_is_registered(name):
    unknown = sorted(c for c in _commands_for(name) if spec_cmd.command_kind(c) is None)
    assert not unknown, (
        f"{name}'s handler issues {unknown}, which spec_cmd does not know. "
        "An unregistered command has no kind, so audited_call cannot tell "
        "whether it needs a justification."
    )


@pytest.mark.parametrize("name", sorted(set(_LITERALS) | _DYNAMIC))
def test_mutating_tools_issue_action_commands(name):
    """``mutates`` must match what the handler sends, both ways round."""
    kinds = {spec_cmd.command_kind(c) for c in _commands_for(name)}
    declared = TOOL_LINEAGE[name]["mutates"]
    if "action" in kinds:
        assert declared, (
            f"{name} issues a SPEC command registered as an action but is "
            "declared mutates=False — it would be classified spec-read and "
            "pass a consumer's read-only write filter."
        )
    else:
        assert not declared, (
            f"{name} is declared mutates=True but issues no action command "
            f"(kinds: {sorted(k for k in kinds if k)}). Either the flag is "
            "wrong or the handler stopped doing what it claims."
        )
