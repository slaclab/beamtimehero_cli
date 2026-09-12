"""A canonical, comparable rendering of an argparse tree.

The acceptance rule for every migration onto ``build_surface`` is "the
generated surface equals the hand-written one". argparse offers no value
to compare — two parsers holding identical commands are not ``==`` — so
this walks the privates once, here, and the parity tests compare dicts.

**This is the only module in the package that reads argparse privates.**
That is the point of it existing: the walk is a liability (``_actions``,
``_SubParsersAction``, ``_choices_actions``, ``_defaults`` are all
undocumented), and a liability in one file with one docstring is a
different thing from the same walk copied into every consumer that wants
to check its own work. ``autonomous`` stamped ``_agent_role`` by walking
``_actions`` in its CLI script and got four of nine branches; that walk
is gone, and this one is fenced.

Deliberately excluded: a subparsers action's ``dest``. It is an internal
Namespace key (``leaf``, ``leaf_s3df``, ``subtree``) that differs between
two parsers holding the identical commands, so including it would make
parity tests fail on a detail nobody can observe from the command line.

Imports nothing from the rest of the library, so a subprocess test is
free to import ``beamtimehero_cli`` at a moment of its own choosing.
"""
from __future__ import annotations

import argparse
import json
from typing import Iterable

# argparse stores the callable; the snapshot stores a stable name, so a
# reference built in another process still compares equal.
_TYPE_NAMES = {
    "int": "int",
    "float": "float",
    "_bool_from_str": "bool",
    "_json_arg": "json",
}

#: The ``set_defaults`` keys that carry meaning for dispatch. Everything
#: else argparse puts in ``_defaults`` is noise for these comparisons.
DISPATCH_DEFAULTS: tuple[str, ...] = ("_tool_name", "_tool_category", "_agent_role")


def _type_name(fn) -> str:
    if fn is None:
        return "str"
    name = getattr(fn, "__name__", None) or str(fn)
    return _TYPE_NAMES.get(name, name)


def _action(a: argparse.Action) -> dict:
    return {
        "flags": sorted(a.option_strings),
        "dest": a.dest,
        "required": bool(a.required),
        "type": _type_name(a.type),
        "choices": list(a.choices) if a.choices else None,
        "default": a.default,
        "nargs": a.nargs,
        "help": a.help,
    }


def _node(parser: argparse.ArgumentParser, keep_defaults: tuple[str, ...], help_text) -> dict:
    options: list[dict] = []
    positionals: list[dict] = []
    children: dict[str, dict] = {}

    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            helps = {
                pseudo.dest: pseudo.help
                for pseudo in getattr(a, "_choices_actions", [])
            }
            for name, sub in a.choices.items():
                children[name] = _node(sub, keep_defaults, helps.get(name))
            continue
        if a.option_strings:
            options.append(_action(a))
        else:
            positionals.append(_action(a))

    options.sort(key=lambda o: o["flags"])
    defaults = {
        k: v
        for k, v in (parser._defaults or {}).items()
        if k in keep_defaults
    }
    return {
        "help": help_text,
        "description": parser.description,
        "defaults": defaults,
        "options": options,
        "positionals": positionals,
        "children": children,
    }


def snapshot_parser(
    parser: argparse.ArgumentParser,
    *,
    keep_defaults: tuple[str, ...] = DISPATCH_DEFAULTS,
) -> dict:
    """Every command, flag and dispatch default the parser will accept."""
    return _node(parser, tuple(keep_defaults), None)


def strip_keys(node: dict, keys: Iterable[str]) -> dict:
    """A copy of ``node`` with ``keys`` gone from every ``defaults`` map.

    Normalisation for a parity test that is comparing surfaces built by
    two different generations of code, where one stamped a default the
    other did not. The canonical use is ``_agent_role``: the old walk
    reached four branches, the new build reaches all of them, and that
    difference is the fix rather than a regression — so the comparison
    is made with the key removed and asserted separately.
    """
    keys = frozenset(keys)
    return {
        **node,
        "defaults": {k: v for k, v in node["defaults"].items() if k not in keys},
        "children": {
            name: strip_keys(child, keys)
            for name, child in node["children"].items()
        },
    }


def canonical(snapshot: dict) -> str:
    """A byte-comparable rendering, for cross-process comparison."""
    return json.dumps(snapshot, sort_keys=True, indent=1, default=str)


__all__ = ["DISPATCH_DEFAULTS", "canonical", "snapshot_parser", "strip_keys"]
