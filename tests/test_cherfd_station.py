"""One station setting (BTH_STATION) for a deployment: BL15-2 or BL4-1.

The mock follows the configured station; the BL4-1 fixture under
tests/data/cherfd_bl41 is a real 2 x fwd+rev Ge scan from 2026-10-10 (no
detector heads fitted, so the Ge columns are zero; ADCs already decoded).
"""
from __future__ import annotations

import json
from pathlib import Path

from tests._cherfd_fixtures import *  # noqa: F401,F403 -- isolation fixtures
from tests._cherfd_fixtures import run_tool as run

import pytest

from beamtimehero_cli import station
from beamtimehero_cli.cherfd_control import mock_server, settings

BL41_DATA = Path(__file__).parent / "data" / "cherfd_bl41"
BL41_RUN = "testing/g4@2026-10-10_175637"


@pytest.fixture
def bl41(monkeypatch):
    monkeypatch.setenv("BTH_STATION", "bl41")
    # the crystal file is a beamline file; pin it so the test is the same anywhere
    monkeypatch.setattr(settings, "station_crystal", lambda profile=None: "A")
    mock_server.reset_mock_state()


def test_station_defaults_and_overrides(monkeypatch):
    assert station.station_name() == "bl15" and station.default("BTH_MAX_COUNT_TIME_S") == ""
    monkeypatch.setenv("BTH_STATION", "bl41")
    assert station.default("BTH_BEAM_STATUS_REQUIRES_GAP") == "0"
    assert station.default("BTH_ACTIVE_COUNTER_PRIORITY").startswith("xsum")
    assert settings.station().detector == "ge32"
    assert station.default("BTH_SPEC_LOG_FILE_PATTERN") == "log_*"
    monkeypatch.setenv("BTH_STATION", "bl99")
    with pytest.raises(ValueError, match="not a known station"):
        station.station_name()


def test_bl41_station_tool(bl41):
    r, _ = run("cherfd_get_station")
    assert r["ok"], r["problems"]
    assert r["profile"]["detector"] == "ge32" and r["profile"]["default_signal"] == "xsum"
    assert r["server"]["station"] == "bl41" and r["server_detector"] == "ge32"


def test_bl41_scan_flow(bl41):
    cmd = "cscan 7100 7150 .2 20"
    r, _ = run("cherfd_check_readiness", command=cmd, include_beam_status=False)
    assert r["ready"], r["blockers"]
    assert "undulator" not in r["info"] and r["info"]["live_check"]["ok"]
    assert not any("undulator" in w or "xtc" in w for w in r["warnings"])
    bad, _ = run("cherfd_start_scan", command=cmd, undulator_scan=True, justification="x")
    assert not bad["ok"] and "no undulator" in bad["error"]
    s, _ = run("cherfd_start_scan", command=cmd, justification="BL4-1 flow test")
    assert s["ok"] and s["started"], s
    w, _ = run("cherfd_wait_for_scan", timeout_s=20, poll_s=0.2)
    assert w["ok"] and w["done"]


def test_bl41_move_waits_for_the_acquisition(bl41):
    run("cherfd_start_scan", command="cscan 7100 7150 .2 20", sweeps=200, justification="long")
    m, _ = run("cherfd_move_energy", energy_ev=7112, justification="park")
    assert not m["ok"] and m["http_status"] == 409
    run("cherfd_stop_scan", justification="done")


def test_bl41_live_check_refuses_out_of_reach(bl41):
    c, _ = run("cherfd_check_command", command="cscan 5300 4600 1 20")
    assert c["ok"] is False and "outside" in c["error"]


@pytest.mark.parametrize("tool, args", [
    ("cherfd_move_gap", {"gap_mm": 9.0}),
    ("cherfd_set_undulator_tracking", {"enable": True}),
    ("cherfd_undulator_gap_for_energy", {"energy_ev": 7100}),
    ("cherfd_daq_count", {"duration_s": 0.5}),
])
def test_bl41_refuses_what_it_does_not_have(bl41, tool, args):
    r, _ = run(tool, justification="x", **args)
    assert not r["ok"] and "BL4-1" in r["error"]


def test_server_of_another_station_is_refused(monkeypatch):
    from beamtimehero_cli.tool_catalog import tools_cherfd
    monkeypatch.setattr(tools_cherfd, "_server_station", lambda: {"station": "bl41"})
    r, _ = run("cherfd_start_scan", command="cherfd 8968 9100 .4 100", justification="x")
    assert r["refused"] == "station"


def test_wait_reports_a_failed_scan():
    run("cherfd_start_scan", command="cherfd 8968 9100 .4 100", justification="x")
    with mock_server._state() as st:
        st["scan"]["finished"] = True
        st["controller"].update(state="error", error="Ge detector recorded 40 of 55 frames")
    w, _ = run("cherfd_wait_for_scan", timeout_s=5, poll_s=0.2)
    assert w["done"] and not w["ok"] and "40 of 55" in w["error"]


@pytest.fixture
def bl41_data(bl41, monkeypatch):
    monkeypatch.setenv("CHERFD_DATA_DIR", str(BL41_DATA))


