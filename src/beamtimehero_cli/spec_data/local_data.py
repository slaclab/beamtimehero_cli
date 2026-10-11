"""Local filesystem data access -- reads SPEC data files directly via silx.

Scans the active scan directory for SPEC files, parses them with
silx.io.specfile.SpecFile, and caches scan metadata in a JSON sidecar
file for performance on repeated queries.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX; advisory locking is a no-op
    fcntl = None

import pandas as pd
from silx.io.specfile import SpecFile, is_specfile

logger = logging.getLogger(__name__)

from beamtimehero_cli import config as bl_config
from beamtimehero_cli import station

_metadata_cache: dict | None = None
# file_path -> (mtime, size) at last successful parse
_cached_file_sigs: dict[str, tuple[float, int]] = {}


class ScanReadError(Exception):
    """A SPEC scan exists in the metadata cache but could not be parsed."""


# ---------------------------------------------------------------------------
# SPEC file helpers
# ---------------------------------------------------------------------------

_SPECFILE_HANDLE_MAX = 8
# (path, mtime, size) -> SpecFile; small LRU so multi-scan operations
# (e.g. averaging all reps of a file) parse the file once, not N+1 times.
_specfile_handles: OrderedDict[tuple[str, float, int], SpecFile] = OrderedDict()


def _open_specfile(spec_path: str | Path) -> SpecFile:
    """Open a SPEC file through a small (path, mtime, size)-keyed LRU.

    Returns a cached ``SpecFile`` when the file is unchanged since it was
    last opened; otherwise re-parses and replaces any stale handle for
    the same path. Raises whatever ``SpecFile``/``stat`` raise.
    """
    st = Path(spec_path).stat()
    key = (str(spec_path), st.st_mtime, st.st_size)
    sf = _specfile_handles.get(key)
    if sf is not None:
        _specfile_handles.move_to_end(key)
        return sf
    sf = SpecFile(str(spec_path))
    # Drop stale handles for the same path before inserting the fresh one.
    for stale in [k for k in _specfile_handles if k[0] == key[0]]:
        del _specfile_handles[stale]
    _specfile_handles[key] = sf
    while len(_specfile_handles) > _SPECFILE_HANDLE_MAX:
        _specfile_handles.popitem(last=False)
    return sf


def _parse_spec_date(header_lines: list[str]) -> str | None:
    """Extract date from #D header line and return as ISO string."""
    for line in header_lines:
        if line.startswith("#D "):
            date_str = line[3:].strip()
            for fmt in (
                "%a %b %d %H:%M:%S %Y",
                "%a %b  %d %H:%M:%S %Y",
            ):
                try:
                    return datetime.strptime(date_str, fmt).isoformat()
                except ValueError:
                    continue
            try:
                from email.utils import parsedate_to_datetime
                return parsedate_to_datetime(date_str).isoformat()
            except Exception:
                pass
    return None


def _parse_count_time(header_lines: list[str]) -> float | None:
    """Extract count time from #T header line."""
    for line in header_lines:
        if line.startswith("#T "):
            parts = line[3:].strip().split()
            if parts:
                try:
                    return float(parts[0])
                except ValueError:
                    pass
    return None


def _parse_scan_command(header_lines: list[str]) -> tuple[int | None, str]:
    """Extract scan number and command from #S header line."""
    for line in header_lines:
        if line.startswith("#S "):
            parts = line[3:].strip().split(None, 1)
            scan_num = int(parts[0]) if parts else None
            command = parts[1] if len(parts) > 1 else ""
            return scan_num, command
    return None, ""


