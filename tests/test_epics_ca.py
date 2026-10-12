"""The ``caget`` tool: read-only Channel Access with the host's CA config.

No network: ``subprocess.run`` is replaced with a fake that records argv
and answers like caget does. The read-only guarantee is checked twice —
on what the module runs, and on its source, so a later edit that adds a
write path fails here rather than shipping.
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from beamtimehero_cli import epics_ca
from beamtimehero_cli.tool_catalog import categorize
from beamtimehero_cli.tool_catalog import TOOL_DEFINITIONS
from beamtimehero_cli.tool_catalog.lineage import TOOL_LINEAGE
from beamtimehero_cli.tool_catalog.tools_core import t_caget

_TS = "2026-10-12 15:18:21.068336"
_ANSWERS = {
    "SPEAR:BeamCurrAvg": f"SPEAR:BeamCurrAvg              {_TS} 1 496.468  \n",
    "BL04:State": f"BL04:State                     {_TS} 1 Open  \n",
    "X:DESC": f"X:DESC                         {_TS} 1  Average Beam Current  \n",
    "X:HOT": f"X:HOT                          {_TS} 1 99.5 HIHI MAJOR\n",
    "X:WF": f"X:WF                           {_TS} 3 1 2 3  \n",
}


@pytest.fixture
def fake_caget(monkeypatch, tmp_path):
    calls: list[dict] = []

    def run(argv, **kw):
        calls.append({"argv": argv, "env": kw.get("env"), "shell": kw.get("shell", False)})
        pv = argv[-1]
        if pv in _ANSWERS:
            return subprocess.CompletedProcess(argv, 0, _ANSWERS[pv], "")
        return subprocess.CompletedProcess(
            argv, 1, "", f"Channel connect timed out: '{pv}' not found.\n")

    monkeypatch.setattr(epics_ca.subprocess, "run", run)
    monkeypatch.setattr(epics_ca, "find_caget", lambda env: "/opt/epics/bin/caget")
    monkeypatch.setattr(epics_ca, "HOST_EPICS_PROFILE", tmp_path / "missing.sh")
    monkeypatch.setenv("EPICS_CA_ADDR_LIST", "10.0.0.255")
    monkeypatch.setenv("EPICS_CA_AUTO_ADDR_LIST", "NO")
    return calls


def test_reads_values_alarms_and_arrays(fake_caget):
    r = epics_ca.caget(["SPEAR:BeamCurrAvg", "BL04:State", "X:DESC", "X:HOT", "X:WF"])
    assert r["ok"] is True
    by = {x["pv"]: x for x in r["results"]}
    assert by["SPEAR:BeamCurrAvg"]["value"] == "496.468"
    assert by["SPEAR:BeamCurrAvg"]["timestamp"] == _TS
    assert by["SPEAR:BeamCurrAvg"]["severity"] == "NO_ALARM"
    assert by["BL04:State"]["value"] == "Open"
    assert by["X:DESC"]["value"] == "Average Beam Current"
    assert (by["X:HOT"]["value"], by["X:HOT"]["status"], by["X:HOT"]["severity"]) == (
        "99.5", "HIHI", "MAJOR")
    assert by["X:WF"]["value"] == ["1", "2", "3"] and by["X:WF"]["count"] == 3
    assert r["ca_addr_list"] == "10.0.0.255"


def test_one_bad_pv_does_not_hide_the_others(fake_caget):
    r = epics_ca.caget(["SPEAR:BeamCurrAvg", "NOPE:X"])
    assert r["ok"] is False
    good, bad = r["results"]
    assert good["connected"] and good["value"] == "496.468"
    assert not bad["connected"] and "not found" in bad["error"]


def test_only_caget_runs_with_an_argv_list_and_no_shell(fake_caget):
    epics_ca.caget(["SPEAR:BeamCurrAvg", "BL04:State"], timeout_s=2.5, max_elements=7)
    assert len(fake_caget) == 2
    for call in fake_caget:
        argv = call["argv"]
        assert isinstance(argv, list) and call["shell"] is False
        assert Path(argv[0]).name == "caget"
        assert argv[1:7] == ["-w", "2.5", "-a", "-#", "7", "--"]


@pytest.mark.parametrize("pv", ["-h", "--help", "", "A B", "A;$(rm -rf /)`", "x" * 200, "A\nB"])
def test_malformed_pv_names_are_refused_before_anything_runs(fake_caget, pv):
    with pytest.raises(ValueError):
        epics_ca.caget([pv])
    assert fake_caget == []


def test_request_limits(fake_caget):
    with pytest.raises(ValueError):
        epics_ca.caget([])
    with pytest.raises(ValueError):
        epics_ca.caget([f"A:{i}" for i in range(epics_ca.MAX_PVS + 1)])
    with pytest.raises(ValueError):
        epics_ca.caget(["A"], timeout_s=0)
    with pytest.raises(ValueError):
        epics_ca.caget(["A"], timeout_s=epics_ca.MAX_TIMEOUT_S + 1)


def test_process_environment_addr_list_wins(fake_caget, tmp_path, monkeypatch):
    profile = tmp_path / "epics.sh"
    profile.write_text('export EPICS_CA_ADDR_LIST="1.2.3.4"\n')
    monkeypatch.setattr(epics_ca, "HOST_EPICS_PROFILE", profile)
    epics_ca.caget(["SPEAR:BeamCurrAvg"])
    assert fake_caget[0]["env"]["EPICS_CA_ADDR_LIST"] == "10.0.0.255"


def test_host_profile_fills_in_a_bare_environment(tmp_path, monkeypatch):
    profile = tmp_path / "epics.sh"
    profile.write_text(
        "export EPICS_BASE=/usr/local/epics/base\n"
        "case x in *) ;; esac\n"
        "export EPICS_CA_AUTO_ADDR_LIST=NO\n"
        'export EPICS_CA_ADDR_LIST="spearca1.slac.stanford.edu:5100 192.168.41.255"\n'
        'export EPICS_CA_EVIL="$(touch /tmp/x)"\n'
    )
    monkeypatch.delenv("EPICS_CA_ADDR_LIST", raising=False)
    monkeypatch.delenv("EPICS_CA_AUTO_ADDR_LIST", raising=False)
    env = epics_ca.ca_environment(profile)
    assert env["EPICS_CA_ADDR_LIST"] == "spearca1.slac.stanford.edu:5100 192.168.41.255"
    assert env["EPICS_CA_AUTO_ADDR_LIST"] == "NO"
    assert "EPICS_CA_EVIL" not in env  # shell expansions are not evaluated or copied


def test_missing_binary_is_an_error_field_not_an_exception(monkeypatch):
    monkeypatch.setattr(epics_ca, "find_caget", lambda env: None)
    r = epics_ca.caget(["SPEAR:BeamCurrAvg"])
    assert r["ok"] is False and "caget not found" in r["error"]


def test_the_module_has_no_write_path():
    """No CA write binary or call is named anywhere in the module's code."""
    tree = ast.parse(Path(epics_ca.__file__).read_text())
    strings = {n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    code_tokens = " ".join(names | attrs).lower()
    for word in ("caput", "ca_put", "pvput", "camonitor"):
        assert word not in code_tokens
    # The only executable named is caget.
    assert epics_ca._BINARY == "caget"
    assert not any(s.strip().split(" ")[0] in {"caput", "pvput", "sh", "bash"} for s in strings)
    imports = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
               for a in n.names}
    assert not imports & {"epics", "caproto", "p4p"}


def test_catalog_wiring_is_read_only():
    (tdef,) = [d for d in TOOL_DEFINITIONS if d["function"]["name"] == "caget"]
    assert TOOL_LINEAGE["caget"]["mutates"] is False
    assert TOOL_LINEAGE["caget"]["source"] == "epics_ioc"
    assert categorize.categorize(tdef) == ("tool",)
    assert "justification" not in tdef["function"]["parameters"]["properties"]


def test_handler_returns_json_and_accepts_a_plain_string(fake_caget):
    text, images = t_caget({"pvs": "SPEAR:BeamCurrAvg, BL04:State"})
    r = json.loads(text)
    assert images == [] and r["ok"] and len(r["results"]) == 2
    bad = json.loads(t_caget({"pvs": ["-w"]})[0])
    assert bad["ok"] is False and "may not start" in bad["error"]