def test_bl41_run_reads_ge_columns_and_counts(bl41_data):
    s, _ = run("cherfd_read_sweep", run_id=BL41_RUN, columns=["absev", "xsum", "adc_1"])
    assert s["provenance"]["detector_source"].startswith("ge32_g4_1_fwd")
    assert {"xs01", "xs24", "xsum", "xsA", "xsB"} <= set(s["columns_available"])
    assert 1800 < s["data"]["adc_1"][0] < 2000                # signed counts, not 2**31-offset
    assert not any("decoded" in n for n in s["notes"])


def test_bl41_run_quality(bl41_data):
    q, _ = run("cherfd_assess_run_quality", run_id=BL41_RUN)
    assert q["signal"] == "xsum" and len(q["sweeps"]) == 4
    for sw in q["sweeps"]:
        codes = {i["code"] for i in sw["issues"]}
        assert "signal_all_zero" in codes                     # no Ge heads fitted that day
        assert "incomplete_range" not in codes                # ~2 eV backlash start is expected
        assert sw["missing_triggers"] == 0


def test_bl41_set_detector(bl41):
    d, _ = run("cherfd_set_detector", detector="none", justification="TFY on the PIPS diode")
    assert d["ok"] and d["detector_after"] == "none"
    s, _ = run("cherfd_get_station")
    assert s["server_detector"] == "none"
    d, _ = run("cherfd_set_detector", detector="vortex", justification="x")
    assert not d["ok"] and "ge32" in d["error"]
    run("cherfd_start_scan", command="cscan 7100 7150 .2 20", sweeps=200, justification="long")
    d, _ = run("cherfd_set_detector", detector="ge32", justification="x")
    assert not d["ok"] and "during an acquisition" in str(d)
    run("cherfd_stop_scan", justification="done")


# ---------------------------------------------------------------------------
# The table never moves in a continuous scan (BL4-1 beamline policy)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["bl15", "bl41"])
def test_profiles_never_track_the_table(name):
    from beamtimehero_cli.science.cherfd import policy
    assert policy.STATION_PROFILES[name].table_tracking is False


def test_table_tools_refuse_at_bl41(bl41):
    on, _ = run("cherfd_set_table_tracking", enable=True, justification="x")
    assert not on.get("ok", False) and "never moved" in json.dumps(on)
    anchor, _ = run("cherfd_set_table_anchor", capture=True, justification="x")
    assert not anchor.get("ok", False) and "never moved" in json.dumps(anchor)
    off, _ = run("cherfd_set_table_tracking", enable=False, justification="x")
    assert off["ok"], off


def test_start_and_readiness_refuse_if_server_tracks_the_table(bl41, monkeypatch):
    from beamtimehero_cli.tool_catalog import tools_cherfd
    real = tools_cherfd.audited_cherfd

    def tracking_on(name, *a, **kw):
        if name == "table_tracking":
            return {"ok": True, "result": {"enabled": True, "anchor": {"energy": 7100.0}}}
        return real(name, *a, **kw)
    monkeypatch.setattr(tools_cherfd, "audited_cherfd", tracking_on)
    cmd = "cscan 7100 7150 .2 20"
    s, _ = run("cherfd_start_scan", command=cmd, justification="x")
    assert s["refused"] == "table_tracking"
    r, _ = run("cherfd_check_readiness", command=cmd, include_beam_status=False)
    assert not r["ready"] and any("never moves" in b for b in r["blockers"])


def test_server_offering_table_tracking_is_a_mismatch(bl41):
    from beamtimehero_cli.tool_catalog import tools_cherfd
    why = tools_cherfd._station_mismatch({"station": "bl41", "table_tracking_available": True})
    assert why and "never moves" in why


def test_scan_mode_policy(monkeypatch):
    # unset station: BL15-2, step only - the behaviour before scan modes existed
    monkeypatch.delenv("BTH_STATION", raising=False)
    assert station.scan_modes() == ("step",) and station.default_scan_mode() == "step"
    with pytest.raises(ValueError, match="allowed: step"):
        station.check_scan_mode("continuous")
    monkeypatch.setenv("BTH_STATION", "bl41")
    assert station.default_scan_mode() == "continuous"
    assert station.check_scan_mode(" Step ") == "step"
    with pytest.raises(ValueError):
        station.check_scan_mode("fly")
    assert station.scan_modes("bl15") == ("step",)


def test_spec_eval_url_is_per_station(monkeypatch):
    import importlib

    from beamtimehero_cli import config
    monkeypatch.delenv("SPEC_EVAL_URL", raising=False)
    try:
        monkeypatch.setenv("BTH_STATION", "bl41")
        assert importlib.reload(config).SPEC_EVAL_URL == "http://127.0.0.1:5016"
        monkeypatch.delenv("BTH_STATION")
        assert importlib.reload(config).SPEC_EVAL_URL == "http://127.0.0.1:5006"   # BL15-2 unchanged
        monkeypatch.setenv("BTH_STATION", "bl41")
        monkeypatch.setenv("SPEC_EVAL_URL", "http://127.0.0.1:5999")
        assert importlib.reload(config).SPEC_EVAL_URL == "http://127.0.0.1:5999"   # explicit wins
    finally:
        monkeypatch.delenv("SPEC_EVAL_URL", raising=False)
        monkeypatch.delenv("BTH_STATION", raising=False)
        importlib.reload(config)
