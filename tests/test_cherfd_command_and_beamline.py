"""Command rules and the ports of cherfd_claude physics, pinned to the originals.

REF values were printed by the cherfd_claude functions themselves
(utils/mono.py, utils/undulator.py, cscan_box/motion.py, triggers.py).
"""
from __future__ import annotations

from tests._cherfd_fixtures import *  # noqa: F401,F403 -- isolation fixtures
from tests._cherfd_fixtures import REAL_RUN, run_tool as run  # noqa: F401

import pytest

from beamtimehero_cli.science.cherfd import beamline, command as cmd

REF = {
    "bragg_B_9000": 24.881211511989704, "bragg_A_9000": 12.692789717296405,
    "energy_B_24.5": 9131.195495557535,
    "steps_8968_9100": -38652, "steps_7090_7200": -55131,
    "gap_9000": (5, 8.103432445), "gap_7100": (3, 9.966015563700003), "gap_12000": (7, 7.82653825),
    "gap_h7_11500": 7.59917659375,
    "trig_60": (62.5, 16, 625), "trig_100": (100.0, 10, 1000),
    "rev": "cherfd 7200 7130 1 7115 .5 7105 .1 7090 .5 20",
}


def test_bragg_parity():
    assert beamline.bragg_angle_deg(9000, "B") == pytest.approx(REF["bragg_B_9000"], abs=1e-9)
    assert beamline.bragg_angle_deg(9000, "A") == pytest.approx(REF["bragg_A_9000"], abs=1e-9)
    assert beamline.energy_from_bragg(24.5, "B") == pytest.approx(REF["energy_B_24.5"], abs=1e-6)


def test_steps_parity():
    assert beamline.motor_steps(8968, 9100) == REF["steps_8968_9100"]
    assert beamline.motor_steps(7090, 7200) == REF["steps_7090_7200"]


@pytest.mark.parametrize("e,key", [(9000, "gap_9000"), (7100, "gap_7100"), (12000, "gap_12000")])
def test_harmonic_gap_parity(e, key):
    h = beamline.harmonic_gap(e)
    assert (h["harmonic"], h["ideal_gap_mm"]) == pytest.approx(REF[key])


def test_gap_poly_parity():
    assert beamline.gap_for_harmonic(7, 11500) == pytest.approx(REF["gap_h7_11500"])


@pytest.mark.parametrize("f,key", [(60, "trig_60"), (100, "trig_100")])
def test_trigger_parity(f, key):
    t = beamline.trigger_timing(f, 10)
    assert (t["real_freq_hz"], t["period_ms"], t["n_triggers"]) == REF[key]


def test_reverse_parity():
    got = cmd.parse_command(cmd.reverse_command("cherfd 7090 7105 .5 7115 .1 7130 .5 7200 1 20"))
    assert got == cmd.parse_command(REF["rev"])


def test_scan_rate_matches_simulator():
    # production simulator: 'cherfd 8968 9100 .4 100' -> mean 13.514 eV/s, 9.768 s motion
    assert beamline.ev_per_second(9034, 0.4) == pytest.approx(13.5, rel=0.02)
    v = cmd.validate_command("cherfd 8968 9100 .4 100")
    assert v["estimated_motion_s"] == pytest.approx(9.66, abs=0.15)


def test_validation_layers():
    v = cmd.validate_command("cherfd 8968 8990 1 9010 .15 9100 1 60")
    assert v["ok"]
    assert v["effective_command"] == "cherfd 8968 8990 1 9010 0.2 9100 1 60"
    assert any("clamped to 0.2" in w for w in v["warnings"])
    assert any("62.5" in w for w in v["warnings"])


@pytest.mark.parametrize("bad,needle", [
    ("cherfd 4000 9100 .4 100", "outside"),
    ("cherfd 8968 9100 9050 .4 100", "pairs"),
    ("cherfd 8968 9100 .4 9000 .4 100", "monotonic"),
    ("cherfd 8968 9100 .05 100", "SPEC"),
    ("cherfd 8968 9100 .4 99.5", "integer"),
    ("cherfd 8968 abc .4 100", "non-numeric"),
])
def test_validation_refusals(bad, needle):
    v = cmd.validate_command(bad)
    assert not v["ok"] and any(needle in e for e in v["errors"]), v["errors"]


def test_harmonic_change_detected():
    # 5th -> 3rd harmonic transition sits between 7100 and 9000 eV
    assert beamline.harmonic_changes(7100, 9000)


def test_build_command_hits_density():
    b = cmd.build_command(8979.0, edge_points_per_ev=10, freq_hz=100)
    edge = b["validation"]["regions"][1]
    assert b["validation"]["ok"]
    assert edge["points_per_ev"] == pytest.approx(10, rel=0.03)


def test_build_command_reports_clamp():
    b = cmd.build_command(8979.0, edge_points_per_ev=100, freq_hz=20)
    assert b["edge_speed"] == 0.2 and b["notes"]


def test_estimate():
    e = cmd.estimate_acquisition_time(13.85, 5, True, overhead_s=16.93)
    assert e["profiles"] == 10 and e["total_s"] == pytest.approx(307.8, abs=0.1)