def _read_spec_scan(spec_path: str | Path, scan_index: int) -> pd.DataFrame | None:
    """Read a single scan from a SPEC file and return as a DataFrame.

    Returns None for an empty (header-only) scan; raises
    :class:`ScanReadError` when the file/scan exists but cannot be parsed.
    """
    try:
        sf = _open_specfile(spec_path)
        scan = sf[scan_index]
        labels = scan.labels
        data = scan.data

        if data.size == 0:
            return None

        df = pd.DataFrame(data.T, columns=labels)
        if labels:
            df = df.set_index(labels[0])

        header = scan.scan_header
        date_str = _parse_spec_date(header)
        count_time = _parse_count_time(header)
        _, command = _parse_scan_command(header)

        motor_dict = {}
        try:
            motor_dict = dict(zip(scan.motor_names, scan.motor_positions))
        except Exception:
            pass

        epoch_col = None
        for col in df.columns:
            if col.lower() == "epoch":
                epoch_col = col
                break

        wall_clock = None
        acquisition = None
        dead_time = None
        if count_time is not None:
            acquisition = count_time * len(df)
        if epoch_col is not None and len(df) > 1:
            epoch_vals = df[epoch_col].values.astype(float)
            wall_clock = float(epoch_vals[-1] - epoch_vals[0])
            if acquisition is not None:
                dead_time = wall_clock - acquisition

        df.attrs = {
            "date_time": datetime.fromisoformat(date_str) if date_str else None,
            "epoch": datetime.fromisoformat(date_str).timestamp() if date_str else None,
            "motor_positions": motor_dict,
            "scan_command": command,
            "counters": list(df.columns),
            "num_points": len(df),
            "count_time": count_time,
            "acquisition_seconds": acquisition,
            "wall_clock_seconds": wall_clock,
            "dead_time_seconds": dead_time,
        }
        return df
    except Exception as e:
        logger.warning("Failed to read scan %d from %s: %s", scan_index, spec_path, e)
        raise ScanReadError(
            f"scan index {scan_index} in {spec_path} could not be parsed: {e}"
        ) from e


# ---------------------------------------------------------------------------
# Scan metadata cache
# ---------------------------------------------------------------------------

# v2: cache entries are keyed by path-relative-to-BL_SCAN_DIR (v1 used the
# bare file name, so same-named files in two experiment subdirs silently
# overwrote each other). New filename so v1 sidecars rebuild instead of
# half-matching.
_CACHE_FILENAME = ".scan_metadata_cache_v2.json"


def _get_cache_file() -> Path | None:
    scan_dir = bl_config.BL_SCAN_DIR
    if scan_dir:
        return scan_dir / _CACHE_FILENAME
    return None


def _entry_key(spec_path: Path, scan_number) -> str:
    """Cache key: path relative to BL_SCAN_DIR (fallback: name) + scan no."""
    try:
        rel = spec_path.resolve().relative_to(Path(bl_config.BL_SCAN_DIR).resolve())
    except (ValueError, OSError):
        rel = spec_path.name
    return f"{rel}::{scan_number}"


def _lookup_entry(cache: dict, file_name, scan_number) -> dict | None:
    """Resolve a scan by file_name (bare name OR scan-dir-relative path).

    Bare names can match several files across experiment subdirs; pick
    the most recently modified — matching pre-v2 freshness expectations
    while no longer silently losing the other file's scans.
    """
    direct = cache.get(f"{file_name}::{scan_number}")
    if direct is not None:
        return direct
    best = None
    for entry in cache.values():
        if entry.get("file_name") != file_name:
            continue
        if str(entry.get("scan_number")) != str(scan_number):
            continue
        if best is None or entry.get("file_mtime", 0) > best.get("file_mtime", 0):
            best = entry
    return best


@contextmanager
def _cache_lock():
    """Advisory inter-process lock around load-merge-save of the cache.

    Uses ``fcntl.flock`` on a ``.lock`` sidecar next to the cache file so
    concurrent CLI processes serialize their load-merge-save sequences
    instead of clobbering each other's writes. Best-effort: any failure
    to acquire the lock degrades to unlocked operation.
    """
    cache_file = _get_cache_file()
    fh = None
    if cache_file is not None and fcntl is not None:
        lock_file = cache_file.with_name(cache_file.name + ".lock")
        try:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            fh = open(lock_file, "a")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            if fh is not None:
                fh.close()
            fh = None
    try:
        yield
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()


