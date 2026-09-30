"""Fixtures for the CHERFD tests (imported with ``from tests._cherfd_fixtures import *``).

Every write -- action log, mock state, safety switches, exports -- goes to a
per-test temp dir, and the cherfd_claude checkout is only used by tests that
ask for ``sibling`` / ``real_data`` (skipped when it is not next to this repo).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SIBLING = Path(__file__).resolve().parents[2] / "cherfd_claude"
REAL_RUN = "2025-10_Sokaras/16_MEA1058_CuNCs_channel_1p23V_CO_Pip_1MKOH_cscan_spot1@2025-10-25_125330"


@pytest.fixture(autouse=True)
def _cherfd_isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("CHERFD_MOCK", raising=False)
    monkeypatch.setenv("BEAMLINE_TOOLS_DB_PATH", str(tmp_path / "actions.db"))
    monkeypatch.setenv("BEAMTIMEHERO_SAFETY_SWITCHES", str(tmp_path / "safety_switches.json"))
    monkeypatch.setenv("CHERFD_MOCK_STATE", str(tmp_path / "mock_state.json"))
    monkeypatch.setenv("CHERFD_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("CHERFD_MOCK_TIME_SCALE", "0.002")
    for var in ("CHERFD_PROJECT_DIR", "CHERFD_DATA_DIR", "CHERFD_LOGS_DIR", "CHERFD_PYTHON"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def switches(tmp_path):
    path = tmp_path / "safety_switches.json"

    def write(obj):
        path.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return write


@pytest.fixture
def sibling(monkeypatch):
    if not (SIBLING / "cscan_box" / "simulate.py").exists():
        pytest.skip("cherfd_claude checkout not available next to this repo")
    monkeypatch.setenv("CHERFD_PROJECT_DIR", str(SIBLING))
    return SIBLING


@pytest.fixture
def real_data(sibling):
    if not (sibling / "data" / "2025-10_Sokaras").is_dir():
        pytest.skip("2025-10_Sokaras sample data not available")
    return sibling / "data"


def run_tool(tool: str, **args):
    """Call a cherfd tool through the library executor on its own branch."""
    from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS, execute_tool
    from beamtimehero_cli.tool_catalog.categorize import categorize
    tdef = next(d for d in TOOL_DEFINITIONS if d["function"]["name"] == tool)
    text, imgs = execute_tool(categorize(tdef), tool, args)
    return json.loads(text), imgs


__all__ = ["REAL_RUN", "SIBLING", "_cherfd_isolated", "switches", "sibling", "real_data", "run_tool"]
