"""capture_sample_image: the camera is per station; BL4-1 has none yet."""
from __future__ import annotations

import json

import pytest

from beamtimehero_cli import config
from beamtimehero_cli.tool_catalog import tools_core


class _Resp:
    content = b"\xff\xd8fakejpeg\xff\xd9"
    status_code = 200

    def raise_for_status(self):
        pass


@pytest.fixture
def cam(monkeypatch):
    calls = []
    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append((url, kw)) or _Resp())
    monkeypatch.setattr(config, "SPEC_MOCK", False)
    monkeypatch.delenv("SAMPLE_CAM_HOST", raising=False)
    return calls


def _run(**args):
    text, imgs = tools_core.t_capture_sample_image(args)
    return json.loads(text), imgs


def test_bl41_has_no_camera_until_one_is_configured(cam, monkeypatch):
    monkeypatch.setenv("BTH_STATION", "bl41")
    out, imgs = _run()
    assert not out["ok"] and "not installed" in out["error"] and imgs == []
    assert cam == []                         # never reached the BL15-2 Pi
    monkeypatch.setenv("SAMPLE_CAM_HOST", "192.168.41.77")
    out, imgs = _run(resolution="full")
    assert out["ok"] and out["resolution"] == "full" and len(imgs) == 1
    url, kw = cam[0]
    assert url == "http://192.168.41.77:8080/snapshot.jpg"
    assert kw["params"]["resolution"] == "full" and kw["timeout"] == 30


def test_bl15_default_is_unchanged(cam, monkeypatch):
    monkeypatch.delenv("BTH_STATION", raising=False)
    out, _ = _run()
    assert out["ok"] and cam[0][0] == "http://192.168.150.93:8080/snapshot.jpg"
    assert cam[0][1]["params"]["resolution"] == "low"


def test_bad_resolution(cam, monkeypatch):
    monkeypatch.setenv("SAMPLE_CAM_HOST", "127.0.0.1")
    out, _ = _run(resolution="huge")
    assert not out["ok"] and cam == []
