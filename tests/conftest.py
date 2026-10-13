"""Suite-wide isolation: no test writes the checkout or reaches the network.

Runs before any test module imports ``beamtimehero_cli`` (pytest loads the
root conftest first), so ``config.DATA_DIR`` / ``DB_PATH`` resolve into a
per-session temp directory instead of ``<repo>/data``:

* ``BEAMTIMEHERO_DATA_DIR`` and ``BEAMLINE_TOOLS_DB_PATH`` -> the temp dir,
  so the action log, query log, cherfd mock state, camera log and safety
  switches of a test run never land in the checkout;
* ``BL_SCAN_DIR`` -> the temp dir unless a test sets its own (the energy
  calibration store writes there);
* SPEC_MOCK=1 and CHERFD_MOCK=1, whatever the shell exported;
* EPICS CA for child processes -> loopback only, no broadcast.

The socket guard refuses any ``connect`` to a non-loopback address for the
whole session; loopback and AF_UNIX stay usable for the in-process mocks and
the fake servers some tests start. A test that needs to prove a refusal can
still monkeypatch ``socket.socket.connect`` itself.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import tempfile

_TMP = tempfile.mkdtemp(prefix="bth-tests-")
os.environ["BEAMTIMEHERO_DATA_DIR"] = _TMP
os.environ["BEAMLINE_TOOLS_DB_PATH"] = os.path.join(_TMP, "beamline_tools.db")
os.environ.setdefault("BL_SCAN_DIR", os.path.join(_TMP, "scans"))
os.makedirs(os.environ["BL_SCAN_DIR"], exist_ok=True)
# SPEC and the CHERFD server are always the in-memory mocks here
os.environ["SPEC_MOCK"] = "1"
os.environ["CHERFD_MOCK"] = "1"
os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
os.environ["EPICS_CA_ADDR_LIST"] = "127.0.0.1:1"

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _loopback(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True                      # AF_UNIX path, abstract socket
    host = address[0]
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False                     # a hostname: would need DNS + network


def _guarded(real):
    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address):
            raise ConnectionRefusedError(f"tests/conftest.py: network access to {address!r} is blocked")
        return real(self, address)
    return connect


socket.socket.connect = _guarded(_real_connect)
socket.socket.connect_ex = _guarded(_real_connect_ex)


def pytest_report_header(config):
    return f"beamtimehero_cli tests: data dir {_TMP}; non-loopback network blocked"
