"""Configuration for the beamtimehero_cli package.

Owns: SPEC transport, sqlite path, beamline scan/log directories, timezone,
and CLI invocation logging knobs. All values resolve from environment
variables (loaded from a .env file in the caller's CWD if present).

This module is project-agnostic: no orchestration, LLM, or web concerns.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional YAML configuration
# ---------------------------------------------------------------------------
def _load_yaml_config() -> None:
    """Apply ``BEAMTIMEHERO_CONFIG``'s ``env:`` mapping to the environment.

    Every setting in this package resolves from an environment variable, which
    suits a container manifest but is awkward to hand a person: there are three
    dozen of them and no single place that lists them. Pointing
    ``BEAMTIMEHERO_CONFIG`` at a YAML file makes that list a real, checkable
    artifact — see ``config.example.yaml``.

    Uses ``setdefault``, so a variable already exported always wins over the
    file. A missing or malformed file warns and is skipped rather than
    preventing the CLI from starting, since a broken config file should not
    make the beamline unreachable.
    """
    path = os.environ.get("BEAMTIMEHERO_CONFIG")
    if not path:
        return
    try:
        import yaml

        data = yaml.safe_load(Path(path).read_text()) or {}
        env = data.get("env") or {}
        if not isinstance(env, dict):
            raise TypeError("top-level 'env:' must be a mapping")
    except Exception as e:
        logger.warning(
            "BEAMTIMEHERO_CONFIG=%s could not be loaded (%s); falling back to "
            "the environment alone.", path, e,
        )
        return
    for key, value in env.items():
        if value is not None:
            os.environ.setdefault(str(key), str(value))


_load_yaml_config()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PACKAGE_ROOT = Path(__file__).resolve().parent


def _repo_root() -> Path | None:
    """The repo root, when running from a source checkout.

    ``PACKAGE_ROOT.parent.parent`` is the repo root for a ``pip install -e``
    or a plain ``src/`` checkout, but for a wheel install it is the
    interpreter's ``lib/pythonX.Y`` directory — so deriving the data
    directory from it wrote the action log inside site-packages. Confirm
    with pyproject.toml rather than assuming.
    """
    candidate = PACKAGE_ROOT.parent.parent
    return candidate if (candidate / "pyproject.toml").is_file() else None


def _default_data_dir() -> Path:
    """Where writable state goes when BEAMTIMEHERO_DATA_DIR is unset.

    A source checkout keeps ``<repo>/data`` — that is where the existing
    action log lives and .gitignore already covers it. An installed package
    has no repo to write into, so it uses the XDG user-data directory.
    """
    repo = _repo_root()
    if repo is not None:
        return repo / "data"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "beamtimehero"


# Kept for the sibling applications that import it. None when the package is
# installed rather than run from a checkout — check before joining onto it.
PROJECT_ROOT = _repo_root()

DATA_DIR = Path(os.environ.get("BEAMTIMEHERO_DATA_DIR") or _default_data_dir())


def ensure_data_dir() -> Path:
    """Create DATA_DIR on first write and return it.

    Not done at import: importing a library should not create directories,
    and on a wheel install the old import-time mkdir landed one inside the
    interpreter's lib directory.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR

# ---------------------------------------------------------------------------
# SPEC dispatcher — defaults to mock so the CLI is usable off-beamline.
# ---------------------------------------------------------------------------
SPEC_SCREEN_NAME = "spec"
SPEC_POLL_INTERVAL_S = 2.0
SPEC_PROMPT_REGEX = r"^\d+\.SPEC> ?$"
SPEC_MOCK = os.getenv("SPEC_MOCK", "1") == "1"

# Transport: "tcp" (spec server binary protocol), "screen" (GNU screen
# stuffing), "sandbox" (HTTP API to sim-mode SPEC).
SPEC_TRANSPORT = os.getenv("SPEC_TRANSPORT", "tcp")
SPEC_HOST = os.getenv("SPEC_HOST", "localhost")
SPEC_PORT = int(os.getenv("SPEC_PORT", "2033"))
SPEC_NAME = os.getenv("SPEC_NAME", "spec")

