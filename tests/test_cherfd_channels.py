"""cherfd_channel_stats: per-channel rates, ratios and edge jumps, synthetic and real."""
from __future__ import annotations

from tests._cherfd_fixtures import *  # noqa: F401,F403 -- isolation fixtures
from tests._cherfd_fixtures import SIBLING, run_tool as run

import numpy as np
import pandas as pd
import pytest

from beamtimehero_cli.science.cherfd import channels as ch

FE_RUN = "2026-10-11_Fe/Fe_pips_200Hz@2026-10-11_180330"


def _arrays(rng, n=4000, period_us=10000.0, mu_jump=1.5):
    e = np.linspace(7080.0, 7220.0, n)
    mu = np.where(e > 7112.0, mu_jump, 0.0) + 0.2
    i0 = rng.poisson(500, n).astype(float)
    i1 = rng.poisson(500 * np.exp(-mu)).astype(float)
    return {"absev": e, "trig_2": np.full(n, period_us), "counter_1": i0, "counter_2": i1,
            "adc_1": np.full(n, 1600.0) + rng.normal(0, 5, n), "counter_3": np.zeros(n)}


def test_rates_poisson_ratio_and_adc():
    s = ch.sweep_channel_stats(_arrays(np.random.default_rng(0)), channels=["counter_1", "adc_1"])
    c1 = s["channels"]["counter_1"]
    assert s["gate_s"] == pytest.approx(0.01)
    assert c1["per_frame"]["mean"] == pytest.approx(500, rel=0.01)
    assert c1["per_second"]["mean"] == pytest.approx(50000, rel=0.01)
    assert c1["poisson_ratio"] == pytest.approx(1.0, abs=0.05)
    adc = s["channels"]["adc_1"]
    assert adc["kind"] == "adc_mean" and "per_second" not in adc and "poisson_ratio" not in adc


def test_edge_jump_and_window():
    a = _arrays(np.random.default_rng(1))
    s = ch.sweep_channel_stats(a, channels=["counter_1"], ratios=["counter_1/counter_2"],
                               window=(7090, 7200), pre=(7085, 7105), post=(7130, 7210))
    r = s["ratios"]["ln(counter_1/counter_2)"]
    assert r["pre_mean"] == pytest.approx(0.2, abs=0.02)
    assert r["jump"] == pytest.approx(1.5, abs=0.03)
    assert 7090 <= s["energy_ev"][0] and s["energy_ev"][1] <= 7200


def test_nonpositive_frames_excluded_and_counted():
    lr, bad = ch.log_ratio(np.array([1.0, 0.0, 2.0, np.nan]), np.array([1.0, 1.0, -1.0, 1.0]))
    assert lr.tolist() == [0.0] and bad == 2


def test_default_channels_skip_all_zero_and_add_signal():
    a = _arrays(np.random.default_rng(2))
    a["xsum"] = np.ones_like(a["absev"])
    assert ch.default_channels(a, "xsum") == ["adc_1", "counter_1", "counter_2", "xsum"]
    assert ch.default_channels(a, "vortex") == ["adc_1", "counter_1", "counter_2"]


def test_bad_inputs_refused():
    a = _arrays(np.random.default_rng(3))
    with pytest.raises(ValueError, match="numerator/denominator"):
        ch.sweep_channel_stats(a, channels=["counter_1"], ratios=["counter_1"])
    with pytest.raises(ValueError, match="not in this sweep"):
        ch.sweep_channel_stats(a, channels=["nope"])
    with pytest.raises(ValueError, match="no frames"):
        ch.sweep_channel_stats(a, channels=["counter_1"], window=(9000, 9100))


