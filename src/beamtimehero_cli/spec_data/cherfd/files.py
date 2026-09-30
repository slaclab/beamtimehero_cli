"""Find cherfd sweep files on disk and group them into acquisition runs.

Layout written by cherfd_claude (``CherfdController.generate_filename`` /
``save_results``), per sweep, under ``<data_root>/<file_dir>/``::

    scan_data_<root>_<N>_<fwd|rev>_<YYYY-MM-DD_HHMMSS>.pkl              raw FPGA frames
    scan_results_<root>_<N>_<fwd|rev>_<ts>.pkl                          ScanResults object
    scan_results_<root>_<N>_<fwd|rev>_<ts>_dataframe.pkl                merged frames  <- analysed
    vortex_<root>_<N>_<fwd|rev>_<ts+~1s>.pkl                            Xspress3 ROIs

Stopped scans land in ``<data_root>/partial/<file_dir>/partial_scan_results_...``.

A **run** is one ``start_acquisition``: sweeps 1..N of one file root. The
root alone does not identify it -- operators reuse roots (2025-10_Sokaras
has ``..._spot1`` acquired at 11:28 and again at 12:53) -- so a new run
starts whenever the sweep number fails to advance or the clock jumps by
more than ``policy.RUN_GAP_S``. Run ids are ``<file_dir>/<root>@<first ts>``.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from beamtimehero_cli.science.cherfd import policy

_TS = r"\d{4}-\d{2}-\d{2}_\d{6}"
_DF_RE = re.compile(
    rf"^(?P<partial>partial_)?scan_results_(?:(?P<root>.+?)_)?(?P<n>\d+)_(?P<dir>fwd|rev)_"
    rf"(?P<ts>{_TS})_dataframe\.pkl$"
)
_VORTEX_RE = re.compile(rf"^vortex_(?:(?P<root>.+?)_)?(?P<n>\d+)_(?P<dir>fwd|rev)_(?P<ts>{_TS})\.pkl$")


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d_%H%M%S")


@dataclass
class SweepFile:
    path: str
    file_dir: str
    root: str
    sweep: int
    direction: str
    timestamp: str
    partial: bool = False

    @property
    def time(self) -> datetime:
        return _parse_ts(self.timestamp)

    @property
    def stem(self) -> str:
        """``<root>_<N>_<dir>_<ts>`` -- the part shared with sibling files."""
        head = f"{self.root}_" if self.root else ""
        return f"{head}{self.sweep}_{self.direction}_{self.timestamp}"

    @property
    def label(self) -> str:
        return f"{self.sweep}{self.direction[0]}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label
        return d


def parse_sweep_filename(path: Path, data_root: Path) -> SweepFile | None:
    m = _DF_RE.match(path.name)
    if not m:
        return None
    rel = path.parent.relative_to(data_root)
    parts = rel.parts
    partial = bool(m.group("partial"))
    if parts and parts[0] == "partial":
        parts = parts[1:]
        partial = True
    return SweepFile(
        path=str(path), file_dir="/".join(parts), root=m.group("root") or "",
        sweep=int(m.group("n")), direction=m.group("dir"), timestamp=m.group("ts"),
        partial=partial,
    )


def require_root(data_root: Path | None) -> Path:
    if data_root is None:
        raise ValueError(
            "no cherfd data directory configured: set CHERFD_DATA_DIR (or CHERFD_PROJECT_DIR)"
        )
    if not data_root.is_dir():
        raise ValueError(f"cherfd data directory does not exist: {data_root}")
    return data_root


def safe_subdir(data_root: Path, file_dir: str | None) -> Path:
    """Resolve ``file_dir`` under the data root, refusing escapes."""
    base = data_root.resolve()
    target = (base / (file_dir or "")).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"file_dir {file_dir!r} is outside the data directory")
    return target


def list_data_dirs(data_root: Path) -> list[dict]:
    out = []
    for d in sorted(p for p in data_root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        n = sum(1 for _ in d.glob("scan_results_*_dataframe.pkl"))
        if n == 0 and d.name != "partial":
            continue
        newest = max((f.stat().st_mtime for f in d.glob("scan_results_*_dataframe.pkl")), default=None)
        out.append({"file_dir": d.name, "sweep_files": n,
                    "newest": datetime.fromtimestamp(newest).isoformat(timespec="seconds") if newest else None})
    return out


def find_sweeps(data_root: Path, file_dir: str | None = None, root: str | None = None,
                include_partial: bool = False) -> list[SweepFile]:
    base = safe_subdir(data_root, file_dir)
    found: list[SweepFile] = []
    paths = base.rglob("*scan_results_*_dataframe.pkl") if file_dir is None else base.glob("*scan_results_*_dataframe.pkl")
    for p in paths:
        sf = parse_sweep_filename(p, data_root)
        if sf is None:
            continue
        if sf.partial and not include_partial:
            continue
        if root is not None and sf.root != root:
            continue
        found.append(sf)
    if include_partial and file_dir is not None:
        pdir = data_root / "partial" / file_dir
        if pdir.is_dir():
            for p in pdir.glob("partial_scan_results_*_dataframe.pkl"):
                sf = parse_sweep_filename(p, data_root)
                if sf and (root is None or sf.root == root):
                    found.append(sf)
    found.sort(key=lambda s: (s.file_dir, s.root, s.timestamp, s.sweep, s.direction))
    return found


def group_runs(sweeps: list[SweepFile]) -> list[dict]:
    """Split sweeps into acquisition runs (see module docstring)."""
    by_key: dict[tuple[str, str], list[SweepFile]] = {}
    for s in sweeps:
        by_key.setdefault((s.file_dir, s.root), []).append(s)
    runs: list[dict] = []
    for (fdir, root), items in by_key.items():
        items.sort(key=lambda s: s.timestamp)
        current: list[SweepFile] = []
        for s in items:
            if current:
                prev = current[-1]
                gap = (s.time - prev.time).total_seconds()
                seen = {(c.sweep, c.direction) for c in current}
                if s.sweep < prev.sweep or (s.sweep, s.direction) in seen or gap > policy.RUN_GAP_S:
                    runs.append(_run_record(fdir, root, current))
                    current = []
            current.append(s)
        if current:
            runs.append(_run_record(fdir, root, current))
    runs.sort(key=lambda r: r["started"])
    return runs


def _run_record(file_dir: str, root: str, items: list[SweepFile]) -> dict:
    t0, t1 = items[0].time, items[-1].time
    cadence = None
    if len(items) > 1:
        cadence = round((t1 - t0).total_seconds() / (len(items) - 1), 2)
    return {
        "run_id": f"{file_dir}/{root}@{items[0].timestamp}",
        "file_dir": file_dir,
        "file_root": root,
        "started": t0.isoformat(timespec="seconds"),
        "last_sweep_at": t1.isoformat(timespec="seconds"),
        "n_files": len(items),
        "n_fwd": sum(1 for s in items if s.direction == "fwd"),
        "n_rev": sum(1 for s in items if s.direction == "rev"),
        "max_sweep": max(s.sweep for s in items),
        "partial": any(s.partial for s in items),
        "seconds_per_profile": cadence,
        "sweeps": items,
    }


def resolve_run(data_root: Path, *, run_id: str | None = None, file_dir: str | None = None,
                file_root: str | None = None, include_partial: bool = False) -> dict:
    """One run by id, or the latest run matching file_dir/file_root, or the latest overall."""
    if run_id:
        head, _, ts = run_id.rpartition("@")
        fdir, _, root = head.rpartition("/")
        runs = group_runs(find_sweeps(data_root, fdir or None, root, include_partial))
        for r in runs:
            if r["run_id"] == run_id:
                return r
        raise ValueError(f"run {run_id!r} not found (use cherfd_list_runs)")
    runs = group_runs(find_sweeps(data_root, file_dir, file_root, include_partial))
    if not runs:
        what = " ".join(x for x in (file_dir and f"file_dir={file_dir}",
                                    file_root is not None and f"file_root={file_root}") if x)
        raise ValueError(f"no cherfd sweeps found{(' for ' + what) if what else ''}")
    return runs[-1]


def select_sweeps(run: dict, sweeps: list[int] | None = None, direction: str = "both") -> list[SweepFile]:
    if direction not in ("both", "fwd", "rev"):
        raise ValueError("direction must be 'both', 'fwd' or 'rev'")
    out = [s for s in run["sweeps"]
           if (direction == "both" or s.direction == direction)
           and (not sweeps or s.sweep in set(sweeps))]
    if not out:
        raise ValueError(f"no sweeps match sweeps={sweeps} direction={direction} in {run['run_id']}")
    return out


def find_vortex_file(sf: SweepFile, max_lag_s: float = 30.0) -> Path | None:
    """The Xspress3 pickle for a sweep: same root/N/dir, timestamp 0..30 s later."""
    folder = Path(sf.path).parent
    head = f"vortex_{sf.root + '_' if sf.root else ''}{sf.sweep}_{sf.direction}_"
    best = None
    for p in folder.glob(head + "*.pkl"):
        m = _VORTEX_RE.match(p.name)
        if not m or (m.group("root") or "") != sf.root:
            continue
        lag = (_parse_ts(m.group("ts")) - sf.time).total_seconds()
        if -2.0 <= lag <= max_lag_s and (best is None or lag < best[0]):
            best = (lag, p)
    return best[1] if best else None


def public_run(run: dict, include_sweeps: bool = True) -> dict:
    out = {k: v for k, v in run.items() if k != "sweeps"}
    if include_sweeps:
        out["sweeps"] = [
            {"label": s.label, "sweep": s.sweep, "direction": s.direction,
             "timestamp": s.timestamp, "file": Path(s.path).name, "partial": s.partial}
            for s in run["sweeps"]
        ]
    return out
