"""Client for the station's SRS current-amplifier server (ssrl-current-amp).

BL4-1 runs ``ssrl-current-amp`` (``/usr/local/projects/ssrl-current-amp``):
a web UI and EPICS IOC for the SR570 amplifiers on I0/I1/I2/PIPS. Its
**Zero Offsets** routine (``devices/autozero.py`` there) sets each detector's
input offset current to the smallest 1-2-5 ladder step whose cscan box ADC
reading clears a small positive margin (BL4-1: +2 mV). The server owns that
routine and all of its guards (at most 8 offset changes per detector, the
probe must respond 0.5-2x the nominal gain, readings must follow the
prediction, a 300 s limit for the run, every offset restored on an error or
abort). This module only starts it, waits for it and reports the result:

  POST /api/autozero/start   409 if a run is already going
  GET  /api/autozero         {enabled, status: {state, step, log, results}}
  POST /api/autozero/abort   on our own deadline: the server restores offsets

``zero_offsets`` writes hardware, so the tool handler wraps it in
``audited_call.audited_device_call`` (justification, safety switch, action
log). The URL is ``BTH_CURRENT_AMP_URL``, else the station default; a
station without the server gets ``ok: false`` saying so.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

from beamtimehero_cli import station

URL_VAR = "BTH_CURRENT_AMP_URL"
TIMEOUT_S = 5.0          # per HTTP request
POLL_S = 1.0
#: The server stops a run after its own max_run_s (300 s at BL4-1); wait a
#: little longer so its limit, not ours, normally ends a stuck run.
DEFAULT_WAIT_S = 360.0
MAX_WAIT_S = 900.0


def server_url() -> Optional[str]:
    url = os.environ.get(URL_VAR) or station.default(URL_VAR)
    return url.rstrip("/") if url else None


def _request(method: str, path: str) -> dict[str, Any]:
    """{ok, status, body} or {ok: false, error}."""
    url = server_url()
    if not url:
        return {"ok": False, "error": f"{station.station_name()} has no current-amp "
                                      f"server configured ({URL_VAR})"}
    import requests
    try:
        r = requests.request(method, url + path, timeout=TIMEOUT_S,
                             proxies={"http": None, "https": None})
    except requests.RequestException as e:
        return {"ok": False, "url": url + path, "error": f"{type(e).__name__}: {e}"}
    try:
        body = r.json()
    except ValueError:
        body = {"text": r.text[:500]}
    if r.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        return {"ok": False, "url": url + path, "status": r.status_code,
                "error": detail or f"HTTP {r.status_code}"}
    return {"ok": True, "url": url + path, "status": r.status_code, "body": body}


def autozero_status() -> dict[str, Any]:
    """The server's last (or current) zeroing run. Read-only."""
    res = _request("GET", "/api/autozero")
    if not res.get("ok"):
        return res
    body = res["body"] or {}
    return {"ok": True, "enabled": bool(body.get("enabled")),
            "status": body.get("status")}


def _summary(status: dict) -> dict[str, Any]:
    started, finished = status.get("started"), status.get("finished")
    return {
        "state": status.get("state"),
        "results": status.get("results") or {},
        "log": [e.get("msg") for e in status.get("log") or []],
        "duration_s": (round(finished - started, 1)
                       if started and finished else None),
    }


def zero_offsets(wait_s: float = DEFAULT_WAIT_S,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Start the server's offset zeroing and wait for it to finish.

    ``ok`` is true only when the run ended ``done``; per-detector outcomes
    (zeroed / skipped / failed / restored) are in ``results``. If the run
    is still going after ``wait_s`` it is aborted, which makes the server
    restore every original offset.
    """
    wait_s = max(1.0, min(float(wait_s), MAX_WAIT_S))
    pre = autozero_status()
    if not pre.get("ok"):
        return pre
    if not pre["enabled"]:
        return {"ok": False, "error": "offset zeroing is not enabled on the "
                                      "current-amp server (config/autozero.json)"}
    start = _request("POST", "/api/autozero/start")
    if not start.get("ok"):
        return start
    deadline = clock() + wait_s
    status: dict = {}
    while True:
        sleep(POLL_S)
        cur = autozero_status()
        if cur.get("ok"):
            status = cur.get("status") or {}
            if status.get("state") != "running":
                break
        if clock() >= deadline:
            _request("POST", "/api/autozero/abort")
            return {"ok": False,
                    "error": f"zeroing still running after {wait_s:g} s; aborted "
                             "(the server restores the original offsets)",
                    "last_status": _summary(status) if status else cur}
    out = _summary(status)
    out["ok"] = out["state"] == "done"
    if not out["ok"]:
        out["error"] = (f"zeroing ended {out['state']!r}; the server restored the "
                        "original offsets (see log)")
    return out