SPEC_EVAL_URL = os.getenv("SPEC_EVAL_URL", "http://127.0.0.1:5006")

# ---------------------------------------------------------------------------
# Research sandbox — the `research ask-question` leaf.
# ---------------------------------------------------------------------------
# A local HTTP service that answers an analysis question by running an agent
# with web access inside a locked-down container. Loopback-pinned in
# `research_client.py` for the same reason spec-eval is: the request carries a
# free-text question that the service turns into a container run.
AGENT_SANDBOX_URL = os.getenv("AGENT_SANDBOX_URL", "http://127.0.0.1:5007")

# Off unless explicitly switched on. The sandbox reads the open web and hands
# back text that an agent will act on, so it is opt-in per deployment rather
# than "on wherever the service happens to be listening". With the flag unset
# the tool still parses and still answers — with an error explaining how to
# enable it — because a tool that raises is a crash and a tool that is absent
# is invisible.
AGENT_SANDBOX_ENABLED = os.getenv("AGENT_SANDBOX_ENABLED", "0") == "1"

# ---------------------------------------------------------------------------
# Action log SQLite — independent of any external schema.
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get("BEAMLINE_TOOLS_DB_PATH", str(DATA_DIR / "beamline_tools.db"))
os.environ.setdefault("BEAMLINE_TOOLS_DB_PATH", DB_PATH)

# ---------------------------------------------------------------------------
# CLI invocation log — one row per `beamtimehero` call.
# ---------------------------------------------------------------------------
CLI_LOG_ENABLED = os.getenv("BEAMTIMEHERO_CLI_LOG", "1") == "1"
CLI_LOG_MAX_RESULT_BYTES = int(os.getenv("BEAMTIMEHERO_CLI_LOG_MAX_BYTES", "65536"))

TOOLS_MODE = os.getenv("TOOLS_MODE", "cli")

# ---------------------------------------------------------------------------
# Analysis defaults (station-overridable)
# ---------------------------------------------------------------------------
def active_counter_priority() -> tuple[str, ...]:
    """Station-level override of the analysis counter auto-pick order.

    ``BTH_ACTIVE_COUNTER_PRIORITY`` is a comma-separated counter list; the
    first name present in a scan's columns wins. Empty/unset (the default)
    keeps the historical BL15-2 selection logic untouched.

    Read at call time (not import time) so it can be changed per-process.
    Passed into ``science.reduce.counters.pick_active_counter``, which stays
    pure — see ``science/README.md``.
    """
    raw = os.environ.get("BTH_ACTIVE_COUNTER_PRIORITY", "")
    return tuple(c.strip() for c in raw.split(",") if c.strip())


# ---------------------------------------------------------------------------
# EPICS PVs (reference only — not wired here)
# ---------------------------------------------------------------------------
# Station-specific PVs are env-overridable (BTH_-prefixed to avoid clashing
# with unrelated EPICS software's environment) so other beamlines can consume
# this package; defaults remain the BL15-2 values.
EPICS_PV_SPEAR_CURRENT = os.getenv("BTH_EPICS_PV_SPEAR_CURRENT", "SPEAR:BeamCurrAvg")
EPICS_PV_BL_STATE = os.getenv("BTH_EPICS_PV_BL_STATE", "BL15:State")
EPICS_PV_GAP_OWNER = os.getenv("BTH_EPICS_PV_GAP_OWNER", "BL15:GapOwnerNode")

# ---------------------------------------------------------------------------
# Beamline data directories and timezone
# ---------------------------------------------------------------------------
BL_TIMEZONE = ZoneInfo("America/Los_Angeles")


def now_pacific() -> datetime:
    """Current time in Pacific, naive datetime for comparison."""
    return datetime.now(BL_TIMEZONE).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Beamline data directories
# ---------------------------------------------------------------------------
# There is no bundled demo data. When a directory is not configured, the
# resolved path is kept as-is and the *_CONFIGURED flag below is False; the
# tools that read it say so in their output rather than serving something
# that looks live. Nothing is logged at import, so `--help` stays clean.