def _load_cache_file(cache_file: Path) -> dict | None:
    """Read the sidecar cache from disk. Returns None when missing,
    unreadable, or not the expected dict-of-dicts shape (e.g. ``null`` or
    a list left by a corrupt/partial write) — caller rebuilds in that case.
    """
    if not cache_file.exists():
        return None
    try:
        loaded = json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(loaded, dict):
        logger.warning(
            "Metadata cache %s contains %s instead of an object; rebuilding.",
            cache_file, type(loaded).__name__,
        )
        return None
    # Drop malformed (non-dict) entries instead of AttributeError-ing later.
    return {k: v for k, v in loaded.items() if isinstance(v, dict)}


def _load_cache() -> dict:
    """Load or rebuild the scan metadata cache.

    Checks per-file (mtime, size) signatures to detect new or changed
    SPEC files and re-parses only those files.
    """
    global _metadata_cache

    scan_dir = bl_config.BL_SCAN_DIR

    if _metadata_cache is not None:
        # Check if any SPEC files have changed or new ones appeared
        changed = _find_changed_files(scan_dir)
        if not changed:
            return _metadata_cache
        # Re-parse only changed files, merge into existing cache
        with _cache_lock():
            logger.info("Re-parsing %d changed SPEC file(s)", len(changed))
            new_entries = _parse_spec_files(changed)
            _metadata_cache.update(new_entries)
            _save_cache()
        return _metadata_cache

    with _cache_lock():
        # Try loading from disk
        cache_file = _get_cache_file()
        loaded = _load_cache_file(cache_file) if cache_file else None
        if loaded is not None:
            _metadata_cache = loaded
            # Rebuild file signature tracking from cache entries
            for entry in _metadata_cache.values():
                fp = entry.get("file_path")
                if fp:
                    _cached_file_sigs[fp] = (
                        entry.get("file_mtime", 0),
                        entry.get("file_size", -1),
                    )
            # Check for changes since disk cache was written
            changed = _find_changed_files(scan_dir)
            if changed:
                logger.info("Re-parsing %d changed SPEC file(s) since last cache", len(changed))
                new_entries = _parse_spec_files(changed)
                _metadata_cache.update(new_entries)
                _save_cache()
            return _metadata_cache

        # Build from scratch
        _metadata_cache = _build_metadata_cache()
        _save_cache()
        return _metadata_cache


def _find_changed_files(scan_dir: Path) -> list[Path]:
    """Find SPEC files that are new or have a different (mtime, size) than cached."""
    changed = []
    if not scan_dir or not scan_dir.exists():
        return changed

    for child in scan_dir.iterdir():
        if child.is_dir():
            for spec_path in child.iterdir():
                if not spec_path.is_file():
                    continue
                _check_file(spec_path, changed)
        elif child.is_file():
            _check_file(child, changed)

    return changed


def _check_file(spec_path: Path, changed: list[Path]):
    """Check if a single file needs re-parsing.

    Compares (mtime, size) for inequality — mtime alone misses
    same-second rewrites and backwards clock adjustments. The new
    signature is recorded by ``_parse_spec_files`` only AFTER a
    successful parse, so a failed parse is retried on the next call.
    """
    try:
        if not is_specfile(str(spec_path)):
            return
    except Exception:
        return
    try:
        st = spec_path.stat()
    except OSError:
        return
    fp = str(spec_path)
    if _cached_file_sigs.get(fp) != (st.st_mtime, st.st_size):
        changed.append(spec_path)


