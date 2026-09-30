"""The processed record: what the first pass and people's jobs produced.

Layout (chemcatal worker/layout.py, portal/processed.py): TENDER_PROCESSED_DIR
is the beamtime's `pipeline/` tree, one directory per job, and a job counts
only if it left a manifest.json -- the manifest IS the record (skill,
params, inputs, outputs, summary, library, and origin='first-pass' for the
automatic pass). The CSVs themselves carry the reduction's provenance in
their `#` header (thresholds, background, central_pix, normalisation
method, pre-edge e0/edge_step), written by tender_analysis.export.

Read-only. Nothing here writes into the processed tree.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from .config import ToolError, processed_dir, safe_name

TENDER_SKILLS = ("tender-herfd", "tender-xes")


def _require_dir() -> Path:
    d = processed_dir()
    if d is None:
        raise ToolError("no processed tree in scope (TENDER_PROCESSED_DIR unset): first-pass "
                        "and job results cannot be read in this session")
    return d


def read_csv(path: Path) -> tuple[dict, dict]:
    """(header {key: value}, columns {name: array}) of a chemcat-shaped CSV."""
    header, lines = {}, []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#"):
                body = line[1:].strip()
                if ":" in body:
                    k, v = body.split(":", 1)
                    header[k.strip()] = v.strip()
            elif line.strip():
                lines.append(line.strip())
    if not lines:
        return header, {}
    names = [c.strip() for c in lines[0].split(",")]
    rows = []
    for ln in lines[1:]:
        try:
            rows.append([float(x) if x.strip().lower() != "nan" else np.nan
                         for x in ln.split(",")])
        except ValueError:
            continue
    arr = np.asarray(rows, dtype=float) if rows else np.empty((0, len(names)))
    return header, {n: arr[:, i] for i, n in enumerate(names) if arr.shape[1] > i}


def _settings_from(manifest: dict, header: dict) -> dict:
    p = manifest.get("params") or {}
    s = {"threshold": p.get("threshold") or "library default [60, 100, 170, 2000]",
         "dark": p.get("dark", "auto"), "bcg_adjust": p.get("bcg_adjust", True)}
    if manifest.get("skill") == "tender-herfd":
        s.update(n=p.get("n", 7), i0_corr=p.get("i0_corr", True),
                 central_pix_requested=p.get("central_pix"),
                 central_pix_used=header.get("central_pix"),
                 normalisation=header.get("normalization"),
                 pre_edge=header.get("pre_edge"))
    s["background_used"] = header.get("background")
    return s


def outputs(sample: str | None = None, measurement: str | None = None) -> list[dict]:
    d = _require_dir()
    rows = []
    for job in sorted(d.iterdir()):
        mf = job / "manifest.json"
        if not mf.is_file():
            continue
        try:
            m = json.loads(mf.read_text())
        except (OSError, ValueError):
            continue
        if m.get("skill") not in TENDER_SKILLS:
            continue
        p = m.get("params") or {}
        if sample is not None and (p.get("sample") or "") != sample:
            continue
        if measurement is not None and p.get("measurement") != measurement:
            continue
        for name in m.get("outputs") or []:
            f = job / name
            if not f.is_file() or "/" in name or ".." in name:
                continue
            header, _ = read_csv(f)
            rows.append({"output": f"{job.name}:{name}", "job": job.name,
                         "skill": m.get("skill"),
                         "origin": m.get("origin", "user job"),
                         "sample": p.get("sample", ""), "measurement": p.get("measurement"),
                         "summary": m.get("summary"), "library": m.get("library"),
                         "mtime": int(os.path.getmtime(mf)),
                         "settings": _settings_from(m, header)})
    rows.sort(key=lambda r: (r["sample"], r["measurement"] or "", -r["mtime"]))
    return rows


def load(output: str) -> tuple[dict, dict, dict]:
    """(row, header, columns) for an output id '<job>:<name>'.

    ':' rather than '/': an id is not a path, and must not look like one to a
    host that containment-checks every '/'-bearing argument (chemcatal-bth)."""
    try:
        job, name = output.split(":", 1)
    except ValueError:
        raise ToolError("an output id is '<job>:<file>' as listed by tender_results") from None
    job, name = safe_name(job, "job"), safe_name(name, "output file")
    f = _require_dir() / job / name
    if not f.is_file():
        raise ToolError(f"no processed output {output!r}")
    row = next((r for r in outputs() if r["output"] == f"{job}:{name}"), None)
    if row is None:
        raise ToolError(f"{output!r} is not a Tender job output")
    header, cols = read_csv(f)
    return row, header, cols


def latest_for(sample: str, measurement: str) -> dict | None:
    rows = outputs(sample, measurement)
    return max(rows, key=lambda r: r["mtime"]) if rows else None


COMPARABLE_KEYS = ("threshold", "dark", "bcg_adjust", "n", "i0_corr", "central_pix_used",
                   "normalisation")


def mismatches(rows: list[dict]) -> dict:
    """Settings that differ between outputs someone wants to compare."""
    out = {}
    for k in COMPARABLE_KEYS:
        vals = {json.dumps(r["settings"].get(k), default=str) for r in rows}
        if len(vals) > 1:
            out[k] = {r["output"]: r["settings"].get(k) for r in rows}
    return out
