"""The tender tree (beamtimehero_cli/tender/), end to end.

Ported from the tender-tools staging repo (lx3g:git/tender-tools.git).
Two data sources:

  * the bundled Na2SO4 RIXS series of a Tender_Analysis checkout (real data;
    set TENDER_ANALYSIS_DATA to its data/ dir, else a sibling checkout is
    tried, else these tests skip) -- the one thing that pins a chat
    re-reduction to the tender-herfd JOB (Measurement.run);
  * a synthetic XES + elastic set: header-only .sif stand-ins plus a
    monkeypatched sif_parser.np_open, so every tender_analysis path above
    the decoder runs without data.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("tender_analysis", reason="needs the [tender] extra")

from beamtimehero_cli.tender import discovery, reduce  # noqa: E402
from beamtimehero_cli.tender.definitions import (  # noqa: E402
    LINEAGE, NAMES, TOOL_DEFINITIONS)
from beamtimehero_cli.tender.handlers import TENDER_HANDLERS as DISPATCH  # noqa: E402
from beamtimehero_cli.tender.settings import Reduction, Roi  # noqa: E402


# ---------------------------------------------------------------- fixtures



ROOT = Path(__file__).resolve().parents[1]
TA_DATA = Path(os.environ.get("TENDER_ANALYSIS_DATA",
                              ROOT.parent / "Tender_Analysis" / "data"))
NA2SO4 = "Na2SO4"
NA2SO4_M = "Na2SO4_pellet_20pcSucrose_Ka_RIXS_01"


@pytest.fixture
def na2so4_env(monkeypatch, tmp_path):
    if not glob.glob(str(TA_DATA / NA2SO4 / "*.sif")):
        pytest.skip("bundled Tender_Analysis/data/Na2SO4 not present")
    monkeypatch.setenv("TENDER_DATA_DIR", str(TA_DATA))
    monkeypatch.setenv("TENDER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("TENDER_PROCESSED_DIR", raising=False)
    return TA_DATA


# ------------------------------------------------------------- synthetic

SIF_MAGIC = b"Andor Technology Multi-Channel File"
H, W = 512, 2048
_FRAMES: dict[str, np.ndarray] = {}


def _write_sif(path: Path, frames: np.ndarray, mono: float, i0: float, exptime=7.0):
    comment = (f"\n65538 1 ... comment: mono = {mono:.4f} I0 = {i0:.0f} I1 = 50 "
               f"exptime = {exptime} vert = 42.0\n").encode()
    body = SIF_MAGIC + b"\n" + comment
    path.write_bytes(body + b"\0" * (8192 - len(body)))
    _FRAMES[str(path)] = frames.astype(float)


def _frames(rng, n, centre_px, photons, pedestal=900.0, line_width=15.0, diffuse=300):
    """n frames: pedestal + read noise + single-photon grains (~230 ADU over a
    2x2 cluster) along an emission line at `centre_px`, with the same
    gaussian row footprint in every column (as a real spectrometer images
    it; random rows make the library's curvature fit extrapolate wildly)."""
    out = np.empty((n, H, W))
    for k in range(n):
        f = pedestal + rng.normal(0, 3.0, (H, W))
        cols = np.concatenate([
            rng.normal(centre_px, line_width, photons),
            rng.uniform(0, W - 2, diffuse)]).round().astype(int).clip(0, W - 2)
        rws = np.clip(rng.normal(256, 45, cols.size).round().astype(int), 0, H - 2)
        for r, c in zip(rws, cols):
            f[r:r + 2, c:c + 2] += 230 / 4
        out[k] = f
    return out


@pytest.fixture
def synthetic_env(monkeypatch, tmp_path):
    """Beamtime dir with AgNO3 (XES, 3 scans + darks) and BN_elastic (4
    elastic energies + darks) whose line position follows E = m*px + b."""
    import sif_parser

    rng = np.random.default_rng(7)
    root = tmp_path / "beamtime"
    xes, el = root / "AgNO3", root / "BN"
    xes.mkdir(parents=True)
    el.mkdir()
    m_true, b_true = 0.0987, 3247.5
    e_inc = 3355.80
    px_line = (3349.0 - b_true) / m_true      # the emission line
    px_el = (e_inc - b_true) / m_true         # the sample's own elastic peak
    for s in (1, 2, 3):
        fr = _frames(rng, 3, px_line, 1500)
        for k in range(3):                     # a weak elastic peak in every frame
            cols = np.clip(rng.normal(px_el, 3, 25).round().astype(int), 0, W - 2)
            for r, c in zip(np.clip(rng.normal(256, 45, 25).round().astype(int), 0, H - 2), cols):
                fr[k, r:r + 2, c:c + 2] += 230 / 4
        _write_sif(xes / f"AgNO3_AgL3val_{e_inc:.2f}eV_{s:02d}.sif", fr, e_inc, 5000 + 10 * s)
        _write_sif(xes / f"AgNO3_AgL3val_{e_inc:.2f}eV_{s:02d}_dark.sif",
                   900.0 + rng.normal(0, 3.0, (2, H, W)), e_inc, 5000)
    for e in (3330.0, 3335.0, 3340.0, 3345.0):
        px = (e - b_true) / m_true
        _write_sif(el / f"elastic_BN_pellet_AgL3val_{e:.2f}eV_01.sif",
                   _frames(rng, 2, px, 1500, line_width=4.0), e, 4000)
        _write_sif(el / f"elastic_BN_pellet_AgL3val_{e:.2f}eV_01_dark.sif",
                   900.0 + rng.normal(0, 3.0, (2, H, W)), e, 4000)

    real = sif_parser.np_open

    def fake_np_open(path, *a, **k):
        p = str(path)
        if p in _FRAMES:
            return _FRAMES[p], {"ExposureTime": 7.0}
        return real(path, *a, **k)

    monkeypatch.setattr(sif_parser, "np_open", fake_np_open)
    monkeypatch.setenv("TENDER_DATA_DIR", str(root))
    monkeypatch.setenv("TENDER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("TENDER_PROCESSED_DIR", raising=False)
    return {"root": root, "m": m_true, "b": b_true, "e_inc": e_inc, "px_line": px_line}


# ------------------------------------------------------------- equivalence





def _job_rixs(m, dark):
    """What chemcatal/skills/tender.py::run does for these settings."""
    from tender_analysis import index_beamtime
    idx = index_beamtime(str(discovery.sample_dir(NA2SO4)))
    meas = next(x for x in idx if x.label() == m["label"])
    kw = {} if dark == "auto" else {"use_dark_as_background": False}
    return meas.run(central_pix=None, n=7, i0_corr=True, **kw)


def test_min_projection_is_compute_background(na2so4_env):
    from tender_analysis import SifFile, compute_background
    m = discovery.find_measurement(NA2SO4, NA2SO4_M)
    paths = m["_paths"][:12]
    ref = compute_background([SifFile(p) for p in paths])
    np.testing.assert_allclose(reduce.min_projection(paths), ref, rtol=0, atol=1e-9)


@pytest.mark.slow
@pytest.mark.parametrize("dark", ["auto", "none"])
def test_herfd_matches_the_job(na2so4_env, dark):
    m = discovery.find_measurement(NA2SO4, NA2SO4_M)
    job = _job_rixs(m, dark)
    rm, build = reduce.rixs_map(m, Reduction(dark=dark))
    assert build["cache"] == "miss"
    h = reduce.herfd_from_map(rm, Roi())
    assert h["central_pix"] == job.central_pix
    np.testing.assert_allclose(h["E"], job.E)
    np.testing.assert_allclose(h["HERFD"], job.HERFD, rtol=1e-9)
    np.testing.assert_allclose(h["TFY"], job.TFY, rtol=1e-9)
    # and the cache returns the same map
    rm2, b2 = reduce.rixs_map(m, Reduction(dark=dark))
    assert b2["cache"] == "hit"
    np.testing.assert_array_equal(rm2.map, rm.map)


def test_xes_matches_the_job(synthetic_env):
    from tender_analysis import index_beamtime
    for dark in ("auto", "none"):
        m = discovery.find_measurement("AgNO3", "AgNO3_L3val_XES_3355.8eV")
        ours = reduce.xes_spectrum(m, Reduction(dark=dark))["spectrum"]
        idx = index_beamtime(str(synthetic_env["root"] / "AgNO3"))
        meas = next(x for x in idx if x.label() == m["label"])
        job = meas.run(**({} if dark == "auto" else {"bcg": None})).spectrum()
        np.testing.assert_allclose(ours, job, rtol=1e-9, atol=1e-6)


@pytest.mark.xfail(strict=True, reason=(
    "tender_analysis c9ab6fd: CurvatureCorrection fits a quadratic through the few "
    "dispersive bins that carry signal and extrapolates it over all 2048 columns; with a "
    "narrow line the shift exceeds the detector and apply() raises. Library issue, see "
    "PLAN-bth-integration.md 'Library issues'."))
def test_library_curvature_narrow_line_hazard():
    from tender_analysis import CurvatureCorrection
    frame = _frames(np.random.default_rng(7), 1, 1030, 600, line_width=30.0, diffuse=0)[0] - 900
    frame[frame < 50] = 0
    CurvatureCorrection().fit_apply(frame)


# ------------------------------------------------------------------ tools





def call(name, **args):
    text, images = DISPATCH[name](args)
    return json.loads(text), images


# ------------------------------------------------------------ registries


def test_registries_agree():
    assert set(NAMES) == set(DISPATCH) == set(LINEAGE)
    assert all(not LINEAGE[n]["mutates"] for n in NAMES)
    for d in TOOL_DEFINITIONS:
        fn = d["function"]
        assert fn["parameters"]["type"] == "object"
        for req in fn["parameters"].get("required", []):
            assert req in fn["parameters"]["properties"]


def test_names_are_bare(na2so4_env):
    for bad in ("../etc", "/abs", "a/b", ""):
        out, _ = call("tender_inspect_measurement", sample=NA2SO4, measurement=bad)
        assert "error" in out
    out, _ = call("tender_list_measurements", sample="../..")
    assert "error" in out


# ----------------------------------------------------------------- real


def test_list_and_inspect_na2so4(na2so4_env):
    out, _ = call("tender_list_measurements")
    assert out["compounds"] == [{"sample": NA2SO4, "n_sif": 84}]
    out, _ = call("tender_list_measurements", sample=NA2SO4)
    [m] = out["measurements"]
    assert (m["label"], m["kind"], m["n_files"], m["n_dark"]) == (NA2SO4_M, "RIXS", 83, 1)
    out, imgs = call("tender_inspect_measurement", sample=NA2SO4, measurement=NA2SO4_M)
    # The story's chapter 3 finding: this series' "dark" was taken with beam on.
    assert out["dark_check"]["verdict"] == "lit"
    assert out["reproduce_with"]["job"]["dark"] == "none"
    assert out["i0"]["missing_or_zero_idx"] == []
    assert len(imgs) == 1


def test_preview_finds_the_photon_peak(na2so4_env):
    out, imgs = call("tender_preview_image", sample=NA2SO4, measurement=NA2SO4_M,
                     point=2481, dark="none")
    assert out["file"].endswith("2481.00.sif")
    assert 200 < out["grains"]["photon_peak_adu"] < 260      # S Ka, ~230 ADU
    assert out["spectrum"]["doublet"] is not None             # Ka2 / Ka1
    assert len(imgs) == 2


@pytest.mark.slow
def test_herfd_story_numbers(na2so4_env):
    pytest.importorskip("larch")  # the ranges need larch (or chemcat's xas_core)
    out, imgs = call("tender_herfd", sample=NA2SO4, measurement=NA2SO4_M, dark="none",
                     compare_centres="peaks")
    assert out["auto_centre"] == 1314
    assert "BETWEEN" in out["emission"]["verdict"]
    main = out["curves"][0]["normalisation"]
    assert main["edge_step"] == pytest.approx(18.4, abs=0.1)   # larch defaults: wrong
    assert main["norm_at_scan_end"] < 0.6
    best = next(r for r in out["post_edge_candidates"] if r["norm1"] == 25)
    assert best["white_line_height_edge_steps"] == pytest.approx(3.5, abs=0.05)
    # a ROI/normalisation change is a cache hit
    out2, _ = call("tender_herfd", sample=NA2SO4, measurement=NA2SO4_M, dark="none",
                   central_pix=1276, norm1=25, nnorm=1)
    assert out2["build"]["cache"] == "hit"
    assert out2["reproduce_with"]["job"]["central_pix"] == 1276
    assert out2["reproduce_with"]["not_yet_job_fields"]["normalisation"] == \
        {"norm1": 25.0, "nnorm": 1}
    assert len(imgs) == 2


def test_budget_refusal_falls_back_to_the_record(na2so4_env, monkeypatch, tmp_path):
    monkeypatch.setenv("TENDER_MAX_FRAMES", "10")
    out, _ = call("tender_herfd", sample=NA2SO4, measurement=NA2SO4_M)
    assert out["rereduced"] is False and "budget" in out
    assert out["reproduce_with"]["job"]["skill"] == "tender-herfd"


# ------------------------------------------------------ processed record


def _fake_job(proc, job, label, E, mu, **params):
    from tender_analysis import RIXSResult, write_xas_csv
    d = proc / job
    d.mkdir(parents=True)
    r = RIXSResult(E=E, HERFD=mu, TFY=mu * 10, central_pix=1314,
                   rixs_map=np.empty((0, E.size)), meta={"background": "x"})
    name = f"{label}_herfd.csv"
    write_xas_csv(r, d / name)
    (d / "manifest.json").write_text(json.dumps({
        "skill": "tender-herfd", "params": {"sample": NA2SO4, "measurement": label, **params},
        "inputs": [], "outputs": [name], "summary": "s", "origin": "first-pass"}))
    return f"{job}:{name}"


def test_results_compare_and_average(na2so4_env, monkeypatch, tmp_path):
    proc = tmp_path / "pipeline"
    monkeypatch.setenv("TENDER_PROCESSED_DIR", str(proc))
    E = np.linspace(2465, 2530, 140)
    edge = lambda e0: 1 / (1 + np.exp(-(E - e0) / 0.6)) + 2.5 * np.exp(-((E - e0 - 1.2) / 1.0) ** 2)
    proc.mkdir()
    a = _fake_job(proc, "j1", NA2SO4_M, E, edge(2479.8))
    b = _fake_job(proc, "j2", NA2SO4_M.replace("_01", "_02"), E, edge(2481.2), n=5)
    out, _ = call("tender_results", action="list")
    assert out["n"] == 2
    out, imgs = call("tender_results", action="compare", outputs=[a, b])
    assert "n" in out["settings_that_differ"] and "warning" in out
    out, imgs = call("tender_results", action="average", outputs=[a, b])
    assert out["e0_shift_applied_eV"][1] == pytest.approx(1.4, abs=0.2)
    assert "alignment_note" in out and len(imgs) == 1
    # herfd sees the record and says whether the settings match
    out, _ = call("tender_inspect_measurement", sample=NA2SO4, measurement=NA2SO4_M,
                  check_dark=False)
    assert out["processed"]["output"] == a


# ------------------------------------------------------------ synthetic


def test_xes_and_elastic_calibration(synthetic_env):
    env = synthetic_env
    out, _ = call("tender_list_measurements", sample="AgNO3")
    [m] = out["measurements"]
    assert m["kind"] == "XES" and m["n_files"] == 3 and m["n_dark"] == 3
    out, imgs = call("tender_xes", sample="AgNO3", measurement=m["label"])
    assert abs(out["main_peak"]["centroid"] - env["px_line"]) < 5
    assert out["axis"].startswith("dispersive pixel")
    out, _ = call("tender_xes", sample="AgNO3", measurement=m["label"], elastic_sample="BN")
    cal = out["calibration"]
    assert cal["m_eV_per_px"] == pytest.approx(env["m"], rel=0.01)
    assert abs(out["main_peak"]["centroid"] - 3349.0) < 0.5
    assert abs(out["elastic_check"]["offset_eV"]) < 0.6
    assert out["reproduce_with"]["not_yet_job_fields"]["calibration"]["m"] == cal["m_eV_per_px"]
    assert len(imgs) == 1


def test_inspect_real_dark_is_dark(synthetic_env):
    m = "AgNO3_L3val_XES_3355.8eV"
    out, _ = call("tender_inspect_measurement", sample="AgNO3", measurement=m)
    assert out["dark_check"]["verdict"] == "dark"
    assert out["reproduce_with"]["job"]["skill"] == "tender-xes"


# --------------------------------------------------- through bth's catalog


def test_the_leaves_dispatch_through_the_catalog(na2so4_env):
    from beamtimehero_cli.tool_catalog import execute_tool
    from beamtimehero_cli.tool_catalog.categorize import categorize
    for d in TOOL_DEFINITIONS:
        assert categorize(d) == ("tender",)
    text, images = execute_tool("tender", "tender_list_measurements", {"sample": NA2SO4})
    assert json.loads(text)["measurements"][0]["label"] == NA2SO4_M
    assert images == []


def test_the_refdoc_is_served():
    from beamtimehero_cli import refdocs
    assert refdocs.has_doc("tender-analysis")
    assert "lit" in refdocs.get_doc("tender-analysis")        # the dark pitfall


def test_a_zero_frame_budget_never_decodes(na2so4_env, monkeypatch):
    """TENDER_MAX_FRAMES=0 (a host running the tools inside a shared service):
    headers and the record still answer, nothing decodes a frame."""
    monkeypatch.setenv("TENDER_MAX_FRAMES", "0")
    out, _ = call("tender_inspect_measurement", sample=NA2SO4, measurement=NA2SO4_M)
    assert "skipped" in out["dark_check"] and out["i0"]["n"] == 83
    assert out["chat_can_rereduce"] is False
    out, _ = call("tender_herfd", sample=NA2SO4, measurement=NA2SO4_M)
    assert out["rereduced"] is False
    out, _ = call("tender_preview_image", sample=NA2SO4, measurement=NA2SO4_M)
    assert "budget" in out
