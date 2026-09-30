"""Load one cherfd sweep from disk into plain arrays, safely.

Two hazards specific to these pickles:

* ``scan_results_*.pkl`` (no ``_dataframe``) is a pickled
  ``cherfd.ScanResults``. Unpickling it normally imports ``cherfd``, which
  imports pyepics and writes log files. ``load_metadata`` uses an unpickler
  that substitutes inert stubs for every ``cherfd``/``__main__`` class, so
  the parameters and summary can be read without that import.
* The ``_dataframe.pkl`` integer columns are ``object`` dtype; everything is
  coerced to float here so the science layer only ever sees numeric arrays.
  Older files call the gap column ``gap_mm``; it is renamed ``gap``.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from beamtimehero_cli.spec_data.cherfd.files import SweepFile, find_vortex_file

#: Columns that come from the Xspress3 rather than the FPGA frame.
DETECTOR_COLUMNS = ("vortex", "roi1", "roi_double", "vortex_intensity")


class _Stub:
    def __init__(self, *a, **k):
        pass

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update(state)
        else:
            self.__dict__["_state"] = state


class _SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module == "cherfd" or module.startswith(("cherfd.", "__main__", "cscan_box", "utils")):
            return type(name, (_Stub,), {})
        return super().find_class(module, name)


def _plain(obj):
    if isinstance(obj, _Stub):
        return {k: _plain(v) for k, v in vars(obj).items() if k != "data_df"}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if hasattr(obj, "value") and type(obj).__module__.startswith("enum"):
        return obj.value
    if isinstance(obj, (np.generic,)):
        return obj.item()
    return obj


def results_object_path(sf: SweepFile) -> Path:
    p = Path(sf.path)
    return p.with_name(p.name.replace("_dataframe.pkl", ".pkl"))


def load_metadata(sf: SweepFile) -> dict:
    """ScanParameters + summary from the sibling ScanResults pickle, if present."""
    path = results_object_path(sf)
    if not path.exists():
        return {"available": False, "reason": f"{path.name} not found"}
    try:
        with open(path, "rb") as fh:
            obj = _SafeUnpickler(fh).load()
    except Exception as e:  # noqa: BLE001 -- a bad sidecar must not break analysis
        return {"available": False, "reason": f"could not read {path.name}: {e}"}
    d = _plain(obj)
    if not isinstance(d, dict):
        return {"available": False, "reason": "unexpected ScanResults layout"}
    params = d.get("parameters") or {}
    return {
        "available": True,
        "file": path.name,
        "command": params.get("command"),
        "parameters": params,
        "summary": d.get("summary"),
        "completion_time": d.get("completion_time"),
    }


def load_frame_table(sf: SweepFile) -> pd.DataFrame:
    df = pd.read_pickle(sf.path)
    if not isinstance(df, pd.DataFrame):
        raise ValueError(f"{Path(sf.path).name} does not hold a DataFrame")
    df = df.rename(columns={"gap_mm": "gap"})
    return df.apply(pd.to_numeric, errors="coerce").astype(float)


def load_sweep_arrays(sf: SweepFile) -> tuple[dict, dict, dict]:
    """(fpga_columns, detector_columns, provenance) as float arrays.

    Detector columns come from the separate ``vortex_*.pkl`` when present
    (it also carries roi1/roi_double), else from the merged table's
    ``vortex`` column, which is the same frames in the same order.
    """
    df = load_frame_table(sf)
    fpga = {c: df[c].to_numpy() for c in df.columns if c not in DETECTOR_COLUMNS}
    det: dict[str, np.ndarray] = {}
    prov = {"file": Path(sf.path).name, "detector_source": None}
    vpath = find_vortex_file(sf)
    if vpath is not None:
        try:
            v = pd.read_pickle(vpath).apply(pd.to_numeric, errors="coerce").astype(float)
            det = {c: v[c].to_numpy() for c in v.columns}
            prov["detector_source"] = vpath.name
        except Exception as e:  # noqa: BLE001
            prov["detector_error"] = f"{vpath.name}: {e}"
    if not det:
        det = {c: df[c].to_numpy() for c in df.columns if c in DETECTOR_COLUMNS}
        if det:
            prov["detector_source"] = "merged table (row order == detector frame order)"
    return fpga, det, prov
