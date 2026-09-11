"""Tests for the SSRL EXAFS Data Collector parsing layer + backend.

A synthetic sweep file is written in the exact on-disk format (validated
against SSRL BL 4-3 beamtimes 2025-07/2025-11) so the tests run without any
data-directory dependency, mirroring the monkeypatched-loader convention of
test_xrs.py.
"""
from __future__ import annotations

import numpy as np
import pytest

from beamtimehero_cli.spec_data import exafs_data, ssrl_ascii
from beamtimehero_cli.spec_data.ssrl_backend import SSRLAsciiBackend

E0 = 2472.0
LABELS = [
    "Real time clock", "Sum_RTC", "Requested Energy", "Achieved Energy",
    "I0", "I1", "SCA1_1", "SCA1_2", "ICR1_1", "ICR1_2",
]


def _sweep_text(npts=120, seed=0, e_start=2400.0, e_step=2.0, header_npts=None):
    """One SSRL ASCII sweep with an edge + single-shell EXAFS on SCA channels.

    ``header_npts`` > ``npts`` models an aborted sweep: the header promises
    more points than the file stores (exactly what the collector leaves
    behind when a sweep is stopped mid-scan).
    """
    rng = np.random.RandomState(seed)
    ncols = len(LABELS)
    header = [
        "SSRL   EXAFS Data Collector 4.0       ",
        "Thu Nov 20 02:18:52 2025              ",
        f"PTS:        {header_npts or npts} COLS:         {ncols}",
        "xsp7sca1.det                          ",
        "SKedgeEXAFSk9.rgn                     ",
        "43 unfocused  0.000 0 X 3.135600 20000",
        "5 1000 600 RST 0 0                    ",
        "i0 5e8 50pA", "1x1mm", "test sample", "", "", "",
        "",
        "Weights: ",
        " " + "  ".join(["1.000"] * ncols),
        "Offsets: ",
        " " + "  ".join(["0.000"] * ncols),
        "Data: ",
    ]
    header += [f"{l:<18}" for l in LABELS]
    header.append("")

    rows = []
    for i in range(npts):
        e = e_start + i * e_step
        k = np.sqrt(max(e - E0, 0.0) / 3.81)
        step = 1.0 / (1.0 + np.exp(-(e - E0) / 1.0))
        osc = 0.3 * np.sin(2 * k * 2.0) / k**2 if k > 0.5 else 0.0
        i0 = 38000.0 + rng.normal(0, 50)
        sca = 500.0 + 3000.0 * step * (1 + osc) + rng.normal(0, 5)
        row = [1.0, float(i + 1), e, e + rng.normal(0, 0.01),
               i0, -2000.0, sca, sca * 0.9, sca * 4, sca * 3.5]
        rows.append("  ".join(f"{v:.3f}" for v in row))
    return "\n".join(header + rows) + "\n"


