"""Merged two-column ingest + the cross-file XAS comparison leaves.

Covers the speciation-campaign capability set: sniffing/reading merged
two-column ASCII spectra, serving them through the
``get_normalized_scan_arrays`` chokepoint, XANES LCF
(``compare_xas_to_references``), energy re-registration (``align_spectra``),
difference spectra, and the multi-file overlay accepting merged files.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from beamtimehero_cli import config as bl_config
from beamtimehero_cli.science.xas.compare import align_spectra, difference_spectrum
from beamtimehero_cli.spec_data import local_data, scans, twocol_ascii


# ---------------------------------------------------------------------------
# Fixtures — synthetic SPEC + merged two-column files
# ---------------------------------------------------------------------------

def _write_spec(path, n_scans: int = 1) -> None:
    """Tiny SPEC file (same shape as tests/test_local_data_cache.py)."""
    lines = [
        f"#F {path}",
        "#E 1700000000",
        "#D Wed Jun 11 10:00:00 2026",
        "#O0 Sx  Sy  Sz",
        "",
    ]
    for i in range(n_scans):
        lines += [
            f"#S {i + 1}  ascan  energy 7100 7110 2 1",
            "#D Wed Jun 11 10:00:00 2026",
            "#T 1  (Seconds)",
            "#P0 1 2 3",
            "#N 4",
            "#L energy  Epoch  I0  I1",
            "7100 0 100 10",
            "7105 1 100 11",
            "7110 2 100 12",
            "",
        ]
    path.write_text("\n".join(lines))


_GRID = np.arange(5450.0, 5560.0, 0.5)  # La L3-ish window, 220 points


def _xanes(energy, e0, wl_height=0.8, wl_width=4.0, wl_offset=7.0):
    """Sigmoid edge + Gaussian white line — a normalized XANES look-alike."""
    edge = 1.0 / (1.0 + np.exp(-(energy - e0) / 1.2))
    white_line = wl_height * np.exp(
        -0.5 * ((energy - (e0 + wl_offset)) / wl_width) ** 2)
    return edge + white_line


def _write_merged(path, energy, intensity, comments=("merged by test",)) -> None:
    """Same on-disk shape as chemcatal xas_core specio.write_two_column_dat."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for line in comments:
            f.write(f"# {line}\n")
        for e, i in zip(energy, intensity):
            f.write(f"{e:.6f}  {i:.8g}\n")


@pytest.fixture
def scan_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bl_config, "BL_SCAN_DIR", tmp_path)
    monkeypatch.delenv("SSRL_COLLECTOR_DIR", raising=False)
    monkeypatch.setenv("BEAMTIMEHERO_CLI_LOG", "0")
    local_data.clear_cache()
    yield tmp_path
    local_data.clear_cache()


# ---------------------------------------------------------------------------
# Sniffing (is_twocol_ascii) + parsing (read_twocol)
# ---------------------------------------------------------------------------

def test_sniff_accepts_commented_two_col(tmp_path):
    p = tmp_path / "merged.dat"
    _write_merged(p, _GRID, _xanes(_GRID, 5483.0),
                  comments=("provenance line", "e0=5483"))
    assert twocol_ascii.is_twocol_ascii(p)


def test_sniff_accepts_bang_comments_and_commas(tmp_path):
    p = tmp_path / "export.txt"
    rows = ["! exported spectrum"]
    rows += [f"{e:.3f}, {i:.6f}" for e, i in zip(_GRID, _xanes(_GRID, 5483.0))]
    p.write_text("\n".join(rows))
    assert twocol_ascii.is_twocol_ascii(p)


def test_sniff_rejects_spec_file(tmp_path):
    p = tmp_path / "sample1"
    _write_spec(p)
    assert not twocol_ascii.is_twocol_ascii(p)


