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
