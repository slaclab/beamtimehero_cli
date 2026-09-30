"""Where the tools look, and how much they may do per call.

Everything is resolved from the environment at CALL time, never at import
time, so a host (chemcatal-bth today, beamtimehero_cli later) can set the
roots per session before the first call without an env-before-import dance.

    TENDER_DATA_DIR        the beamtime's raw directory (read-only). Holds one
                           subdirectory per compound, or the .sif files
                           directly. Falls back to BL_SCAN_DIR, which is what
                           chemcatal-bth already sets from scope.json.
    TENDER_PROCESSED_DIR   the beamtime's processed job tree
                           (<OUTPUT_ROOT>/.../<exp>/pipeline): one directory
                           per job with a manifest.json. Optional; without it
                           the tools cannot see first-pass or job results.
    TENDER_WORKSPACE_DIR   the user's workspace for this beamtime (read):
                           presets and calibration JSONs saved from the
                           notebooks. Optional.
    TENDER_CACHE_DIR       writable scratch for cached RIXS maps. Default
                           ./work/tender_cache (the chat session workspace's
                           writable work/ dir).
    TENDER_MAX_FRAMES      frames one call may reduce (default 400; about
                           25 s and a few hundred MB on the measured data).
    TENDER_MAX_FILE_FRAMES frames one file may hold: sif_parser decodes a
                           whole file at once as float64, 8 MB per frame, so
                           this caps peak memory per file (default 150).

Only BARE NAMES cross the tool boundary (a compound directory, a
measurement label, a file basename). They are validated here and joined onto
the roots above, so no argument can name a path outside them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# The same character class chemcat's SAFE_NAME_RE and tender_analysis's
# Measurement.label() use.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")

#: Bytes per frame on disk: sif stores 32-bit pixels, 512 x 2048 of them.
#: The measured 1-frame file is 4 197 836 bytes (header included), so this
#: estimates a file's frame count from its size without decoding it.
FRAME_BYTES = 512 * 2048 * 4


class ToolError(ValueError):
    """A refusal or a bad argument, reported to the agent as {"error": ...}."""


def safe_name(value: str, what: str) -> str:
    v = (value or "").strip()
    if not v or v in (".", "..") or not _SAFE_NAME.match(v):
        raise ToolError(f"{what} must be a bare name (letters, digits, . _ + -); got {value!r}")
    return v


def _env_dir(*names: str) -> Path | None:
    for name in names:
        v = os.environ.get(name, "").strip()
        if v:
            return Path(v)
    return None


def data_dir() -> Path:
    d = _env_dir("TENDER_DATA_DIR", "BL_SCAN_DIR")
    if d is None or not d.is_dir():
        raise ToolError("no Tender data directory in scope (TENDER_DATA_DIR / BL_SCAN_DIR unset "
                        "or missing)")
    return d


def processed_dir() -> Path | None:
    d = _env_dir("TENDER_PROCESSED_DIR")
    return d if d is not None and d.is_dir() else None


def workspace_dir() -> Path | None:
    d = _env_dir("TENDER_WORKSPACE_DIR")
    return d if d is not None and d.is_dir() else None


def cache_dir() -> Path:
    d = _env_dir("TENDER_CACHE_DIR") or Path.cwd() / "work" / "tender_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _int_env(name: str, default: int) -> int:
    """0 is meaningful: TENDER_MAX_FRAMES=0 means "never decode a frame" (a
    host running the tools inside a shared service, e.g. chemcat's local
    chat backend in the portal container). Listing, headers and the
    processed record still work; every re-reduction answers from the record."""
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def max_frames() -> int:
    return _int_env("TENDER_MAX_FRAMES", 400)


def max_file_frames() -> int:
    return _int_env("TENDER_MAX_FILE_FRAMES", 150)


def sample_dir(sample: str | None) -> Path:
    """The compound directory (or the beamtime directory itself for '')."""
    root = data_dir()
    if not sample:
        return root
    d = root / safe_name(sample, "sample")
    if not d.is_dir():
        raise ToolError(f"no compound directory {sample!r} in this beamtime")
    return d


def estimate_frames(path: str | Path) -> int:
    """Frame count from the file size (one stat, no decode)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0
    return max(1, round(size / FRAME_BYTES))
