"""cherfd_build_exafs_command: equal-k regions keep the k spacing <= dk."""
from __future__ import annotations

import json

import pytest

from beamtimehero_cli.science.cherfd import command as c, policy as p

BL41 = p.STATION_PROFILES["bl41"]


def test_spacing_never_exceeds_dk_unless_clamped():
    out = c.build_exafs_command(7112.0, 12.0, freq_hz=20, dk=0.05, crystal="A", station=BL41)
    assert out["validation"]["ok"], out["validation"]
    parsed = c.parse_command(out["command"])
    assert parsed["direction"] == "increasing"
    assert abs(parsed["final_ev"] - (7112.0 + 12.0 ** 2 / p.ETOK)) < 0.1
    for r in out["k_regions"]:
        assert r["dk_at_k_lo"] <= 0.05 + 1e-4, r
    speeds = [r["speed"] for r in out["k_regions"]]
    assert speeds == sorted(speeds)                    # faster with k


def test_clamps_are_reported():
    out = c.build_exafs_command(7112.0, 12.0, freq_hz=100, crystal="A", station=BL41)
    assert any("clamped" in n for n in out["notes"])


@pytest.mark.parametrize("kw", [{"k_max": 1.0}, {"k_max": 12, "dk": 0}, {"k_max": 12, "k_regions": 0}])
def test_bad_arguments(kw):
    with pytest.raises(ValueError):
        c.build_exafs_command(7112.0, station=BL41, crystal="A", **{"k_max": 12, **kw})


def test_tool(monkeypatch):
    monkeypatch.setenv("BTH_STATION", "bl41")
    from beamtimehero_cli.cherfd_control import settings
    monkeypatch.setattr(settings, "station_crystal", lambda profile=None: "A")
    from beamtimehero_cli.tool_catalog import tools_cherfd
    text, _ = tools_cherfd.HANDLERS["cherfd_build_exafs_command"]({"element": "Fe", "k_max": 11})
    out = json.loads(text)
    assert out["command"].startswith("cherfd ") and out["edge_info"]["element"] == "Fe"