def test_sniff_rejects_three_columns_and_non_energy_axis(tmp_path):
    three = tmp_path / "three.dat"
    three.write_text("\n".join(f"{e} 1.0 2.0" for e in _GRID[:20]))
    assert not twocol_ascii.is_twocol_ascii(three)

    kspace = tmp_path / "chi.dat"  # 0–15 Å⁻¹ axis is not an eV energy axis
    k = np.linspace(0, 15, 100)
    kspace.write_text("\n".join(f"{x:.4f} {np.sin(x):.6f}" for x in k))
    assert not twocol_ascii.is_twocol_ascii(kspace)

    nonmono = tmp_path / "nonmono.dat"
    nonmono.write_text("5450 1\n5451 2\n5449 3\n5452 4\n5453 5\n5454 6\n")
    assert not twocol_ascii.is_twocol_ascii(nonmono)


def test_read_twocol_round_trip_and_descending_flip(tmp_path):
    p = tmp_path / "merged.dat"
    mu = _xanes(_GRID, 5483.0)
    _write_merged(p, _GRID[::-1], mu[::-1])  # descending on disk
    energy, intensity, meta = twocol_ascii.read_twocol(p)
    assert np.all(np.diff(energy) > 0)
    np.testing.assert_allclose(energy, _GRID, atol=1e-6)
    np.testing.assert_allclose(intensity, mu, rtol=1e-6)
    assert meta["n_points"] == len(_GRID)
    assert meta["comments"] == ["merged by test"]


# ---------------------------------------------------------------------------
# get_normalized_scan_arrays: merged source
# ---------------------------------------------------------------------------

def test_get_normalized_scan_arrays_serves_merged_file(scan_dir):
    mu = _xanes(_GRID, 5483.0)
    _write_merged(scan_dir / "MERGE" / "sampleA.dat", _GRID, mu)

    combined, file_name, counter, used = scans.get_normalized_scan_arrays(
        "MERGE/sampleA.dat")
    assert counter == "merged"
    assert used == [1]
    assert combined.shape[1] == 1
    assert combined.attrs["normalization"] == "raw"
    assert combined.attrs["counter"] == "merged"
    np.testing.assert_allclose(combined.index.values, _GRID, atol=1e-6)
    np.testing.assert_allclose(combined.iloc[:, 0].values, mu, rtol=1e-6)

    # basename resolution finds the file inside MERGE/
    combined2, _, counter2, _ = scans.get_normalized_scan_arrays("sampleA.dat")
    assert counter2 == "merged"
    np.testing.assert_allclose(
        combined2.iloc[:, 0].values, combined.iloc[:, 0].values)


def test_merged_file_respects_energy_window(scan_dir):
    _write_merged(scan_dir / "MERGE" / "sampleA.dat", _GRID, _xanes(_GRID, 5483.0))
    combined, _, _, _ = scans.get_normalized_scan_arrays(
        "MERGE/sampleA.dat", e_min=5470.0, e_max=5500.0)
    assert combined.index.min() >= 5470.0
    assert combined.index.max() <= 5500.0


def test_spec_files_keep_existing_behavior(scan_dir):
    _write_spec(scan_dir / "sample1", n_scans=2)
    combined, file_name, counter, used = scans.get_normalized_scan_arrays("sample1")
    assert file_name == "sample1"
    assert counter == "I1"  # no vortDT/ppboff counters in the fixture
    assert used == [1, 2]
    assert combined.attrs["normalization"] == "edge_step"

    with pytest.raises(ValueError, match="No scans found"):
        scans.get_normalized_scan_arrays("no_such_file")


def test_merged_files_do_not_break_specfile_gated_caching(scan_dir):
    _write_spec(scan_dir / "sample1")
    _write_merged(scan_dir / "MERGE" / "sampleA.dat", _GRID, _xanes(_GRID, 5483.0))

    cache = local_data._load_cache()
    assert all(e["file_name"] == "sample1" for e in cache.values()), (
        "merged ASCII files must never enter the SPEC metadata cache")
    listed = {f["path"] for f in local_data.list_files("*.dat")}
    assert "MERGE/sampleA.dat" in listed


# ---------------------------------------------------------------------------
# XANES LCF leaf
# ---------------------------------------------------------------------------

