"""Station web-service reads, app logs, provenance bundles, BL4-1 SPEC extras."""
from __future__ import annotations

import inspect
import json
import zipfile

import pytest

from beamtimehero_cli import applogs, provenance, station_services
from beamtimehero_cli.tool_catalog import tools_core


# --- station services (GET only, mocked) -------------------------------------

class _R:
    def __init__(self, body, code=200):
        self._b, self.status_code, self.text = body, code, json.dumps(body)

    def json(self):
        return self._b


@pytest.fixture
def http(monkeypatch):
    import requests
    calls, replies = [], {}
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append(url) or replies.get(url, _R({}, 404)))
    for name in ("post", "put", "delete", "patch"):
        monkeypatch.setattr(requests, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("write")))
    monkeypatch.setenv("BTH_STATION", "bl41")
    return calls, replies


def test_module_never_writes():
    src = inspect.getsource(station_services)
    assert "requests.get" in src
    for verb in ("requests.post", "requests.put", "requests.delete", "requests.patch", "caput"):
        assert verb not in src


def test_sample_environment_reads_the_gas_mixer(http):
    calls, replies = http
    replies["http://127.0.0.1:5006/api/status"] = _R({
        "mode": "live", "mixer": {"set_ic": 3, "main_p": 1.1},
        "ion_chambers": [{"num": 1, "label": "I0", "pressure": 1.07}], "hv": [], "page_rules": {}, "pvs": {}})
    out = json.loads(tools_core.t_get_sample_environment({})[0])
    assert out["gas_mixer"]["ok"] and out["gas_mixer"]["ion_chambers"][0]["pressure"] == 1.07
    assert not out["temperatures"]["ok"] and "BTH_SAMPLE_TEMP_PVS" in out["temperatures"]["error"]
    assert calls == ["http://127.0.0.1:5006/api/status"]


def test_temperature_pvs_are_read_with_caget(http, monkeypatch):
    from beamtimehero_cli import epics_ca
    monkeypatch.setenv("BTH_SAMPLE_TEMP_PVS", "cryo=BL41:LS:T1,heater=BL41:LS:T2")
    monkeypatch.setattr(epics_ca, "caget", lambda pvs, **kw: {"ok": True, "results": [
        {"pv": p, "connected": True, "value": 77.0} for p in pvs]})
    out = station_services.sample_environment()
    assert [r["label"] for r in out["temperatures"]["readings"]] == ["cryo", "heater"]


def test_bl15_has_none_of_the_services(monkeypatch):
    monkeypatch.delenv("BTH_STATION", raising=False)
    for var in ("BTH_GASMIXER_URL", "BTH_XSP32_GUI_URL", "BTH_MOTOR_ANALYZER_URL"):
        monkeypatch.delenv(var, raising=False)
    assert not station_services.ge_detector_status()["ok"]
    assert "no Motor Analyzer" in station_services.motor_diagnostics("mono")["error"]


def test_ge_and_motor_reads(http):
    calls, replies = http
    replies["http://192.168.41.6:8032/api/state"] = _R({"rois": [[6300, 6500]], "bad": [7]})
    replies["http://127.0.0.1:5011/api/motors"] = _R({"motors": [{"mne": "mono"}, {"mne": "Sx"}]})
    replies["http://127.0.0.1:5011/api/runs"] = _R({"runs": [{"id": "r1", "motor": "Sx"}, {"id": "r2", "motor": "mono"}]})
    assert station_services.ge_detector_status()["state"]["bad"] == [7]
    d = station_services.motor_diagnostics("Sx")
    assert d["motors"] == [{"mne": "Sx"}] and [r["id"] for r in d["recent_runs"]] == ["r1"]
    assert not station_services.motor_diagnostics("nope")["ok"]


# --- app logs ----------------------------------------------------------------

@pytest.fixture
def logroot(tmp_path, monkeypatch):
    root = tmp_path / "logs"
    (root / "sub").mkdir(parents=True)
    (root / "app.log").write_text("".join(f"line {i}\n" for i in range(300)) + "ERROR boom\n")
    (root / "sub" / "x.jsonl").write_text('{"a": 1}\n')
    (root / "secret.key").write_text("nope")
    outside = tmp_path / "outside.log"
    outside.write_text("outside\n")
    (root / "link.log").symlink_to(outside)
    monkeypatch.setenv("BTH_APP_LOG_ROOTS", f"app={root}")
    return root