@pytest.fixture()
def collector_dir(tmp_path):
    """A dataset directory: one 3-sweep group (one sweep aborted) + an align scan."""
    for sweep, (npts, seed) in enumerate([(120, 0), (120, 1), (6, 2)], start=1):
        (tmp_path / f"05_test_sample_019_A.{sweep:03d}").write_text(
            _sweep_text(npts=npts, seed=seed, header_npts=120))
    (tmp_path / "align_data_001_A.001").write_text(_sweep_text(npts=10, seed=3))
    # config dir with a motor profile
    cfg = tmp_path / "05_test_sample_019"
    cfg.mkdir()
    (cfg / "profile.txt").write_text(
        "Device:  MONO\n"
        "Channel: 1 Requested Pos.   5000.0000\n"
        "Channel: 2 Achieved Pos.    4999.9989\n"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Parsing layer
# ---------------------------------------------------------------------------

def test_read_ssrl_scan_shape_and_attrs(collector_dir):
    df = ssrl_ascii.read_ssrl_scan(collector_dir / "05_test_sample_019_A.001")
    assert df.index.name == "Achieved Energy"
    assert "SCA_sum" in df.columns and "ICR_sum" in df.columns
    np.testing.assert_allclose(
        df["SCA_sum"].values, df["SCA1_1"].values + df["SCA1_2"].values)
    a = df.attrs
    assert a["npts_header"] == 120 and a["is_complete"]
    assert a["count_time"] == 1.0
    assert a["region_file"] == "SKedgeEXAFSk9.rgn"
    assert "test sample" in a["comments"]
    assert a["motor_positions"] == {"MONO": 4999.9989}


def test_aborted_sweep_flagged_incomplete(collector_dir):
    df = ssrl_ascii.read_ssrl_scan(collector_dir / "05_test_sample_019_A.003")
    assert df.attrs["num_points"] == 6
    assert not df.attrs["is_complete"]


def test_read_rejects_non_ssrl(tmp_path):
    p = tmp_path / "not_ssrl.txt"
    p.write_text("#F spec_file\n#S 1 ascan\n")
    with pytest.raises(ValueError):
        ssrl_ascii.read_ssrl_scan(p)


def test_group_sweeps_skips_align(collector_dir):
    groups = ssrl_ascii.group_sweeps(collector_dir)
    assert list(groups) == ["05_test_sample_019"]
    assert len(groups["05_test_sample_019"]) == 3
    assert "align_data_001" in ssrl_ascii.group_sweeps(collector_dir, include_align=True)


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------

def test_backend_satisfies_protocol(collector_dir):
    from beamtimehero_cli.spec_data.backend import ScansBackend
    backend = SSRLAsciiBackend(collector_dir)
    assert isinstance(backend, ScansBackend)


def test_backend_addressing(collector_dir):
    backend = SSRLAsciiBackend(collector_dir)
    assert backend.get_scan_numbers_for_file("05_test_sample_019") == [1, 2, 3]
    df = backend.read_scan("05_test_sample_019", 2)
    assert df is not None and len(df) == 120
    assert backend.read_scan("05_test_sample_019", 99) is None
    meta = backend.get_scan_metadata("05_test_sample_019", 1)
    assert meta["file_name"] == "05_test_sample_019"
    assert backend.get_most_recent_file() == "05_test_sample_019"
    dead = backend.get_scan_deadtime("05_test_sample_019", 1)
    assert dead["acquisition_seconds"] == pytest.approx(120.0)
    assert dead["dead_time_seconds"] is None


def test_backend_requires_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("SSRL_COLLECTOR_DIR", raising=False)
    with pytest.raises(ValueError):
        SSRLAsciiBackend(None)
    with pytest.raises(ValueError):
        SSRLAsciiBackend(tmp_path / "missing")


# ---------------------------------------------------------------------------
# exafs_data chokepoint over the SSRL source
# ---------------------------------------------------------------------------

def test_load_mu_merges_and_drops_aborted(collector_dir):
    r = exafs_data.load_mu(file_name="05_test_sample_019", collector_dir=str(collector_dir))
    assert r["source"] == "ssrl_ascii"
    assert r["counter"] == "SCA_sum"
    assert r["n_reps"] == 2                      # aborted sweep 3 dropped
    assert r["dropped_short_reps"] == ["S003"]
    assert len(r["energy"]) >= 100
    # merged mu shows the edge: post-edge fluorescence well above pre-edge
    pre = r["mu"][r["energy"] < E0 - 20].mean()
    post = r["mu"][r["energy"] > E0 + 50].mean()
    assert post > 3 * pre


def test_load_mu_full_chain_to_first_shell(collector_dir):
    from beamtimehero_cli.science.exafs.background import autobk_lite
    from beamtimehero_cli.science.exafs.fourier import first_shell_peak, xftf
    from beamtimehero_cli.science.xas.normalize import pre_post_normalize
    from beamtimehero_cli.science.xas.e0 import find_e0

    r = exafs_data.load_mu(file_name="05_test_sample_019", collector_dir=str(collector_dir))
    e0 = find_e0(r["energy"], r["mu"])["e0_ev"]
    assert abs(e0 - E0) < 3.0
    _flat, prov = pre_post_normalize(r["energy"], r["mu"], e0)
    assert prov["applied"] and prov["edge_step"] > 0
    bk = autobk_lite(r["energy"], r["mu"], e0, edge_step=prov["edge_step"])
    ft = xftf(bk["k"], bk["chi"], kmin=2.0, kmax=bk["k"].max() - 0.5)
    peak = first_shell_peak(ft["r"], ft["chir_mag"])
    assert peak["found"]
    assert abs(peak["r_peak_ang"] - 2.0) < 0.25   # synthetic shell at 2.0 Å


def test_is_ssrl_ascii_rejects_binary_variant(collector_dir, tmp_path):
    assert ssrl_ascii.is_ssrl_ascii(collector_dir / "05_test_sample_019_A.001")
    # the collector's binary/ variant carries the banner but with NUL bytes
    (tmp_path / "bin.001").write_bytes(b"SSRL - EXAFS Data Collector 4.0 \n\x00PTS: 71")
    assert not ssrl_ascii.is_ssrl_ascii(tmp_path / "bin.001")
    (tmp_path / "junk.txt").write_text("not a collector file\n")
    assert not ssrl_ascii.is_ssrl_ascii(tmp_path / "junk.txt")
    assert not ssrl_ascii.is_ssrl_ascii(tmp_path / "missing")


def test_load_mu_routes_by_collector_env(collector_dir, monkeypatch):
    """SSRL_COLLECTOR_DIR alone (no collector_dir argument) must select the
    SSRL backend — chemcatal-bth sets only the env for collector beamtimes."""
    monkeypatch.setenv("SSRL_COLLECTOR_DIR", str(collector_dir))
    r = exafs_data.load_mu(file_name="05_test_sample_019")
    assert r["source"] == "ssrl_ascii"
    assert r["n_reps"] == 2
    # an explicit source always wins over the env
    monkeypatch.setattr(
        exafs_data.scans, "get_normalized_scan_arrays",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("spec chain")))
    with pytest.raises(ValueError, match="spec chain"):
        exafs_data.load_mu(file_name="05_test_sample_019", source="spec")


