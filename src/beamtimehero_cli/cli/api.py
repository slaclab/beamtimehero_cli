"""The supported import surface for consumers composing their own CLI.

The parser helpers have always been public — ``README.md`` documents them
— but they live in ``beamtimehero_cli.cli.__main__``, and importing a
module named ``__main__`` from another package is the kind of thing that
looks like a mistake and invites being "fixed". Worse, it is also the
module ``python -m beamtimehero_cli.cli`` executes, so a consumer that
imports it by path can end up with two copies of the parser state.

Import from here instead::

    from beamtimehero_cli.cli.api import build_parser, dispatch, run_with

Nothing is reimplemented: these are the same objects. What the module
buys is a name that says "this is the contract", so the thirty-odd
helpers in ``__main__`` that are *not* listed here can move without
breaking anyone.

Branch names live in :mod:`beamtimehero_cli.cli.trees`, which imports
nothing; this module builds the whole parser, so it is not import-light.
"""
from __future__ import annotations

from beamtimehero_cli.cli.__main__ import (
    ToolParser,
    add_arg,
    build_catalog_subtrees,
    build_parser,
    build_ref_subtree,
    dispatch,
    main,
    run_ref,
    run_tool_leaf,
    run_with,
)

__all__ = [
    "ToolParser",
    "add_arg",
    "build_catalog_subtrees",
    "build_parser",
    "build_ref_subtree",
    "dispatch",
    "main",
    "run_ref",
    "run_tool_leaf",
    "run_with",
]
