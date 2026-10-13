"""CLI discovery: leaf --help says what a tool does; no empty or foreign trees."""
from __future__ import annotations

import argparse

from beamtimehero_cli.cli.__main__ import build_catalog_subtrees
from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS


def _build(defs):
    parser = argparse.ArgumentParser(prog="beamtimehero")
    subs = parser.add_subparsers(dest="tree")
    build_catalog_subtrees(subs, defs)
    return parser, subs


def test_leaf_help_carries_the_description(capsys):
    parser, _ = _build(TOOL_DEFINITIONS)
    try:
        parser.parse_args(["tool", "caget", "--help"])
    except SystemExit:
        pass
    assert "over Channel Access" in " ".join(capsys.readouterr().out.split())


def test_trees_with_no_tools_are_not_listed():
    only = [d for d in TOOL_DEFINITIONS if d["function"]["name"] == "caget"]
    _, subs = _build(only)
    assert set(subs.choices) == {"tool"}


def test_bl41_hides_xrs_and_tender(monkeypatch):
    monkeypatch.setenv("BTH_STATION", "bl41")
    _, subs = _build(TOOL_DEFINITIONS)
    assert "xrs" not in subs.choices and "tender" not in subs.choices and "cherfd" in subs.choices
    monkeypatch.delenv("BTH_STATION")
    _, subs = _build(TOOL_DEFINITIONS)
    assert "xrs" in subs.choices                     # BL15-2 unchanged