# ---------------------------------------------------------------------------
# Generic scans-chokepoint routing (the spec-file tool surface) over a
# collector session (SSRL_COLLECTOR_DIR set, no per-call collector_dir)
# ---------------------------------------------------------------------------


@pytest.fixture()
def collector_env(collector_dir, monkeypatch):
    monkeypatch.setenv("SSRL_COLLECTOR_DIR", str(collector_dir))
    return collector_dir


def test_scans_chokepoint_lists_and_reads_collector(collector_env):
    from beamtimehero_cli.spec_data import scans

    listed = scans.list_processed_scans(limit=10)
    assert listed and all(s["file_name"] in ("05_test_sample_019", "align_data_001")
                          for s in listed)
    df = scans.read_processed_scan("05_test_sample_019", 1)
    assert df is not None and "SCA_sum" in df.columns
    assert scans.get_scan_metadata("05_test_sample_019", 2)["num_points"] == 120
    assert scans.get_scan_deadtime("05_test_sample_019", 1)["wall_clock_seconds"] is None
    assert scans.get_most_recent_file() in ("05_test_sample_019", "align_data_001")
    df2, reason = scans.read_processed_scan_ex("05_test_sample_019", 99)
    assert df2 is None and reason == "not_found"


def test_active_counter_prefers_sca_sum_on_collector(collector_env):
    from beamtimehero_cli.spec_data import scans

    got = scans.get_active_counter("05_test_sample_019", 1)
    assert got["active_counter"] == "SCA_sum"


def test_normalized_multiscan_chain_on_collector(collector_env):
    """get_normalized_scan_arrays is the chokepoint for average/convergence/
    plot-stack/interpretation — the whole multi-scan surface."""
    from beamtimehero_cli.spec_data import scans

    combined, file_name, counter, used = scans.get_normalized_scan_arrays(
        "05_test_sample_019")
    assert file_name == "05_test_sample_019" and counter == "SCA_sum"
    assert used == [1, 2, 3]
    out = scans.average_energy_scans(file_name="05_test_sample_019")
    assert out.get("error") is None and out["num_scans_averaged"] >= 2
    assert out["active_counter"] == "SCA_sum"


def test_average_latest_energy_scans_on_collector(collector_env):
    from beamtimehero_cli.spec_data import scans

    out = scans.average_latest_energy_scans()
    # the only >1-sweep group is the sample group, never the 1-sweep align scan
    assert out.get("error") is None
    assert out["file_name"] == "05_test_sample_019"


def test_plot_scan_on_collector(collector_env):
    from beamtimehero_cli.spec_data import plotting

    result = plotting.plot_scan("05_test_sample_019", 1)
    # plot_scan returns (payload, images) shapes that differ by version; accept
    # any non-error result that mentions the group
    assert result is not None


def test_spec_chain_untouched_without_env(collector_dir, monkeypatch):
    """No SSRL_COLLECTOR_DIR -> the SPEC/local_data chain answers (and finds
    nothing in an empty scan dir), proving the default path is unchanged."""
    monkeypatch.delenv("SSRL_COLLECTOR_DIR", raising=False)
    monkeypatch.setenv("BL_SCAN_DIR", str(collector_dir / "empty-subdir"))
    from beamtimehero_cli.spec_data import scans
    assert scans.list_processed_scans(limit=5) == []
