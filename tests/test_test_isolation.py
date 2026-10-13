"""The suite's own isolation (tests/conftest.py) holds."""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

from beamtimehero_cli import config

REPO = Path(__file__).resolve().parent.parent


def test_data_dir_is_not_the_checkout():
    assert not Path(config.DATA_DIR).resolve().is_relative_to(REPO)
    assert not Path(config.DB_PATH).resolve().is_relative_to(REPO)


def test_non_loopback_connect_is_blocked():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ConnectionRefusedError, match="blocked"):
            s.connect(("192.168.41.6", 8032))
    finally:
        s.close()


def test_loopback_still_works():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        c.connect(srv.getsockname())
    finally:
        c.close()
        srv.close()
