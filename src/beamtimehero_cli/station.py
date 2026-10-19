"""The deployment's station: one setting that selects every station default.

``BTH_STATION`` names the endstation this deployment runs at (``bl15`` =
BL15-2, the default, or ``bl41`` = BL4-1). It decides two things:

* the defaults of the ``BTH_*`` station variables below (beam-status gap
  requirement, count-time cap, energy-scan prefixes, counter priority, plot
  columns, beam-status PVs). An explicitly exported variable still wins, so a
  single value can be overridden without restating the station;
* the CHERFD station profile (``science/cherfd/policy.py STATION_PROFILES``):
  mono crystals and step size, the scan detector and its signal, whether
  there is an undulator, the cscan_daq app and the per-sweep overhead.

It also names the scan modes the station collects with (``SCAN_MODES``:
continuous by default at BL4-1, step-only at BL15-2). That is beamline
policy, not a default, so no variable overrides it.

A deployment sets ``BTH_STATION`` and nothing else station-specific. An
unknown name raises rather than falling back to BL15-2, so a typo cannot run
one beamline with another's settings.

Read at call time, except where a module caches a value at import (as
``config.EPICS_PV_*`` and ``spec_cmd``'s station hooks already did).
"""
from __future__ import annotations

import os

DEFAULT_STATION = "bl15"

#: Station -> defaults for the BTH_* station variables. BL15-2's are the
#: built-in defaults at each read site, so its entry is empty.
STATION_ENV: dict[str, dict[str, str]] = {
    "bl15": {},
    "bl41": {
        # wiggler beamline: no ID gap to own; the beam macro prints
        # spear_current and bl_state only
        "BTH_BEAM_STATUS_REQUIRES_GAP": "0",
        # the cscan box single-shot window (SPEC csct caps at 67 s)
        "BTH_MAX_COUNT_TIME_S": "67",
        "BTH_ENERGY_SCAN_PREFIXES": "gscan mono,kscan",
        # 24-element Ge detector sums
        "BTH_ACTIVE_COUNTER_PRIORITY": "xsum,xsA,xsB,I1",
        "BTH_DEFAULT_Y_COLUMNS": "xsum,I1,I0",
        "BTH_EPICS_PV_BL_STATE": "BL04:State",
        # 41_spec/logfile.mac writes log_<USER>_<MM-DD-YYYY> (log_fourone_*);
        # BL15-2's built-in log__* matches none of them
        "BTH_SPEC_LOG_FILE_PATTERN": "log_*",
        # spec-eval sandbox: :5006 is the gas mixer at BL4-1
        "SPEC_EVAL_URL": "http://127.0.0.1:5016",
        # local read-only web services (BL4-1_Web/services.json)
        "BTH_GASMIXER_URL": "http://127.0.0.1:5006",
        "BTH_XSP32_GUI_URL": "http://192.168.41.6:8032",
        "BTH_MOTOR_ANALYZER_URL": "http://127.0.0.1:5011",
        # where the station's apps write their logs (applogs.py)
        "BTH_APP_LOG_ROOTS": (
            "fourone=/usr/local/projects/autonomous_fourone/logs,"
            "microagents=/usr/local/projects/autonomous_microagents/logs,"
            "cherfd=/usr/local/projects/cherfd_claude/logs,"
            "cscan_acq=/usr/local/projects/cscan_acq/logs,"
            "xsp32=/usr/local/projects/xspress_32element/logs,"
            "motor_analyzer=/usr/local/projects/motor_analyzer/logs,"
            "current_amps=/usr/local/projects/ssrl-current-amp/logs"
        ),
    },
}

STATIONS: tuple[str, ...] = tuple(STATION_ENV)


def station_name() -> str:
    """The configured station (``BTH_STATION``, default bl15). Raises on an unknown name."""
    name = (os.environ.get("BTH_STATION") or DEFAULT_STATION).strip().lower()
    if name not in STATION_ENV:
        raise ValueError(f"BTH_STATION={name!r} is not a known station ({', '.join(STATIONS)})")
    return name


def default(var: str, fallback: str = "") -> str:
    """The station's default for a BTH_* variable, else ``fallback``.

    Read sites use ``os.environ.get(VAR) or station.default(VAR, builtin)``,
    so an exported value always wins.
    """
    return STATION_ENV[station_name()].get(var, fallback)


#: Tool trees a station's CLI leaves out entirely: BL4-1 has no X-ray Raman
#: spectrometer (xrs) and does not read BL6-2a tender data.
HIDDEN_TREES: dict[str, tuple[str, ...]] = {
    "bl15": (),
    "bl41": ("xrs", "tender"),
}


def hidden_trees(name: str | None = None) -> tuple[str, ...]:
    return HIDDEN_TREES.get(name or station_name(), ())


#: Scan modes each station collects with, the default first. ``step`` is a
#: SPEC step scan (gscan / kscan); ``continuous`` is a CHERFD sweep on the
#: cscan box. BL4-1 collects continuously by default and keeps step scans;
#: BL15-2 is step-only. Policy, not a deployment setting: no BTH_* variable
#: overrides it.
SCAN_MODES: dict[str, tuple[str, ...]] = {
    "bl15": ("step",),
    "bl41": ("continuous", "step"),
}


#: Fast-shutter commands each station's SPEC accepts. BL4-1 has no fast
#: shutter, so its SPEC refuses ``fson`` (auto open/close around each count);
#: the shutter is held open with ``fsopen; fsoff`` instead. BL15-2 keeps all
#: four. Policy, not a deployment setting: no BTH_* variable overrides it.
SHUTTER_COMMANDS: dict[str, tuple[str, ...]] = {
    "bl15": ("fsopen", "fsclose", "fson", "fsoff"),
    "bl41": ("fsopen", "fsclose", "fsoff"),
}


def shutter_commands(name: str | None = None) -> tuple[str, ...]:
    """The fast-shutter commands ``name`` (default: this deployment's station) allows."""
    return SHUTTER_COMMANDS[name or station_name()]


def scan_modes(name: str | None = None) -> tuple[str, ...]:
    """The scan modes ``name`` (default: this deployment's station) allows."""
    return SCAN_MODES[name or station_name()]


def default_scan_mode(name: str | None = None) -> str:
    """The station's default scan mode."""
    return scan_modes(name)[0]


def check_scan_mode(mode: str, name: str | None = None) -> str:
    """``mode`` if the station allows it, else ValueError naming the allowed ones."""
    allowed = scan_modes(name)
    m = (mode or "").strip().lower()
    if m not in allowed:
        raise ValueError(f"scan mode {mode!r} is not used at {name or station_name()} "
                         f"(allowed: {', '.join(allowed)})")
    return m