def _parse_spec_files(file_list: list[Path]) -> dict:
    """Parse a list of SPEC files and return cache entries."""
    cache = {}
    for spec_path in file_list:
        # Stat BEFORE parsing: if the file changes mid-parse, the recorded
        # signature is the pre-parse one and the file is re-parsed next call.
        try:
            st = spec_path.stat()
        except OSError:
            continue
        try:
            sf = _open_specfile(spec_path)
        except Exception:
            logger.warning("Failed to open SPEC file: %s", spec_path)
            continue

        file_name = spec_path.name
        file_mtime = st.st_mtime
        file_size = st.st_size
        exp_name = spec_path.parent.name

        for scan_idx in range(len(sf)):
            try:
                scan = sf[scan_idx]
                header = scan.scan_header
                scan_number = scan.number
                _, command = _parse_scan_command(header)
                date_str = _parse_spec_date(header)
                count_time = _parse_count_time(header)
                labels = scan.labels
                data = scan.data
                num_points = data.shape[1] if data.ndim == 2 else 0

                motor_dict = {}
                try:
                    motor_dict = dict(zip(scan.motor_names, scan.motor_positions))
                except Exception:
                    pass

                acquisition = None
                wall_clock = None
                dead_time = None
                if count_time is not None and num_points > 0:
                    acquisition = count_time * num_points
                if num_points > 1 and labels:
                    epoch_idx = None
                    for i, lbl in enumerate(labels):
                        if lbl.lower() == "epoch":
                            epoch_idx = i
                            break
                    if epoch_idx is not None:
                        epoch_vals = data[epoch_idx]
                        wall_clock = float(epoch_vals[-1] - epoch_vals[0])
                        if acquisition is not None:
                            dead_time = wall_clock - acquisition

                key = _entry_key(spec_path, scan_number)
                cache[key] = {
                    "file_name": file_name,
                    "file_path": str(spec_path),
                    "experiment": exp_name,
                    "scan_number": scan_number,
                    "scan_index": scan_idx,
                    "scan_command": command,
                    "date_time": date_str,
                    "epoch": datetime.fromisoformat(date_str).timestamp() if date_str else None,
                    "motor_positions": motor_dict,
                    "counters": list(labels) if labels else [],
                    "num_points": num_points,
                    "count_time": count_time,
                    "acquisition_seconds": acquisition,
                    "wall_clock_seconds": wall_clock,
                    "dead_time_seconds": dead_time,
                    "file_mtime": file_mtime,
                    "file_size": file_size,
                }
            except Exception as e:
                logger.warning("Failed to parse scan %d in %s: %s", scan_idx, spec_path, e)
                continue

        # Record the signature only AFTER the file parsed successfully,
        # so a failed parse is retried on the next call.
        _cached_file_sigs[str(spec_path)] = (file_mtime, file_size)

    return cache


def _build_metadata_cache() -> dict:
    """Scan BL_SCAN_DIR for SPEC files and extract scan metadata via silx."""
    scan_dir = bl_config.BL_SCAN_DIR
    if not scan_dir or not scan_dir.exists():
        return {}

    all_spec_files = []
    for child in scan_dir.iterdir():
        if child.is_dir():
            for spec_path in child.iterdir():
                if spec_path.is_file():
                    try:
                        if is_specfile(str(spec_path)):
                            all_spec_files.append(spec_path)
                    except Exception:
                        continue
        elif child.is_file():
            try:
                if is_specfile(str(child)):
                    all_spec_files.append(child)
            except Exception:
                continue

    # _parse_spec_files records the (mtime, size) signature per file
    # after each successful parse.
    return _parse_spec_files(all_spec_files)


def _save_cache():
    """Persist cache to disk atomically.

    Writes to a temp file in the same directory then ``os.replace()``s it
    over the cache, so readers never see a partially written JSON file.
    """
    cache_file = _get_cache_file()
    if not cache_file or not _metadata_cache:
        return
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(cache_file.parent),
            prefix=cache_file.name + ".",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(_metadata_cache, default=str))
            os.replace(tmp_path, str(cache_file))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.warning("Failed to save metadata cache: %s", e)


def clear_cache():
    """Clear the in-memory cache. Called when scan dir changes."""
    global _metadata_cache
    _metadata_cache = None
    _cached_file_sigs.clear()
    _specfile_handles.clear()


def refresh_cache():
    """Force a full cache rebuild."""
    clear_cache()
    _load_cache()


def _all_scans_sorted() -> list[dict]:
    """Return all cached scan metadata, sorted by date_time descending."""
    cache = _load_cache()
    scans = list(cache.values())
    scans.sort(key=lambda s: s.get("date_time") or "", reverse=True)
    return scans


# --- Public API ---