def test_lcf_recovers_known_fractions(scan_dir):
    from beamtimehero_cli.tool_catalog.tools_core import t_compare_xas_to_references

    ref_a = _xanes(_GRID, 5483.0, wl_height=0.9, wl_width=3.0, wl_offset=5.0)
    ref_b = _xanes(_GRID, 5483.5, wl_height=0.3, wl_width=8.0, wl_offset=12.0)
    mix = 0.3 * ref_a + 0.7 * ref_b
    _write_merged(scan_dir / "MERGE" / "refA.dat", _GRID, ref_a)
    _write_merged(scan_dir / "MERGE" / "refB.dat", _GRID, ref_b)
    _write_merged(scan_dir / "MERGE" / "mixture.dat", _GRID, mix)

    text, images = t_compare_xas_to_references({
        "file_name": "MERGE/mixture.dat",
        "references": [
            {"file_name": "MERGE/refA.dat", "label": "A"},
            {"file_name": "MERGE/refB.dat", "label": "B"},
        ],
    })
    result = json.loads(text)
    assert "error" not in result, result
    fractions = {c["name"]: c["fraction"] for c in result["components"]}
    assert fractions["A"] == pytest.approx(0.3, abs=0.05)
    assert fractions["B"] == pytest.approx(0.7, abs=0.05)
    assert result["fit_r2"] > 0.99
    assert result["residual_rms"] < 0.01
    assert "calibration_caveat" in result
    assert set(result["reference_e0s_ev"]) == {"A", "B"}
    assert "calibration_warning" not in result
    assert len(images) == 1 and len(images[0]) > 100  # b64 plot


def test_lcf_warns_on_reference_e0_spread(scan_dir):
    from beamtimehero_cli.tool_catalog.tools_core import t_compare_xas_to_references

    ref_a = _xanes(_GRID, 5483.0)
    ref_b = _xanes(_GRID, 5486.0)  # 3 eV apart — miscalibrated reference set
    _write_merged(scan_dir / "MERGE" / "refA.dat", _GRID, ref_a)
    _write_merged(scan_dir / "MERGE" / "refB.dat", _GRID, ref_b)
    _write_merged(scan_dir / "MERGE" / "target.dat", _GRID,
                  0.5 * ref_a + 0.5 * ref_b)

    text, _images = t_compare_xas_to_references({
        "file_name": "MERGE/target.dat",
        "references": [{"file_name": "MERGE/refA.dat"},
                       {"file_name": "MERGE/refB.dat"}],
    })
    result = json.loads(text)
    assert result["reference_e0_spread_ev"] > 1.0
    assert "calibration_warning" in result


# ---------------------------------------------------------------------------
# align_spectra — pure math + leaf
# ---------------------------------------------------------------------------

def test_align_spectra_recovers_2ev_shift():
    mu = _xanes(_GRID, 5483.0)
    shifted = _xanes(_GRID, 5485.0)  # same shape, +2 eV
    records = align_spectra([(_GRID, mu), (_GRID, shifted)])
    assert records[0]["shift_applied"] == pytest.approx(0.0, abs=0.01)
    assert records[1]["shift_applied"] == pytest.approx(-2.0, abs=0.3)
    assert not records[1]["refused"]
    assert records[1]["e0_after"] == pytest.approx(records[0]["e0_after"], abs=0.3)


def test_align_spectra_refuses_15ev_shift():
    grid = np.arange(5440.0, 5580.0, 0.5)
    mu = _xanes(grid, 5483.0)
    far = _xanes(grid, 5498.0)  # +15 eV — beyond plausible mono drift
    records = align_spectra([(grid, mu), (grid, far)])
    assert records[1]["refused"]
    assert records[1]["shift_applied"] == 0.0
    assert "glitch" in records[1]["note"]
    np.testing.assert_array_equal(records[1]["energy"], grid)


def test_align_spectra_explicit_target_e0():
    mu = _xanes(_GRID, 5483.0)
    records = align_spectra([(_GRID, mu)], target_e0=5484.0)
    assert records[0]["target_source"] == "explicit target_e0"
    assert records[0]["shift_applied"] == pytest.approx(
        5484.0 - records[0]["e0_before"], abs=1e-3)
    assert records[0]["e0_after"] == pytest.approx(5484.0, abs=1e-3)


