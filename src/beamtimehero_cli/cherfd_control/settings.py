"""Environment-driven settings for the CHERFD transport.

Unlike ``beamtimehero_cli.config``, every value is resolved *at call time*,
so flipping ``CHERFD_MOCK`` takes effect without a restart and no import
order can pin it. They are documented with everything else in
``config.example.yaml`` (``tests/test_config_surface.py``).

The one to know is ``CHERFD_MOCK``. It is deliberately stricter than
beamtimehero_cli's ``SPEC_MOCK``: the mock stays on for **every value except
the exact string ``"0"``** -- unset, ``"1"``, ``""``, ``"true"``, a YAML
``false`` that arrives as ``"False"`` -- all mock. Going live has to be
spelled out, because live here means a real mono, a real undulator gap
and a real FPGA trigger list.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Hardware reach
# ---------------------------------------------------------------------------

def mock_enabled() -> bool:
    """True unless ``CHERFD_MOCK`` is exactly ``"0"``."""
    return os.environ.get("CHERFD_MOCK", "1") != "0"


def server_url() -> str:
    """Base URL of the cherfd control server (``server/server.py``)."""
    return (os.environ.get("CHERFD_SERVER_URL") or "http://127.0.0.1:5002").rstrip("/")


def daq_url() -> str:
    """Base URL of the cscan_daq count app (``cscan_daq/app.py``)."""
    return (os.environ.get("CHERFD_DAQ_URL") or "http://127.0.0.1:5004").rstrip("/")


def http_timeout_s() -> float:
    return float(os.environ.get("CHERFD_HTTP_TIMEOUT_S") or "10")


# ---------------------------------------------------------------------------
# The cherfd_claude checkout (simulator, logs, data)
# ---------------------------------------------------------------------------

def project_dir() -> Path | None:
    """The cherfd_claude checkout, or None when not configured."""
    value = os.environ.get("CHERFD_PROJECT_DIR")
    return Path(value).expanduser() if value else None


def simulator_python() -> str:
    """Interpreter used to run the production motion simulator.

    Defaults to the checkout's own ``venv/bin/python`` (the simulator imports
    pyepics at module load, even though it never calls it), else this
    interpreter.
    """
    explicit = os.environ.get("CHERFD_PYTHON")
    if explicit:
        return explicit
    proj = project_dir()
    if proj is not None:
        candidate = proj / "venv" / "bin" / "python"
        if candidate.exists():
            return str(candidate)
    return sys.executable


def data_dir() -> Path | None:
    """Root holding ``<file_dir>/scan_results_*_dataframe.pkl`` sweeps."""
    value = os.environ.get("CHERFD_DATA_DIR")
    if value:
        return Path(value).expanduser()
    proj = project_dir()
    return (proj / "data") if proj is not None else None


def logs_dir() -> Path | None:
    value = os.environ.get("CHERFD_LOGS_DIR")
    if value:
        return Path(value).expanduser()
    proj = project_dir()
    return (proj / "logs") if proj is not None else None


# ---------------------------------------------------------------------------
# Where this package writes
# ---------------------------------------------------------------------------

def _bth_data_dir() -> Path:
    """The per-deployment directory (the action log lives there too)."""
    from beamtimehero_cli.config import DATA_DIR
    return Path(DATA_DIR)


def export_dir() -> Path:
    value = os.environ.get("CHERFD_EXPORT_DIR")
    return Path(value).expanduser() if value else _bth_data_dir() / "cherfd_exports"


def mock_state_path() -> Path:
    value = os.environ.get("CHERFD_MOCK_STATE")
    return Path(value).expanduser() if value else _bth_data_dir() / "cherfd_mock_state.json"


def mock_time_scale() -> float:
    """Mock-clock multiplier: 0.01 makes a 30 s mock sweep take 0.3 s."""
    return float(os.environ.get("CHERFD_MOCK_TIME_SCALE") or "1.0")


#: Every environment variable this package reads. Pinned against
#: ``config.example.yaml`` by ``tests/test_config_surface.py``.
ENV_VARS: tuple[str, ...] = (
    "CHERFD_MOCK",
    "CHERFD_SERVER_URL",
    "CHERFD_DAQ_URL",
    "CHERFD_HTTP_TIMEOUT_S",
    "CHERFD_PROJECT_DIR",
    "CHERFD_PYTHON",
    "CHERFD_DATA_DIR",
    "CHERFD_LOGS_DIR",
    "CHERFD_EXPORT_DIR",
    "CHERFD_MOCK_STATE",
    "CHERFD_MOCK_TIME_SCALE",
)
