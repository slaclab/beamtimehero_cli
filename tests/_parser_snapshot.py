"""Canonical, comparable snapshot of an argparse tree.

A test that claims "this change does not alter the parser" needs a value
it can compare, and argparse offers none — so this walks the privates
once, here, and every parity test in the suite compares dicts instead.

Imports nothing from ``beamtimehero_cli``: the import-order test in
``test_registration_seams.py`` runs this inside a subprocess that must be
free to import the library at a moment of its own choosing.

Deliberately excluded: a subparsers action's ``dest``. It is an internal
Namespace key (``leaf``, ``leaf_s3df``, ``subtree``) that differs between
two parsers holding identical commands, so including it would make the
parity tests fail on a detail nobody can observe from the command line.
"""
from __future__ import annotations

import argparse
import json

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
DISPATCH_DEFAULTS = ("_tool_name", "_tool_category", "_agent_role")


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


def canonical(snapshot: dict) -> str:
    """A byte-comparable rendering, for cross-process comparison."""
    return json.dumps(snapshot, sort_keys=True, indent=1, default=str)
