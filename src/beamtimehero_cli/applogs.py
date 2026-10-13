"""Read-only access to the station's application and service logs.

For debugging from inside an agent: which app logged what, and when. The
reader only ever opens files under a fixed set of named log roots
(``BTH_APP_LOG_ROOTS``: ``name=/abs/dir,name=/abs/dir``; BL4-1 has a
station default listing its apps' ``logs/`` directories). A request names
a root and a path *relative to it*; anything that resolves outside the
root — ``..``, an absolute path, a symlink pointing elsewhere — is refused.
Nothing here writes, deletes or rotates a file.
"""
from __future__ import annotations

import os
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from beamtimehero_cli import station

#: Files the tools will read (plain-text logs only).
LOG_SUFFIXES = (".log", ".txt", ".jsonl", ".out", ".err")
MAX_TAIL_LINES = 2000
DEFAULT_TAIL_LINES = 200
MAX_SEARCH_MATCHES = 500
MAX_SEARCH_FILES = 50
MAX_LINE_CHARS = 2000


def roots() -> dict[str, Path]:
    raw = os.environ.get("BTH_APP_LOG_ROOTS") or station.default("BTH_APP_LOG_ROOTS")
    out: dict[str, Path] = {}
    for item in (raw or "").split(","):
        name, sep, path = item.strip().partition("=")
        if sep and name.strip() and path.strip():
            p = Path(path.strip()).expanduser()
            if p.is_absolute():
                out[name.strip()] = p
    return out


def _root(name: str) -> Path:
    rs = roots()
    if name not in rs:
        known = ", ".join(sorted(rs)) or "none configured (BTH_APP_LOG_ROOTS)"
        raise ValueError(f"unknown log root {name!r}; known: {known}")
    return rs[name]


def _resolve(root_name: str, rel: str) -> Path:
    """`rel` under the root, refusing anything that escapes it."""
    root = _root(root_name).resolve()
    if not rel or Path(rel).is_absolute():
        raise ValueError("give a path relative to the log root")
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"{rel!r} is outside the {root_name} log root")
    if not target.is_file():
        raise ValueError(f"{rel!r} is not a file under the {root_name} log root")
    if target.suffix not in LOG_SUFFIXES:
        raise ValueError(f"{target.name} is not a log file ({', '.join(LOG_SUFFIXES)})")
    return target


def _files(root: Path) -> list[Path]:
    root = root.resolve()
    out = []
    for p in root.rglob("*"):
        try:
            if p.is_file() and p.suffix in LOG_SUFFIXES and p.resolve().is_relative_to(root):
                out.append(p)
        except OSError:
            continue
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def list_logs(root_name: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Log files per root, newest first."""
    rs = roots()
    names = [root_name] if root_name else sorted(rs)
    out: dict[str, Any] = {"ok": True, "roots": {}}
    for name in names:
        root = _root(name)
        if not root.is_dir():
            out["roots"][name] = {"path": str(root), "error": "directory does not exist"}
            continue
        files = _files(root)[: max(1, int(limit))]
        out["roots"][name] = {
            "path": str(root),
            "files": [{"path": str(p.resolve().relative_to(root.resolve())),
                       "size_bytes": p.stat().st_size,
                       "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
                      for p in files],
        }
    return out


def _clip(line: str) -> str:
    line = line.rstrip("\n")
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " …[truncated]"


def read_log(root_name: str, path: str, lines: int = DEFAULT_TAIL_LINES) -> dict[str, Any]:
    """The last `lines` lines of one log file."""
    target = _resolve(root_name, path)
    n = max(1, min(int(lines), MAX_TAIL_LINES))
    with open(target, "r", errors="replace") as fh:
        tail = deque(fh, maxlen=n)
    return {"ok": True, "root": root_name, "path": path, "lines": [_clip(x) for x in tail],
            "n_lines": len(tail), "size_bytes": target.stat().st_size}


def search_logs(pattern: str, root_name: str | None = None, max_matches: int = 100,
                ignore_case: bool = True) -> dict[str, Any]:
    """Regex search over the newest log files of one root (or all)."""
    try:
        rx = re.compile(pattern, re.I if ignore_case else 0)
    except re.error as e:
        raise ValueError(f"bad regex: {e}") from e
    cap = max(1, min(int(max_matches), MAX_SEARCH_MATCHES))
    matches: list[dict] = []
    names = [root_name] if root_name else sorted(roots())
    for name in names:
        root = _root(name)
        if not root.is_dir():
            continue
        for p in _files(root)[:MAX_SEARCH_FILES]:
            try:
                with open(p, "r", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            matches.append({"root": name, "path": str(p.resolve().relative_to(root.resolve())),
                                            "line_no": i, "line": _clip(line)})
                            if len(matches) >= cap:
                                return {"ok": True, "matches": matches, "truncated": True}
            except OSError:
                continue
    return {"ok": True, "matches": matches, "truncated": False}
