"""Write merged CHERFD spectra where the beamtimehero XAS tools can read them.

This is the hand-off from continuous-scan reduction to the XAS tools:

* ``twocol`` -- exactly two columns (energy eV, intensity) with ``#``
  provenance comments. beamtimehero's ``spec_data/twocol_ascii.py`` accepts
  it wherever a ``file_name`` is taken (it demands exactly two columns, so
  the standard error goes to a sibling ``.sem.dat``).
* ``spec`` -- a SPEC-format file: one ``#S`` block per sweep (binned) plus a
  final ``#S`` holding the merge, columns ``Energy signal sem frames``.
  Readable by silx and bth's ``spec-file`` tools.

Writes only under ``CHERFD_EXPORT_DIR``; nothing is written into the
cherfd_claude data tree.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np


def _clean_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name).strip("._") or "cherfd"


def write(binned: dict, *, fmt: str, name: str, provenance: dict, out_dir: Path) -> dict:
    if fmt not in ("twocol", "spec"):
        raise ValueError("format must be 'twocol' or 'spec'")
    out_dir.mkdir(parents=True, exist_ok=True)
    name = _clean_name(name)
    e, y, s, n = binned["energy"], binned["merged"], binned["sem"], binned["frames_per_bin"]
    ok = np.isfinite(y) & (n > 0)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    header = [
        f"beamtimehero cherfd export {stamp}",
        f"provenance {json.dumps(provenance, default=str)}",
        f"bin_ev {binned['bin_ev']}  error_model {binned['error_model']}",
    ]
    if fmt == "twocol":
        path = out_dir / f"{name}.dat"
        sem_path = out_dir / f"{name}.sem.dat"
        with open(path, "w") as fh:
            for h in header:
                fh.write(f"# {h}\n")
            fh.write("# energy_eV  intensity\n")
            for ei, yi in zip(e[ok], y[ok]):
                fh.write(f"{ei:.4f}  {yi:.8g}\n")
        with open(sem_path, "w") as fh:
            fh.write("# energy_eV  sem  frames\n")
            for ei, si, ni in zip(e[ok], s[ok], n[ok]):
                fh.write(f"{ei:.4f}  {si:.6g}  {int(ni)}\n")
        return {"format": fmt, "path": str(path), "sem_path": str(sem_path), "points": int(ok.sum())}

    path = out_dir / f"{name}.spec"
    with open(path, "w") as fh:
        fh.write(f"#F {path.name}\n#E {int(time.time())}\n#D {time.ctime()}\n")
        for h in header:
            fh.write(f"#C {h}\n")
        fh.write("\n")
        blocks = [(str(p["label"]), p["values"], None, p["frames"]) for p in binned["per_sweep"]]
        blocks.append(("merged", y, s, n))
        sig = binned["signal"] + (f"_over_{binned['normalize_by']}" if binned["normalize_by"] else "")
        for i, (label, vals, sem, frames) in enumerate(blocks, start=1):
            m = np.isfinite(vals) & (frames > 0)
            fh.write(f"#S {i}  cherfd_{label}\n#D {time.ctime()}\n#N 4\n")
            fh.write(f"#L Energy  {sig}  sem  frames\n")
            for j in np.flatnonzero(m):
                sv = sem[j] if sem is not None and np.isfinite(sem[j]) else 0.0
                fh.write(f"{e[j]:.4f} {vals[j]:.8g} {sv:.6g} {int(frames[j])}\n")
            fh.write("\n")
    return {"format": fmt, "path": str(path), "scans": len(blocks), "merged_scan_number": len(blocks),
            "points": int(ok.sum())}
