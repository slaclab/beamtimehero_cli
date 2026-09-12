"""End-to-end smoke: dispatch a core tool through execute_tool."""
from __future__ import annotations


import pytest


@pytest.fixture(autouse=True)
def mock_spec(monkeypatch):
    monkeypatch.setenv("SPEC_MOCK", "1")
    monkeypatch.setenv("BEAMTIMEHERO_CLI_LOG", "0")


def test_execute_unknown_tool_returns_message():
    from beamtimehero_cli.tool_catalog import execute_tool
    text, imgs = execute_tool(("tool",), "definitely_not_a_real_tool", {})
    assert "Unknown tool" in text
    assert imgs == []


def test_execute_list_scans():
    from beamtimehero_cli.tool_catalog import execute_tool
    text, imgs = execute_tool(("tool",), "list_scans", {"limit": 5})
    # Either returns a JSON array (possibly empty) or a plain message; both fine.
    assert isinstance(text, str)
    assert isinstance(imgs, list)


def test_tool_definitions_are_core_only():
    """TOOL_DEFINITIONS must not include plan-aware tool names."""
    from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS
    names = {d["function"]["name"] for d in TOOL_DEFINITIONS}
    forbidden = {
        "request_human_intervention", "post_status_update",
        "update_plan", "record_sample_progress", "record_convergence_stats",
        "get_plan", "get_experiment_config", "get_remaining_beamtime",
        "set_experiment_end_time", "get_staff_guidance", "list_open_interventions",
        "set_sample_time_budget", "set_holder_time_budget", "get_holder_time_budget",
        "get_scans_since_last_plan_update", "get_scans_for_active_sample",
        "upload_sample_alignment_results", "upload_sample_survey_results",
        "get_comprehensive_collection_plan", "record_completed_scan",
        "regenerate_plan", "log_status_assessment",
    }
    leaked = forbidden & names
    assert not leaked, f"plan-aware tools leaked into TOOL_DEFINITIONS: {leaked}"