def test_list_tail_and_search(logroot):
    files = {f["path"] for f in applogs.list_logs()["roots"]["app"]["files"]}
    assert {"app.log", "sub/x.jsonl"} <= files and "secret.key" not in files and "link.log" not in files
    tail = applogs.read_log("app", "app.log", lines=2)
    assert tail["lines"] == ["line 299", "ERROR boom"]
    hits = applogs.search_logs("error", "app")["matches"]
    assert hits and hits[0]["line_no"] == 301


@pytest.mark.parametrize("path", ["../outside.log", "/etc/passwd", "link.log", "secret.key", ""])
def test_paths_outside_the_root_are_refused(logroot, path):
    with pytest.raises(ValueError):
        applogs.read_log("app", path)


def test_unknown_root(logroot):
    out = json.loads(tools_core.t_read_app_log({"root": "nope", "path": "app.log"})[0])
    assert not out["ok"] and "unknown log root" in out["error"]


def test_bl41_default_roots(monkeypatch):
    monkeypatch.delenv("BTH_APP_LOG_ROOTS", raising=False)
    monkeypatch.setenv("BTH_STATION", "bl41")
    assert {"fourone", "cherfd", "xsp32"} <= set(applogs.roots())


# --- provenance / export -------------------------------------------------------

def test_bundle_has_a_manifest_and_zip(tmp_path, monkeypatch):
    from beamtimehero_cli import config
    scan = tmp_path / "scans"
    scan.mkdir()
    (scan / "Fe_s1.01").write_text("#S 1\n")
    (scan / "sub").mkdir()
    (scan / "sub" / "Fe_s1.01").write_text("#S 2\n")
    monkeypatch.setattr(config, "BL_SCAN_DIR", scan)
    monkeypatch.setenv("BTH_EXPORT_DIR", str(tmp_path / "exp"))
    out = provenance.export_bundle(["Fe_s1.01", str(scan / "sub" / "Fe_s1.01")], "beam time 1",
                                   {"proposal": "1234"})
    man = json.loads(open(out["manifest"]).read())
    assert [f["file"] for f in man["files"]] == ["Fe_s1.01", "Fe_s1_1.01"]
    assert len(man["files"][0]["sha256"]) == 64 and man["metadata"] == {"proposal": "1234"}
    assert zipfile.ZipFile(out["zip"]).namelist()
    with pytest.raises(ValueError, match="allowed data root"):
        provenance.export_bundle(["/etc/hostname"], "x")


def test_provenance_is_append_only(monkeypatch):
    provenance.record_provenance("exp1", "mounted", "s1", {"holder": "h1"})
    provenance.record_provenance("exp1", "measured", "s2")
    assert [r["event"] for r in provenance.read_provenance("exp1")["records"]] == ["mounted", "measured"]
    assert len(provenance.read_provenance("exp1", sample="s2")["records"]) == 1


# --- BL4-1 SPEC extras (SPEC mock) ---------------------------------------------

def test_measure_darks_at_bl41(monkeypatch):
    monkeypatch.setenv("BTH_STATION", "bl41")
    out = json.loads(tools_core.t_measure_darks({"justification": "darks after gain change",
                                                 "count_time_s": 2})[0])
    assert out["ok"], out
    assert out["result"]["offsets_cps"] == {"I0": 1200.5, "I1": 850.25, "I2": 210.0, "pips": 31.5}


def test_bl41_spec_extras_refused_elsewhere(monkeypatch):
    monkeypatch.delenv("BTH_STATION", raising=False)
    for fn in (tools_core.t_measure_darks, tools_core.t_get_crystal_set, tools_core.t_get_check_beam):
        out = json.loads(fn({"justification": "x"})[0])
        assert not out["ok"] and "BL4-1" in out["error"]


def test_check_beam_and_crystal(monkeypatch):
    monkeypatch.setenv("BTH_STATION", "bl41")
    on = json.loads(tools_core.t_set_check_beam({"justification": "x", "enabled": True})[0])
    assert on["ok"] and on["result"]["enabled"] is True
    assert json.loads(tools_core.t_get_check_beam({})[0])["result"]["enabled"] is True
    xt = json.loads(tools_core.t_get_crystal_set({})[0])
    assert xt["result"]["crystal"] == "A" and xt["result"]["name"] == "Si(220)"


def test_slack_dm(monkeypatch):
    from beamtimehero_cli.notify import slack

    class C:
        def conversations_open(self, users):
            return {"channel": {"id": "D1"}}

        def chat_postMessage(self, channel, text):
            return {"ts": "1.2"}
    monkeypatch.setattr(slack, "_get_client", lambda: C())
    out = json.loads(tools_core._HANDLERS["post_slack_dm"]({"user_id": "U1", "text": "hi"})[0])
    assert out == {"status": "posted", "channel": "D1", "ts": "1.2", "user_id": "U1"}
