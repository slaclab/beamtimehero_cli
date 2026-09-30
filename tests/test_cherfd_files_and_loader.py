"""Filename parsing, run grouping (root reuse), stub unpickling."""
from __future__ import annotations

from tests._cherfd_fixtures import *  # noqa: F401,F403 -- isolation fixtures
from tests._cherfd_fixtures import REAL_RUN, run_tool as run  # noqa: F401

import pickle
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from beamtimehero_cli.spec_data.cherfd import files, loader


def _touch_sweep(d: Path, root: str, n: int, direction: str, ts: str, with_vortex=True):
    stem = f"{root + '_' if root else ''}{n}_{direction}_{ts}"
    df = pd.DataFrame({"trig_1": [10000, 30000, 20000], "absev": [9000.0, 9000.2, 9000.1],
                       "gap_mm": [8.1, np.nan, 8.1], "vortex": [1.0, 3.0, 2.0]}).astype(object)
    df.to_pickle(d / f"scan_results_{stem}_dataframe.pkl")
    if with_vortex:
        pd.DataFrame({"roi1": [1.0, 2.0, 3.0], "roi_double": [0, 0, 0], "vortex": [1.0, 2.0, 3.0]}).to_pickle(
            d / f"vortex_{stem[:-2]}{int(ts[-2:]) + 1:02d}.pkl")


@pytest.mark.parametrize("name,root,n,direction", [
    ("scan_results_spot1_2_1_fwd_2025-10-23_121304_dataframe.pkl", "spot1_2", 1, "fwd"),
    ("scan_results_3_rev_2025-10-23_121304_dataframe.pkl", "", 3, "rev"),
    ("scan_results_a_b_c_12_fwd_2025-10-23_121304_dataframe.pkl", "a_b_c", 12, "fwd"),
])
def test_parse(tmp_path, name, root, n, direction):
    p = tmp_path / "d" / name
    p.parent.mkdir()
    p.touch()
    sf = files.parse_sweep_filename(p, tmp_path)
    assert (sf.root, sf.sweep, sf.direction, sf.file_dir) == (root, n, direction, "d")


def test_not_a_sweep(tmp_path):
    assert files.parse_sweep_filename(tmp_path / "scan_results_x_1_fwd_2025-10-23_121304.pkl", tmp_path) is None


def test_runs_split_on_root_reuse(tmp_path):
    d = tmp_path / "beam"
    d.mkdir()
    for i, ts in enumerate(["2025-10-25_112814", "2025-10-25_112844", "2025-10-25_112915", "2025-10-25_112946"]):
        _touch_sweep(d, "spot1", i // 2 + 1, ["fwd", "rev"][i % 2], ts)
    for i, ts in enumerate(["2025-10-25_125330", "2025-10-25_125401"]):
        _touch_sweep(d, "spot1", 1, ["fwd", "rev"][i], ts)
    runs = files.group_runs(files.find_sweeps(tmp_path))
    assert [r["n_files"] for r in runs] == [4, 2]
    assert runs[0]["seconds_per_profile"] == pytest.approx(30.67)
    latest = files.resolve_run(tmp_path, file_root="spot1")
    assert latest["run_id"] == "beam/spot1@2025-10-25_125330"
    assert files.resolve_run(tmp_path, run_id=runs[0]["run_id"])["n_files"] == 4


def test_file_dir_escape_refused(tmp_path):
    with pytest.raises(ValueError):
        files.safe_subdir(tmp_path, "../..")


def test_vortex_pairing_and_numeric_coercion(tmp_path):
    d = tmp_path / "beam"
    d.mkdir()
    _touch_sweep(d, "r", 1, "fwd", "2025-10-25_112814")
    sf = files.find_sweeps(tmp_path)[0]
    fpga, det, prov = loader.load_sweep_arrays(sf)
    assert prov["detector_source"].startswith("vortex_r_1_fwd_")
    assert "gap" in fpga and "gap_mm" not in fpga
    assert fpga["trig_1"].dtype == float and "roi1" in det


def test_stub_unpickler_never_imports_cherfd(tmp_path):
    """Build a pickle referencing cherfd.ScanResults, then read it with no cherfd module."""
    mod = types.ModuleType("cherfd")

    class ScanParameters:
        pass

    class ScanResults:
        pass

    for cls in (ScanParameters, ScanResults):
        cls.__module__, cls.__qualname__ = "cherfd", cls.__name__
    mod.ScanParameters, mod.ScanResults = ScanParameters, ScanResults
    sys.modules["cherfd"] = mod
    try:
        p = ScanParameters()
        p.command, p.freq = "cherfd 8968 9100 .4 100", 100.0
        r = ScanResults()
        r.parameters, r.summary, r.data_df, r.completion_time = p, {"vortex_mode": "TS"}, pd.DataFrame(), 1.0
        d = tmp_path / "beam"
        d.mkdir()
        stem = "r_1_fwd_2025-10-25_112814"
        with open(d / f"scan_results_{stem}.pkl", "wb") as fh:
            pickle.dump(r, fh)
        pd.DataFrame({"trig_1": [10000], "absev": [9000.0]}).to_pickle(d / f"scan_results_{stem}_dataframe.pkl")
    finally:
        del sys.modules["cherfd"]
    sf = files.find_sweeps(tmp_path)[0]
    meta = loader.load_metadata(sf)
    assert "cherfd" not in sys.modules
    assert meta["available"] and meta["command"] == "cherfd 8968 9100 .4 100"
    assert meta["summary"] == {"vortex_mode": "TS"}


def test_relative_data_root(tmp_path, monkeypatch):
    """safe_subdir resolves paths; a relative root must still work (relative_to used to raise)."""
    d = tmp_path / "beam"
    d.mkdir()
    _touch_sweep(d, "r", 1, "fwd", "2025-10-25_112814")
    monkeypatch.chdir(tmp_path)
    found = files.find_sweeps(Path("."), "beam")
    assert len(found) == 1 and found[0].file_dir == "beam"
