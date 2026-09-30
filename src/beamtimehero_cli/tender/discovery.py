"""What is on disk: compounds, measurements, file headers. Names first.

The SSRL share punishes per-file reads (a validating header pass over 16893
files did not finish in 15 minutes), so this module is layered by cost:

  list_compounds      one scandir of the beamtime + one per compound
  scan                one listing of ONE compound, grouped from filenames
                      (tender_analysis.scan_directory(validate=False), the
                      same call the Processing tab and the first pass make)
  headers             8 kB per file of ONE measurement (SifFile.metadata
                      reads the comment block only, never the frames)

No function here decodes a frame.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from .config import ToolError, data_dir, estimate_frames, safe_name, sample_dir


def list_compounds() -> dict:
    root = data_dir()
    compounds, loose = [], 0
    with os.scandir(root) as it:
        entries = sorted(it, key=lambda e: e.name)
    for e in entries:
        if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
            try:
                with os.scandir(e.path) as sub:
                    n = sum(1 for s in sub if s.name.lower().endswith(".sif"))
            except OSError:
                continue
            if n:
                compounds.append({"sample": e.name, "n_sif": n})
        elif e.name.lower().endswith(".sif"):
            loose += 1
    out = {"beamtime_dir": root.name, "compounds": compounds,
           "n_compounds": len(compounds),
           "n_sif_total": sum(c["n_sif"] for c in compounds) + loose}
    if loose:
        out["loose_sif_in_beamtime_dir"] = loose
        out["note"] = ("The beamtime directory holds .sif files directly; pass sample='' "
                       "to scan them.")
    return out


def scan(sample: str | None) -> dict:
    from tender_analysis import scan_directory

    d = sample_dir(sample)
    rep = scan_directory(str(d), validate=False).as_dict()
    rep.pop("directory", None)
    for r in rep["rejected"]:
        r.pop("path", None)
    rep["sample"] = sample or ""
    return rep


def find_measurement(sample: str | None, label: str) -> dict:
    """The scan-report entry for one measurement, with absolute paths added
    under `_paths` / `_dark_paths` (never returned to the agent)."""
    label = safe_name(label, "measurement")
    rep = scan(sample)
    m = next((m for m in rep["measurements"] if m["label"] == label), None)
    if m is None:
        have = ", ".join(x["label"] for x in rep["measurements"][:12]) or "(none)"
        raise ToolError(f"no measurement {label!r} in {sample or 'this directory'}; have: {have}")
    d = sample_dir(sample)
    m = dict(m)
    m["_paths"] = [str(d / f) for f in m["files"]]
    m["_dark_paths"] = [str(d / f) for f in m["dark_files"]]
    return m


def file_path(sample: str | None, name: str) -> Path:
    name = safe_name(name, "file")
    if not name.lower().endswith(".sif"):
        raise ToolError("file must be a .sif basename")
    p = sample_dir(sample) / name
    if not p.is_file():
        raise ToolError(f"no file {name!r} in {sample or 'this directory'}")
    return p


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def header(path: str | Path) -> dict:
    """Header-only facts for one file (8 kB read, no frame decode)."""
    from tender_analysis import SifFile
    from tender_analysis.scan import SIF_MAGIC

    p = str(path)
    s = SifFile(p)
    try:
        head_ok = s.comment.encode("latin-1", errors="replace").startswith(SIF_MAGIC)
        md = s.metadata
    except OSError as e:
        return {"file": os.path.basename(p), "error": f"unreadable: {e}"}
    return {"file": os.path.basename(p), "andor_header": head_ok,
            "mono_eV": _num(md.get("mono")), "I0": _num(md.get("I0")),
            "I1": _num(md.get("I1")), "exptime_s": _num(md.get("exptime")),
            "frames_est": estimate_frames(p)}


def energy_from_name(path: str) -> float | None:
    from tender_analysis import parse_sif_name
    rec = parse_sif_name(path)
    return rec.energy if rec is not None else None
