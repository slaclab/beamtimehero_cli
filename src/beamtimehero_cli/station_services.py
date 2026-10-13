"""Read-only clients for a station's own web services.

BL4-1 runs small local apps beside SPEC (the BL4-1 directory,
``BL4-1_Web/services.json``): the gas mixer / ion-chamber HV page, the
32-element Ge detector GUI and the Motor Analyzer. Each has a JSON read
API. This module issues HTTP **GET** only — there is no POST, PUT or DELETE
anywhere in it (``tests/test_station_services.py`` checks) — and never
sends anything the caller supplies except a motor name used to filter a
reply locally.

URLs come from ``BTH_GASMIXER_URL`` / ``BTH_XSP32_GUI_URL`` /
``BTH_MOTOR_ANALYZER_URL``, else the station default
(``station.STATION_ENV``; BL4-1 only). A station without the service gets
``ok: false`` saying so, which is also what BL15-2 sees.

Temperatures: neither station has a temperature controller in its SPEC
config. ``BTH_SAMPLE_TEMP_PVS`` (``label=PV,label=PV``) names the EPICS PVs
to read for one (a cryostat or heater controller, when staff add one); they
are read with the read-only ``epics_ca.caget``.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from beamtimehero_cli import station

TIMEOUT_S = 5.0

SERVICES: dict[str, tuple[str, str]] = {
    # name: (env var, what it is)
    "gas_mixer": ("BTH_GASMIXER_URL", "gas mixer / ion-chamber HV page (gasmixer/gas-mixer)"),
    "ge_detector": ("BTH_XSP32_GUI_URL", "32-element Ge detector GUI (xspress_32element)"),
    "motor_analyzer": ("BTH_MOTOR_ANALYZER_URL", "Motor Analyzer (motor_analyzer)"),
}


def service_url(name: str) -> Optional[str]:
    var, _ = SERVICES[name]
    url = os.environ.get(var) or station.default(var)
    return url.rstrip("/") if url else None


def _get(name: str, path: str, params: Optional[dict] = None) -> dict[str, Any]:
    """GET <service><path>; {ok, status, body} or {ok: false, error}."""
    url = service_url(name)
    if not url:
        return {"ok": False, "error": f"{station.station_name()} has no {SERVICES[name][1]} "
                                      f"configured ({SERVICES[name][0]})"}
    import requests
    try:
        r = requests.get(url + path, params=params, timeout=TIMEOUT_S,
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


# ---------------------------------------------------------------------------
# Sample environment
# ---------------------------------------------------------------------------

def temperature_pvs() -> dict[str, str]:
    raw = os.environ.get("BTH_SAMPLE_TEMP_PVS") or station.default("BTH_SAMPLE_TEMP_PVS")
    out: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        label, _, pv = item.partition("=")
        out[label.strip() if pv else item] = (pv or label).strip()
    return out


def sample_environment() -> dict[str, Any]:
    """Gas mixer state plus any configured temperature PVs. Read-only."""
    gas = _get("gas_mixer", "/api/status")
    out: dict[str, Any] = {"station": station.station_name(), "gas_mixer": gas}
    if gas.get("ok"):
        body = gas.pop("body") or {}
        # gasmixer/status.build(): mixer, ion_chambers, hv, page_rules, pvs
        for key in ("mode", "mixer", "ion_chambers", "hv", "page_rules", "pvs"):
            gas[key] = body.get(key)
    pvs = temperature_pvs()
    if not pvs:
        out["temperatures"] = {"ok": False, "error": "no temperature PVs configured "
                               "(BTH_SAMPLE_TEMP_PVS); this station has no temperature "
                               "controller in its SPEC config"}
    else:
        from beamtimehero_cli import epics_ca
        res = epics_ca.caget(list(pvs.values()))
        by_pv = {r["pv"]: r for r in res.get("results", [])}
        out["temperatures"] = {
            "ok": res.get("ok", False),
            "readings": [{"label": label, **by_pv.get(pv, {"pv": pv, "connected": False})}
                         for label, pv in pvs.items()],
        }
    out["ok"] = bool(gas.get("ok") or out["temperatures"].get("ok"))
    return out


# ---------------------------------------------------------------------------
# Ge detector
# ---------------------------------------------------------------------------

def ge_detector_status() -> dict[str, Any]:
    """The xsp32 GUI's /api/state: ROIs, groups, dead elements, rates."""
    res = _get("ge_detector", "/api/state")
    if res.get("ok"):
        res["state"] = res.pop("body")
    return res


# ---------------------------------------------------------------------------
# Motor diagnostics
# ---------------------------------------------------------------------------

def motor_diagnostics(motor: Optional[str] = None, runs: int = 5) -> dict[str, Any]:
    """Motor Analyzer: a motor's configuration (from the SPEC config) and its
    most recent diagnostic runs. Never starts a run."""
    cfg = _get("motor_analyzer", "/api/motors")
    if not cfg.get("ok"):
        return cfg
    motors = (cfg.pop("body") or {}).get("motors") or []
    if motor:
        motors = [m for m in motors if m.get("mne") == motor]
        if not motors:
            return {"ok": False, "error": f"motor {motor!r} is not in the SPEC config"}
    out: dict[str, Any] = {"ok": True, "motors": motors}
    hist = _get("motor_analyzer", "/api/runs")
    if hist.get("ok"):
        rows = (hist["body"] or {}).get("runs") or []
        if motor:
            rows = [r for r in rows if motor in (r.get("motor"), r.get("mne"))
                    or motor in str(r.get("id") or r.get("run_id") or "")]
        out["recent_runs"] = rows[: max(0, int(runs))]
    else:
        out["recent_runs_error"] = hist.get("error")
    return out