def list_processed_scans(limit=20) -> list[dict]:
    """List scans, most recent first."""
    scans = _all_scans_sorted()[:limit]
    return [
        {
            "file_name": s["file_name"],
            "scan_number": s["scan_number"],
            "scan_command": s["scan_command"],
            "date_time": s["date_time"],
            "num_points": s["num_points"],
            "counters": s["counters"],
            "count_time": s["count_time"],
            "acquisition_seconds": s["acquisition_seconds"],
        }
        for s in scans
    ]


def get_scan_metadata(file_name, scan_number) -> dict | None:
    """Get full metadata for a single scan."""
    cache = _load_cache()
    entry = _lookup_entry(cache, file_name, scan_number)
    if not entry:
        return None
    return {
        "file_name": entry["file_name"],
        "file_path": entry["file_path"],
        "scan_number": entry["scan_number"],
        "scan_command": entry["scan_command"],
        "date_time": entry["date_time"],
        "epoch": entry["epoch"],
        "motor_positions": entry["motor_positions"],
        "counters": entry["counters"],
        "num_points": entry["num_points"],
        "count_time": entry["count_time"],
        "acquisition_seconds": entry["acquisition_seconds"],
    }


def read_processed_scan(file_name, scan_number) -> pd.DataFrame | None:
    """Read scan data from the SPEC file. Returns DataFrame or None.

    None covers both scan-not-found and parse failure (backward compat);
    use :func:`read_processed_scan_ex` to distinguish the two.
    """
    df, _reason = read_processed_scan_ex(file_name, scan_number)
    return df


def read_processed_scan_ex(file_name, scan_number) -> tuple[pd.DataFrame | None, str | None]:
    """Like :func:`read_processed_scan` but returns ``(df, reason)``.

    ``reason`` is None on success, ``"not_found"`` when the scan is not
    in the cache (or its file vanished), ``"empty_scan"`` for a
    header-only scan, or ``"parse_error: ..."`` when the file exists but
    silx failed to parse it.
    """
    cache = _load_cache()
    entry = _lookup_entry(cache, file_name, scan_number)
    if not entry or not entry.get("file_path"):
        return None, "not_found"
    spec_path = Path(entry["file_path"])
    if not spec_path.exists():
        return None, "not_found"
    scan_index = entry.get("scan_index")
    if scan_index is None:
        return None, "not_found"
    try:
        df = _read_spec_scan(spec_path, scan_index)
    except ScanReadError as e:
        return None, f"parse_error: {e}"
    if df is None:
        return None, "empty_scan"
    return df, None


def get_scan_deadtime(file_name, scan_number) -> dict | None:
    """Get dead time info for a single scan."""
    cache = _load_cache()
    entry = _lookup_entry(cache, file_name, scan_number)
    if not entry:
        return None

    acq = entry.get("acquisition_seconds")
    wall = entry.get("wall_clock_seconds")
    dead = entry.get("dead_time_seconds")
    dead_pct = None
    if wall and dead is not None:
        dead_pct = round(100 * dead / wall, 2)

    return {
        "file_name": file_name,
        "scan_number": scan_number,
        "scan_command": entry.get("scan_command"),
        "num_points": entry.get("num_points"),
        "count_time": entry.get("count_time"),
        "acquisition_seconds": acq,
        "wall_clock_seconds": wall,
        "dead_time_seconds": dead,
        "dead_time_pct": dead_pct,
    }


def get_most_recent_file() -> str | None:
    """Find the most recently modified SPEC file (excluding alignment)."""
    for s in _all_scans_sorted():
        if s["file_name"] not in ("alignment", "alignment_Fe"):
            return s["file_name"]
    return None


def _energy_scan_prefixes() -> tuple[str, ...]:
    """Scan-command prefixes that count as energy scans.

    ``BTH_ENERGY_SCAN_PREFIXES`` is a comma-separated list; unset keeps the
    BL15-2 default (``gscan energy``). A station whose energy motor is
    ``mono`` sets e.g. ``gscan mono,kscan``.
    """
    raw = os.environ.get("BTH_ENERGY_SCAN_PREFIXES") or station.default("BTH_ENERGY_SCAN_PREFIXES")
    prefixes = tuple(p.strip() for p in raw.split(",") if p.strip())
    return prefixes or ("gscan energy",)