# True when the corresponding directory resolved to a real, existing
# beamline directory. This is the flag to check — a tool holding False
# should report "not configured" rather than an empty result that reads
# like "no scans exist".
SCAN_DIR_CONFIGURED = False
LOGS_DIR_CONFIGURED = False

# Retained so the sibling applications that read them keep importing. This
# package has no sample data, so they are permanently False.
USING_SAMPLE_DATA = False
USING_SAMPLE_LOGS = False

BL_LOGS_DIR = Path(os.getenv("BL_LOGS_DIR", "/usr/local/lib/spec.log/logfiles"))
LOGS_DIR_CONFIGURED = BL_LOGS_DIR.exists()

_DATA_ROOT = Path(os.getenv("BL_SCAN_DIR", "/data/fifteen"))


def _resolve_scan_dir(root: Path) -> tuple[Path, bool]:
    """Resolve the active scan directory.

    Returns ``(scan_dir, configured)``. A root that is itself dated
    (``YYYY-mm_*``) is used directly; otherwise the most recently modified
    dated subdirectory wins, which is how the beamline rolls over between
    runs. When neither resolves, the configured root is returned unchanged
    with ``configured=False`` — the caller reports that, rather than
    substituting a directory the user did not ask for.
    """
    if root.is_dir():
        if re.match(r"\d{4}-\d{2}_", root.name):
            return root, True
        subdirs = [d for d in root.iterdir()
                   if d.is_dir() and re.match(r"\d{4}-\d{2}_", d.name)]
        if subdirs:
            return max(subdirs, key=lambda d: d.stat().st_mtime), True
    return root, False


def _set_scan_dir_globals(scan_dir: Path, configured: bool) -> None:
    """Update BL_SCAN_DIR and the configuration flag."""
    global BL_SCAN_DIR, SCAN_DIR_CONFIGURED
    BL_SCAN_DIR = scan_dir
    SCAN_DIR_CONFIGURED = configured


def data_dir_status() -> dict:
    """Which beamline directories are configured, for tools to report.

    Kept here so every tool that reads scan or log files describes the
    condition the same way, in-band in its JSON, instead of each one
    inventing a phrasing or logging to stderr where an agent reads it as a
    crash.
    """
    return {
        "scan_dir": str(BL_SCAN_DIR),
        "scan_dir_configured": SCAN_DIR_CONFIGURED,
        "logs_dir": str(BL_LOGS_DIR),
        "logs_dir_configured": LOGS_DIR_CONFIGURED,
    }


_set_scan_dir_globals(*_resolve_scan_dir(_DATA_ROOT))


def set_scan_dir(name: str) -> Path:
    """Set BL_SCAN_DIR to a subdirectory of the data root.

    Pass 'auto' to re-run auto-detection.
    """
    if name == "auto":
        _set_scan_dir_globals(*_resolve_scan_dir(_DATA_ROOT))
        logger.info("Scan directory auto-detected: %s", BL_SCAN_DIR)
        return BL_SCAN_DIR

    target = _DATA_ROOT / name
    if not target.is_dir():
        raise ValueError(f"Directory does not exist: {target}")

    _set_scan_dir_globals(target, True)
    logger.info("Scan directory set to: %s", BL_SCAN_DIR)
    return BL_SCAN_DIR


LOG_FILE_PATTERN = "log__*"

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
MAX_LOG_LINES = 1000

# ---------------------------------------------------------------------------
# Sample camera (RPi-Cam snapshot endpoint)
# ---------------------------------------------------------------------------
SAMPLE_CAM_HOST = os.getenv("SAMPLE_CAM_HOST", "192.168.150.93")
SAMPLE_CAM_PORT = int(os.getenv("SAMPLE_CAM_PORT", "8080"))
SAMPLE_CAM_DEFAULT_QUALITY = int(os.getenv("SAMPLE_CAM_QUALITY", "50"))
