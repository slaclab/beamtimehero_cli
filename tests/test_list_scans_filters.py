"""``list_scans`` filters (file_name, command) and the summary view."""
from __future__ import annotations

import json

import pytest

from beamtimehero_cli.tool_catalog import tools_core


def _scan(file_name, n, command, when, points=51):
    return {
        "file_name": file_name, "scan_number": n, "scan_command": command,
        "date_time": when, "num_points": points, "counters": ["I0", "I1"],
        "count_time": 0.5, "acquisition_seconds": 30.0,
    }


# most recent first, as the backends return them
SCANS = [
    _scan("monos4", 3, "ascan mono 7107 7117 50 0.5", "2026-10-10T03:00:00"),
    _scan("monos4", 2, "ascan mono 7107 7117 50 0.5", "2026-10-10T02:00:00", points=12),
    _scan("loops4", 1, "loopscan 3600 .5", "2026-10-10T01:30:00", points=3600),
    _scan("monos4", 1, "ascan mono 7107 7117 50 0.5", "2026-10-10T01:00:00"),
    _scan("alignment", 7, "ascan Tz 14 18 40 0.2", "2026-10-09T12:00:00", points=41),
]


@pytest.fixture(autouse=True)
def fake_scans(monkeypatch):
    seen = {}

    def fake_list(limit=20):
        seen["limit"] = limit
        return [dict(s) for s in SCANS[:limit]]

    monkeypatch.setattr(tools_core.scan_data, "list_processed_scans", fake_list)
    return seen


def _run(**args):
    text, imgs = tools_core.t_list_scans(args)
    assert imgs == []
    return json.loads(text)


def test_plain_listing_unchanged(fake_scans):
    out = _run(limit=2)
    assert [s["scan_number"] for s in out] == [3, 2]
    assert fake_scans["limit"] == 2


def test_limit_applies_after_filtering(fake_scans):
    # The one alignment scan is the oldest: a pre-filter limit would miss it.
    out = _run(file_name="alignment", limit=1)
    assert [(s["file_name"], s["scan_number"]) for s in out] == [("alignment", 7)]
    assert fake_scans["limit"] > len(SCANS)


def test_command_filter_is_case_insensitive_substring():
    out = _run(command="MONO")
    assert {s["file_name"] for s in out} == {"monos4"}
    assert len(out) == 3


def test_summary_groups_by_file_and_command():
    out = _run(summary=True)
    assert out["total_scans"] == 5
    assert out["total_groups"] == 3
    monos = out["groups"][0]
    assert monos == {
        "file_name": "monos4",
        "scan_command": "ascan mono 7107 7117 50 0.5",
        "count": 3,
        "first_scan": 1,
        "last_scan": 3,
        "first_time": "2026-10-10T01:00:00",
        "last_time": "2026-10-10T03:00:00",
        "num_points": [12, 51],
    }
    assert out["groups"][1]["num_points"] == 3600


def test_summary_with_filter_and_limit():
    out = _run(summary=True, command="ascan", limit=1)
    assert out["total_scans"] == 4
    assert out["total_groups"] == 2
    assert [g["file_name"] for g in out["groups"]] == ["monos4"]