def average_latest_energy_scans_file() -> str | None:
    """Find latest file with >1 energy scan. Returns file_name or None."""
    cache = _load_cache()
    prefixes = _energy_scan_prefixes()

    file_energy_counts: dict[str, tuple[int, str]] = {}
    for entry in cache.values():
        fn = entry["file_name"]
        if fn in ("alignment", "alignment_Fe"):
            continue
        cmd = entry.get("scan_command") or ""
        if cmd.startswith(prefixes):
            if fn not in file_energy_counts:
                file_energy_counts[fn] = (0, "")
            count, max_dt = file_energy_counts[fn]
            dt = entry.get("date_time") or ""
            file_energy_counts[fn] = (count + 1, max(max_dt, dt))

    candidates = [(fn, count, max_dt) for fn, (count, max_dt) in file_energy_counts.items() if count > 1]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[0][0]


def get_scan_numbers_for_file(file_name) -> list[int]:
    """Get all scan numbers for a file, sorted ascending."""
    cache = _load_cache()
    numbers = []
    for key, entry in cache.items():
        if entry["file_name"] == file_name:
            numbers.append(entry["scan_number"])
    numbers.sort()
    return numbers


# ---------------------------------------------------------------------------
# File access tools (non-SPEC files in scan dir)
# ---------------------------------------------------------------------------

MAX_READ_SIZE = 100 * 1024  # 100KB


def list_files(pattern: str = "*") -> list[dict]:
    """List non-SPEC files in the scan directory."""
    scan_dir = bl_config.BL_SCAN_DIR
    if not scan_dir or not scan_dir.exists():
        return []

    results = []
    for p in sorted(scan_dir.rglob(pattern)):
        if not p.is_file():
            continue
        # Skip SPEC data files and cache files
        try:
            if is_specfile(str(p)):
                continue
        except Exception:
            pass
        if p.name.startswith("."):
            continue
        results.append({
            "path": str(p.relative_to(scan_dir)),
            "size": p.stat().st_size,
        })
    return results


def read_file(rel_path: str) -> str:
    """Read a file from the scan directory.

    Args:
        rel_path: Path relative to BL_SCAN_DIR.

    Returns:
        File contents as string.

    Raises:
        ValueError: If path is outside scan dir or file too large.
        FileNotFoundError: If file doesn't exist.
    """
    scan_dir = bl_config.BL_SCAN_DIR
    if not scan_dir:
        raise ValueError("No scan directory configured")

    target = (scan_dir / rel_path).resolve()
    # Path traversal check
    try:
        target.relative_to(scan_dir.resolve())
    except ValueError:
        raise ValueError(f"Path is outside scan directory: {rel_path}")

    if not target.is_file():
        raise FileNotFoundError(f"File not found: {rel_path}")

    if target.stat().st_size > MAX_READ_SIZE:
        raise ValueError(f"File too large ({target.stat().st_size} bytes, limit {MAX_READ_SIZE})")

    return target.read_text()


def write_file(filename: str, content: str) -> str:
    """Write a file to the scan directory.

    Args:
        filename: Name only (no subdirectories). Must end in .txt or .mac.
        content: File contents.

    Returns:
        The path of the written file relative to scan dir.

    Raises:
        ValueError: If filename is invalid or path escapes scan dir.
    """
    scan_dir = bl_config.BL_SCAN_DIR
    if not scan_dir:
        raise ValueError("No scan directory configured")

    # Validate extension
    if not (filename.endswith(".txt") or filename.endswith(".mac")):
        raise ValueError("Only .txt and .mac files can be written")

    target = (scan_dir / filename).resolve()
    try:
        target.relative_to(scan_dir.resolve())
    except ValueError:
        raise ValueError(f"Invalid filename: {filename}")

    target.write_text(content)
    return str(target.relative_to(scan_dir))
