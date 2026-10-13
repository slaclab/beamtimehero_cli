"""Sample provenance records and end-of-beamtime results bundles.

Two pieces an app needs at the end of a beamtime (and along the way):

* ``record_provenance`` appends one JSON line per event to
  ``<data dir>/provenance/<experiment>.jsonl``: who/what/when for a sample
  (mounted, aligned, measured under which conditions, exported). Records
  are append-only; ``read_provenance`` returns them.
* ``export_bundle`` copies the named data files into a new directory under
  the export root, writes ``manifest.json`` (sha256, size, source path and
  modification time per file, plus the caller's metadata) and, optionally,
  a zip beside it. Sources must lie under an allowed data root — the scan
  directory, the CHERFD export directory, this package's data directory, or
  ``BTH_EXPORT_SOURCE_ROOTS`` — so a bundle cannot sweep up arbitrary files.

The export root is ``BTH_EXPORT_DIR``, else ``<data dir>/exports``. Nothing
is deleted or overwritten: every bundle gets a fresh timestamped directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
MAX_FILES = 2000


def _data_dir() -> Path:
    from beamtimehero_cli import config
    return Path(config.DATA_DIR)


def _safe(name: str, what: str) -> str:
    clean = _NAME.sub("_", (name or "").strip()).strip("._")
    if not clean:
        raise ValueError(f"{what} is empty")
    return clean[:120]


def export_root() -> Path:
    v = os.environ.get("BTH_EXPORT_DIR")
    return Path(v).expanduser() if v else _data_dir() / "exports"


def source_roots() -> list[Path]:
    from beamtimehero_cli import config
    out = [Path(config.BL_SCAN_DIR), _data_dir()]
    try:
        from beamtimehero_cli.cherfd_control import settings as cherfd
        out.append(cherfd.export_dir())
        d = cherfd.data_dir()
        if d is not None:
            out.append(d)
    except Exception:  # noqa: BLE001
        pass
    for item in (os.environ.get("BTH_EXPORT_SOURCE_ROOTS") or "").split(","):
        if item.strip():
            out.append(Path(item.strip()).expanduser())
    return [p.resolve() for p in out]


def _check_source(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        from beamtimehero_cli import config
        p = Path(config.BL_SCAN_DIR) / p
    p = p.resolve()
    if not any(p.is_relative_to(r) for r in source_roots()):
        raise ValueError(f"{path!r} is not under an allowed data root "
                         f"({', '.join(str(r) for r in source_roots())})")
    if not p.is_file():
        raise ValueError(f"{path!r} is not a file")
    return p


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_bundle(files: Iterable[str], name: str, metadata: Optional[dict] = None,
                  make_zip: bool = True) -> dict[str, Any]:
    """Copy `files` into <export root>/<name>_<timestamp>/ with a manifest."""
    srcs = [_check_source(f) for f in files]
    if not srcs:
        raise ValueError("no files given")
    if len(srcs) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} files per bundle")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = export_root() / f"{_safe(name, 'name')}_{stamp}"
    dest.mkdir(parents=True, exist_ok=False)
    entries, used = [], set()
    for src in srcs:
        target = src.name
        n = 1
        while target in used:                       # same basename from two dirs
            target = f"{src.stem}_{n}{src.suffix}"
            n += 1
        used.add(target)
        shutil.copy2(src, dest / target)
        entries.append({
            "file": target,
            "source": str(src),
            "size_bytes": src.stat().st_size,
            "sha256": _sha256(dest / target),
            "modified": datetime.fromtimestamp(src.stat().st_mtime).isoformat(timespec="seconds"),
        })
    manifest = {
        "bundle": dest.name,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "metadata": metadata or {},
        "files": entries,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    out = {"ok": True, "path": str(dest), "n_files": len(entries),
           "total_bytes": sum(e["size_bytes"] for e in entries), "manifest": str(dest / "manifest.json")}
    if make_zip:
        zpath = dest.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(dest.iterdir()):
                z.write(p, f"{dest.name}/{p.name}")
        out["zip"] = str(zpath)
    return out


def _provenance_file(experiment: str) -> Path:
    return _data_dir() / "provenance" / f"{_safe(experiment, 'experiment')}.jsonl"


def record_provenance(experiment: str, event: str, sample: Optional[str] = None,
                      details: Optional[dict] = None, agent: str = "llm") -> dict[str, Any]:
    """Append one provenance event."""
    rec = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "experiment": experiment,
        "sample": sample,
        "event": _safe(event, "event"),
        "agent": agent,
        "details": details or {},
    }
    path = _provenance_file(experiment)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return {"ok": True, "record": rec, "path": str(path)}


def read_provenance(experiment: str, sample: Optional[str] = None) -> dict[str, Any]:
    path = _provenance_file(experiment)
    if not path.exists():
        return {"ok": True, "records": [], "path": str(path)}
    recs = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if sample is None or r.get("sample") == sample:
            recs.append(r)
    return {"ok": True, "records": recs, "path": str(path)}
