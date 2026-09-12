"""--help surface tests.

Confirm the top-level CLI exposes only the generic trees plus `ref` and
the bundled agent profiles, and that no per-role agent branches
(`blaligner`, `samplealigner`, `collector`, `surveyor`) leak in.
"""
from __future__ import annotations


import pytest

from beamtimehero_cli.cli.__main__ import build_parser as _build_parser, main


EXPECTED_TREES = {
    "ref", "tool", "db", "spec-read", "spec-write",
    "spec-file", "s3df", "slack",
    # machine-readable tool schemas, for an agent harness that registers
    # tools up front instead of discovering them with --help
    "catalog",
    # dedicated X-ray Raman (XRS) analysis branch
    "xrs",
    # dedicated EXAFS k-space analysis branch
    "exafs",
    # the sandboxed research agent — one leaf, on its own branch so it can be
    # granted to a single agent without being granted to everything that
    # carries `tool`
    "research",
    # bundled agent profiles (curated views over the catalog)
    "bl-aligner",
}
FORBIDDEN_TREES = {"blaligner", "samplealigner", "collector", "surveyor", "steering"}


def test_top_level_trees_exact():
    parser = _build_parser()
    # subparsers is the first positional subparsers action
    sp = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    trees = set(sp.choices.keys())
    assert trees == EXPECTED_TREES, f"unexpected trees: {trees ^ EXPECTED_TREES}"


def test_no_agent_role_trees():
    parser = _build_parser()
    sp = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    leaked = FORBIDDEN_TREES & set(sp.choices.keys())
    assert not leaked, f"forbidden trees leaked into CLI: {leaked}"


def test_help_prints_and_exits_zero(capsys, monkeypatch):
    monkeypatch.setenv("BEAMTIMEHERO_CLI_LOG", "0")
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    # argparse exits 0 on --help
    assert exc_info.value.code in (0, None)
    out = capsys.readouterr().out
    for tree in EXPECTED_TREES:
        assert tree in out
