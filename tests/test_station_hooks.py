"""Station hooks: additive, BL15-2 defaults unchanged.

A station whose hardware differs from BL15-2 (BL4-1: no ID gap, no
filters, no Vortex, a 67 s count cap, gains on an EPICS IOC) needs a way
to say so without patching this package. These tests hold the hooks to
two promises: with nothing set, BL15-2 behaviour is byte-identical; with
the hook set, the station behaviour is what it says.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from beamtimehero_cli.spec_control import spec_cmd
from beamtimehero_cli.spec_control.spec_cmd import CommandSpec


@pytest.fixture
def station_reset():
    """Restore module-level station settings after each test."""
    gap = spec_cmd.beam_status_requires_gap()
    cap = spec_cmd.max_count_time()
    yield
    spec_cmd.set_beam_status_requires_gap(gap)
    spec_cmd.set_max_count_time(cap)


# ---- beam status -----------------------------------------------------------

BL152_OUT = "{'spear_current': 485.2, 'bl_state': 'OPEN', 'gap_owned': 1}"
BL152_NOGAP = "{'spear_current': 485.2, 'bl_state': 'OPEN', 'gap_owned': 0}"
BL41_OUT = '{"spear_current": 485.20, "bl_state": "OPEN"}'


def test_beam_status_default_is_bl152(station_reset):
    assert spec_cmd.beam_status_requires_gap() is True
    ok = spec_cmd._parse_beam_status(BL152_OUT, [])
    assert ok["beam_good"] is True and ok["gap_owned"] is True
    bad = spec_cmd._parse_beam_status(BL152_NOGAP, [])
    assert bad["beam_good"] is False
    assert bad["reason"] == "gap not owned by BL15-2"
    # A two-field reply is *not* a good beam at BL15-2 (gap unknown).
    assert not spec_cmd._parse_beam_status(BL41_OUT, [])["beam_good"]


def test_beam_status_without_gap(station_reset):
    spec_cmd.set_beam_status_requires_gap(False)
    good = spec_cmd._parse_beam_status(BL41_OUT, [])
    assert good["beam_good"] is True
    assert good["gap_owned"] is None
    assert good["reason"] is None
    low = spec_cmd._parse_beam_status('{"spear_current": 12.0, "bl_state": "OPEN"}', [])
    assert low["beam_good"] is False and "SPEAR current low" in low["reason"]
    closed = spec_cmd._parse_beam_status('{"spear_current": 490.0, "bl_state": "CLOSED"}', [])
    assert closed["reason"] == "beamline state is CLOSED"
    # gap_owned 0 is ignored: there is no gap to own.
    assert spec_cmd._parse_beam_status(BL152_NOGAP, [])["beam_good"] is True


def test_mock_beam_status_follows_setting(station_reset):
    from beamtimehero_cli.spec_control.transport import _MockScreen

    assert "gap_owned" in _MockScreen.inject("p beam_status()")
    spec_cmd.set_beam_status_requires_gap(False)
    out = _MockScreen.inject("p beam_status()")
    assert "gap_owned" not in out
    assert spec_cmd._parse_beam_status(out, [])["beam_good"] is True


# ---- count-time cap ----------------------------------------------------------

def test_no_cap_renders_verbatim(station_reset):
    spec_cmd.set_max_count_time(None)
    assert spec_cmd.render("ct", ["120"]) == "ct 120"
    assert spec_cmd.render("ct", []) == "ct 1"
    assert spec_cmd.render("dscan", ["Sx", "-1", "1", "20", "90"]) == "dscan Sx -1 1 20 90"
    assert spec_cmd.render("gscan", ["mono", "7080", "7100", "1", "90"]).startswith("gscan mono")


def test_cap_refuses_long_counts(station_reset):
    spec_cmd.set_max_count_time(67)
    assert spec_cmd.render("ct", ["2"]) == "ct 2"
    for command, args in (
        ("ct", ["68"]),
        ("ascan", ["Sx", "-1", "1", "20", "70"]),
        ("dscan", ["Sx", "-1", "1", "20", "70"]),
        ("d2scan", ["Sx", "-1", "1", "Sz", "-1", "1", "20", "70"]),
        ("gscan", ["mono", "7080", "7100", "1", "70"]),
        ("kscan", ["7080", "1", "70", "7100", "7112", "2", "0.05", "12", "1", "4", "2"]),
        ("kscan", ["7080", "1", "1", "7100", "7112", "2", "0.05", "12", "1", "80", "2"]),
    ):
        with pytest.raises(ValueError, match="exceeds this station's limit"):
            spec_cmd.render(command, args)
    # call() turns the render failure into a refusal, before any dispatch.
    res = spec_cmd.call("ct", ["90"])
    assert res["ok"] is False and "limit of 67" in res["error"]


def test_env_value_parsing():
    assert spec_cmd._positive_float("67") == 67.0
    assert spec_cmd._positive_float("nope") is None
    assert spec_cmd._positive_float("") is None
    assert spec_cmd._positive_float("0") is None
    assert spec_cmd._flag("0", True) is False
    assert spec_cmd._flag("", True) is True
    assert spec_cmd._flag("1", False) is True


# ---- register / unregister ---------------------------------------------------

def test_register_and_unregister_command():
    probe = CommandSpec("zz_probe_cmd", "read", lambda a: "p 1", lambda o, a: {"raw": o})
    spec_cmd.register_command(probe)
    try:
        assert spec_cmd.command_kind("zz_probe_cmd") == "read"
        assert spec_cmd.render("zz_probe_cmd", []) == "p 1"
        with pytest.raises(ValueError, match="already registered"):
            spec_cmd.register_command(probe)
        spec_cmd.register_command(
            CommandSpec("zz_probe_cmd", "action", lambda a: "p 2"), replace=True)
        assert spec_cmd.command_kind("zz_probe_cmd") == "action"
    finally:
        removed = spec_cmd.unregister_command("zz_probe_cmd")
    assert removed is not None
    assert spec_cmd.command_kind("zz_probe_cmd") is None
    assert spec_cmd.unregister_command("zz_probe_cmd") is None
    with pytest.raises(ValueError):
        spec_cmd.register_command(CommandSpec("zz_bad", "other", lambda a: ""))


def test_unregistered_command_is_refused():
    saved = spec_cmd.unregister_command("mv_energy")
    try:
        res = spec_cmd.call("mv_energy", ["7100"], justification="test")
        assert res == {"ok": False, "kind": "unknown", "error": "unknown command: mv_energy"}
    finally:
        spec_cmd.register_command(saved)
    assert spec_cmd.command_kind("mv_energy") == "action"


# ---- mock counters -----------------------------------------------------------

def test_mock_counters_override(monkeypatch):
    from beamtimehero_cli.spec_control.transport import _MockScreen

    monkeypatch.delenv("SPEC_MOCK_COUNTERS", raising=False)
    assert "vortDT" in _MockScreen.inject("ct 1")
    monkeypatch.setenv("SPEC_MOCK_COUNTERS", json.dumps({"I0": 1.5e5, "xsum": 4200}))
    out = _MockScreen.inject("ct 1")
    parsed = spec_cmd._parse_ct(out, [])
    assert parsed["counters"] == {"I0": 1.5e5, "xsum": 4200.0}


# ---- energy-scan prefixes ----------------------------------------------------

def test_energy_scan_prefixes(monkeypatch):
    from beamtimehero_cli.spec_data import local_data

    monkeypatch.delenv("BTH_ENERGY_SCAN_PREFIXES", raising=False)
    assert local_data._energy_scan_prefixes() == ("gscan energy",)
    monkeypatch.setenv("BTH_ENERGY_SCAN_PREFIXES", "gscan mono, kscan")
    assert local_data._energy_scan_prefixes() == ("gscan mono", "kscan")

    cache = {
        "a:1": {"file_name": "a", "scan_command": "gscan mono 7080 7100 1 1", "date_time": "2026-10-01"},
        "a:2": {"file_name": "a", "scan_command": "kscan 7080 1 1 7100 7112 2 0.05 12 1 4 2", "date_time": "2026-10-02"},
        "b:1": {"file_name": "b", "scan_command": "gscan energy 7080 7100 1 1", "date_time": "2026-10-03"},
        "b:2": {"file_name": "b", "scan_command": "gscan energy 7080 7100 1 1", "date_time": "2026-10-03"},
    }
    monkeypatch.setattr(local_data, "_load_cache", lambda: cache)
    assert local_data.average_latest_energy_scans_file() == "a"
    monkeypatch.delenv("BTH_ENERGY_SCAN_PREFIXES")
    assert local_data.average_latest_energy_scans_file() == "b"


# ---- counter auto-pick -------------------------------------------------------

def test_xsum_after_bl152_counters():
    from beamtimehero_cli.science.reduce.counters import pick_active_counter

    df = pd.DataFrame({"mono": [1, 2], "I0": [1, 1], "I1": [1, 1], "xsum": [5, 9]})
    assert pick_active_counter(df)[0] == "xsum"
    df["vortDT"] = [3, 4]
    assert pick_active_counter(df)[0] == "vortDT"  # BL15-2 order wins
    assert pick_active_counter(df.drop(columns=["xsum", "vortDT"]))[0] == "I1"


# ---- audited device call -----------------------------------------------------

@pytest.fixture
def action_rows(monkeypatch, tmp_path):
    from beamtimehero_cli import audited_call as ac

    rows: list[dict] = []

    class _Row:
        def __init__(self, i):
            self.id = i

    def start_action(**kw):
        rows.append({"start": kw})
        return _Row(f"act-{len(rows)}")

    def finish_action(action_id, **kw):
        rows[-1]["finish"] = {"id": action_id, **kw}

    monkeypatch.setattr(ac, "start_action", start_action)
    monkeypatch.setattr(ac, "mark_action_started", lambda _id: None)
    monkeypatch.setattr(ac, "finish_action", finish_action)
    switches = tmp_path / "safety_switches.json"
    monkeypatch.setenv("BEAMTIMEHERO_SAFETY_SWITCHES", str(switches))
    return rows, switches


def test_device_call_logs_before_running(action_rows):
    from beamtimehero_cli.audited_call import audited_device_call

    rows, _ = action_rows
    seen = []

    def run():
        seen.append(len(rows))  # the row exists before the I/O
        return {"ok": True, "value": 3}

    res = audited_device_call("probe_write", ["x"], "because", run, wire="caput X 3")
    assert res["ok"] is True and res["action_id"] == "act-1"
    assert seen == [1]
    assert rows[0]["start"]["spec_string"] == "caput X 3"
    assert rows[0]["finish"]["success"] is True


def test_device_call_refusals(action_rows):
    from beamtimehero_cli.audited_call import audited_device_call

    rows, switches = action_rows
    ran = []
    res = audited_device_call("probe_write", [], "  ", lambda: ran.append(1), wire="w")
    assert res["ok"] is False and "justification" in res["error"]
    switches.write_text(json.dumps({"spec_read_enabled": True, "spec_write_enabled": False}))
    res = audited_device_call("probe_write", [], "why", lambda: ran.append(1), wire="w")
    assert res["ok"] is False and "SAFETY SWITCH" in res["error"]
    assert ran == [] and rows == []


def test_device_call_records_failures(action_rows):
    from beamtimehero_cli.audited_call import audited_device_call

    rows, _ = action_rows

    def boom():
        raise RuntimeError("no IOC")

    res = audited_device_call("probe_write", [], "why", boom, wire="w")
    assert res["ok"] is False and "no IOC" in res["error"]
    assert rows[0]["finish"]["success"] is False
    res = audited_device_call("probe_write", [], "why",
                              lambda: {"ok": False, "error": "refused"}, wire="w")
    assert res["error"] == "refused"


def test_action_rows_name_the_phase_run(action_rows, monkeypatch):
    from beamtimehero_cli.audited_call import PHASE_RUN_ENV, audited_device_call

    rows, _ = action_rows
    monkeypatch.delenv(PHASE_RUN_ENV, raising=False)
    audited_device_call("probe_write", [], "why", lambda: {"ok": True}, wire="w")
    assert rows[0]["start"]["phase_run_id"] is None          # unset: as before
    monkeypatch.setenv(PHASE_RUN_ENV, "pr-9")
    audited_device_call("probe_write", [], "why", lambda: {"ok": True}, wire="w")
    assert rows[1]["start"]["phase_run_id"] == "pr-9"


def test_action_rows_take_the_phase_label_from_the_env(action_rows, monkeypatch):
    from beamtimehero_cli import runtime_state
    from beamtimehero_cli.audited_call import PHASE_ENV, audited_device_call

    rows, _ = action_rows
    monkeypatch.delenv(PHASE_ENV, raising=False)
    audited_device_call("probe_write", [], "why", lambda: {"ok": True}, wire="w")
    assert rows[0]["start"]["phase"] == runtime_state.get_phase()   # unset: as before
    monkeypatch.setenv(PHASE_ENV, "energy_calibration")
    audited_device_call("probe_write", [], "why", lambda: {"ok": True}, wire="w")
    assert rows[1]["start"]["phase"] == "energy_calibration"