def test_align_spectra_leaf_on_merged_files(scan_dir):
    from beamtimehero_cli.tool_catalog.tools_core import t_align_spectra

    _write_merged(scan_dir / "MERGE" / "a.dat", _GRID, _xanes(_GRID, 5483.0))
    _write_merged(scan_dir / "MERGE" / "b.dat", _GRID, _xanes(_GRID, 5485.0))

    text, images = t_align_spectra({
        "spectra": [{"file_name": "MERGE/a.dat", "label": "A"},
                    {"file_name": "MERGE/b.dat", "label": "B"}],
    })
    out = json.loads(text)
    assert "error" not in out, out
    by_label = {s["label"]: s for s in out["spectra"]}
    assert by_label["B"]["shift_applied"] == pytest.approx(-2.0, abs=0.3)
    assert not by_label["B"]["refused"]
    assert "no files were modified" in out["note"]
    assert len(images) == 1 and len(images[0]) > 100


def test_align_spectra_leaf_needs_two(scan_dir):
    from beamtimehero_cli.tool_catalog.tools_core import t_align_spectra

    text, images = t_align_spectra({"spectra": [{"file_name": "MERGE/a.dat"}]})
    assert "error" in json.loads(text)
    assert images == []


# ---------------------------------------------------------------------------
# difference_spectrum — pure math + leaf
# ---------------------------------------------------------------------------

def test_difference_of_shifted_identical_spectra_is_flat_after_align():
    mu_a = _xanes(_GRID, 5483.0)
    mu_b = _xanes(_GRID, 5485.0)
    aligned = difference_spectrum(_GRID, mu_a, _GRID, mu_b, align=True)
    raw = difference_spectrum(_GRID, mu_a, _GRID, mu_b, align=False)
    # Aligned: shape-identical spectra difference to ~0; the raw difference
    # carries the derivative-shaped calibration artifact.
    assert aligned["stats"]["max_abs_delta"] < 0.05
    assert raw["stats"]["max_abs_delta"] > 5 * aligned["stats"]["max_abs_delta"]
    assert aligned["alignment"][1]["shift_applied"] == pytest.approx(-2.0, abs=0.3)


def test_difference_spectrum_rejects_disjoint_ranges():
    lo = np.arange(5450.0, 5470.0, 0.5)
    hi = np.arange(5500.0, 5520.0, 0.5)
    with pytest.raises(ValueError, match="overlap"):
        difference_spectrum(lo, np.ones_like(lo), hi, np.ones_like(hi),
                                align=False)


def test_difference_spectrum_leaf_on_merged_files(scan_dir):
    from beamtimehero_cli.tool_catalog.tools_core import t_difference_spectrum

    _write_merged(scan_dir / "MERGE" / "a.dat", _GRID, _xanes(_GRID, 5483.0))
    _write_merged(scan_dir / "MERGE" / "b.dat", _GRID, _xanes(_GRID, 5485.0))

    text, images = t_difference_spectrum({
        "file_name_a": "MERGE/a.dat", "file_name_b": "MERGE/b.dat",
    })
    out = json.loads(text)
    assert "error" not in out, out
    assert out["aligned"] is True
    assert out["max_abs_delta"] < 0.05
    assert len(images) == 1 and len(images[0]) > 100

    text_raw, _ = t_difference_spectrum({
        "file_name_a": "MERGE/a.dat", "file_name_b": "MERGE/b.dat",
        "align": False,
    })
    assert json.loads(text_raw)["max_abs_delta"] > out["max_abs_delta"]


# ---------------------------------------------------------------------------
# Overlay accepts merged files
# ---------------------------------------------------------------------------

def test_plot_averaged_scans_overlays_merged_files(scan_dir):
    from beamtimehero_cli.tool_catalog.tools_core import t_plot_averaged_scans

    _write_merged(scan_dir / "MERGE" / "a.dat", _GRID, _xanes(_GRID, 5483.0))
    _write_merged(scan_dir / "MERGE" / "b.dat", _GRID, _xanes(_GRID, 5485.0))

    text, images = t_plot_averaged_scans({
        "file_names": ["MERGE/a.dat", "MERGE/b.dat"],
    })
    assert len(images) == 1 and len(images[0]) > 100
    assert "MERGE/a.dat" in text and "MERGE/b.dat" in text