def test_run_trend_sees_i0_decay():
    rng = np.random.default_rng(4)
    per = []
    for k in range(6):
        a = _arrays(rng)
        a["counter_1"] = a["counter_1"] * (1 - 0.02 * k)
        per.append(ch.sweep_channel_stats(a, channels=["counter_1"], ratios=["counter_1/counter_2"],
                                          pre=(7085, 7105), post=(7130, 7210)))
    t = ch.run_channel_summary(per)
    i0 = t["channels"]["counter_1"]["per_frame_mean"]
    assert i0["n"] == 6 and i0["slope_fraction_per_sweep"] == pytest.approx(-0.022, abs=0.004)
    assert "jump" in t["ratios"]["ln(counter_1/counter_2)"]
    assert ch.run_channel_summary(per[:1])["channels"]["counter_1"]["per_frame_mean"] == {
        "n": 1, "mean": per[0]["channels"]["counter_1"]["per_frame"]["mean"]}


def test_tool_on_synthetic_files(tmp_path, monkeypatch):
    d = tmp_path / "data" / "chk"
    d.mkdir(parents=True)
    rng = np.random.default_rng(5)
    for n, (direction, ts) in enumerate([("fwd", "2026-10-11_100000"), ("rev", "2026-10-11_100010")]):
        a = _arrays(rng, n=500)
        k = np.arange(1, 501)
        df = pd.DataFrame({"trig_1": k * 10000.0, **a})
        df.to_pickle(d / f"scan_results_s_1_{direction}_{ts}_dataframe.pkl")
    monkeypatch.setenv("CHERFD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BTH_STATION", "bl41")
    r, imgs = run("cherfd_channel_stats", file_dir="chk", file_root="s",
                  ratios=["counter_1/counter_2"], pre_edge_ev=[7085, 7105], post_edge_ev=[7130, 7210])
    assert not imgs and r["channels"] == ["adc_1", "counter_1", "counter_2"]
    assert [s["sweep"] for s in r["sweeps"]] == ["1f", "1r"]
    assert set(r["run"]["by_direction"]) == {"fwd", "rev"}
    assert r["run"]["all"]["ratios"]["ln(counter_1/counter_2)"]["jump"]["mean"] == pytest.approx(1.5, abs=0.05)
    bad, _ = run("cherfd_channel_stats", file_dir="chk", file_root="s", pre_edge_ev=[7085, 7105])
    assert not bad["ok"] and "both" in bad["error"]


@pytest.fixture
def fe_data(monkeypatch):
    root = SIBLING / "data"
    if not (root / "2026-10-11_Fe").is_dir():
        pytest.skip("2026-10-11_Fe sample data not available")
    monkeypatch.setenv("CHERFD_DATA_DIR", str(root))
    monkeypatch.setenv("BTH_STATION", "bl41")
    return root


def test_real_fe_transmission(fe_data):
    """BL4-1 Fe foil, box counters only: I0 rate, sample edge jump, fwd/rev frame weighting."""
    r, _ = run("cherfd_channel_stats", run_id=FE_RUN, ratios=["counter_1/counter_2", "counter_2/counter_3"],
               pre_edge_ev=[7060, 7100], post_edge_ev=[7150, 7200])
    assert len(r["sweeps"]) == 20 and r["sweeps"][0]["gate_s"] == pytest.approx(0.005)
    i0 = r["run"]["all"]["channels"]["counter_1"]["per_second_mean"]
    assert i0["mean"] == pytest.approx(59700, rel=0.02) and abs(i0["spread_fraction"]) < 0.03
    jump = r["run"]["all"]["ratios"]["ln(counter_1/counter_2)"]["jump"]
    assert jump["mean"] == pytest.approx(1.671, abs=0.01) and jump["spread_fraction"] < 0.01
    # the mirrored reverse command puts more frames below the edge, so the
    # frame-weighted window mean of I1 is higher in reverse
    by = r["run"]["by_direction"]
    assert (by["rev"]["channels"]["counter_2"]["per_frame_mean"]["mean"]
            > 1.2 * by["fwd"]["channels"]["counter_2"]["per_frame_mean"]["mean"])
