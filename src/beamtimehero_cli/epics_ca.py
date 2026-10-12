"""Read-only EPICS Channel Access client: ``caget`` and nothing else.

Backs the ``caget`` tool. It shells out to the EPICS base ``caget`` binary
rather than linking pyepics, which is what makes the read-only claim
checkable: the only executable this module can run is one named ``caget``,
resolved once, invoked with an argument list (never a shell), and there is
no code path that builds a ``caput``/``camonitor``/``cainfo`` command. A
write would need a different binary, and this module cannot name one.
``tests/test_epics_ca.py`` asserts that on the source as well as the
behaviour.

Which IOCs are reachable is the host's decision, not this package's. The
CA search list comes from the host's EPICS environment:

* ``EPICS_CA_ADDR_LIST`` / ``EPICS_CA_AUTO_ADDR_LIST`` (and the other
  ``EPICS_CA_*`` variables) from the process environment, when set — a
  login shell gets them from ``/etc/profile.d/epics.sh``;
* otherwise the ``export EPICS_*=...`` lines of that same profile file,
  so a service started without a login shell (systemd, cron) still
  searches exactly where an operator's terminal would.

Nothing here lets a caller widen that list: the agent supplies PV names
and a timeout, nothing that reaches the environment or the command line
as an option.

Like the other clients in this package, nothing in this module raises for
an operational failure — a missing binary, a PV that does not connect, a
timeout — they come back as fields on the result so the handler can hand
them straight to the agent.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

#: The host's EPICS login profile, read when the process environment has no
#: CA address list of its own.
HOST_EPICS_PROFILE = Path("/etc/profile.d/epics.sh")

#: The one executable this module ever runs.
_BINARY = "caget"

MAX_PVS = 50
DEFAULT_TIMEOUT_S = 1.0
MAX_TIMEOUT_S = 30.0
DEFAULT_MAX_ELEMENTS = 100
_MAX_WORKERS = 8

# EPICS record names are letters, digits and ``_ - : . [ ] < > ;``; a field
# suffix adds ``.FIELD``. A leading ``-`` is refused separately below so a
# "PV" can never be read by caget as an option.
_PV_RE = re.compile(r"^[A-Za-z0-9_:.\-\[\]<>;{}$]{1,128}$")

# ``export NAME=value`` / ``export NAME="value"`` in the host profile.
_EXPORT_RE = re.compile(
    r"""^\s*export\s+(EPICS_[A-Z0-9_]+)=(?:"([^"$`]*)"|'([^']*)'|([^\s"'$`;]*))\s*$""",
    re.M,
)

# ``caget -a`` line: name, timestamp, value, then STAT SEVR only when the
# record is in alarm (both are blank on NO_ALARM).
_WIDE_RE = re.compile(
    r"^(\S+)\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)\s(.*)$"
)
_ALARM_TAIL_RE = re.compile(r"\s+([A-Z_]+)\s+(MINOR|MAJOR|INVALID)\s*$")


def _host_profile_env(path: Path = HOST_EPICS_PROFILE) -> dict[str, str]:
    """The ``EPICS_*`` exports of the host profile, or ``{}``."""
    try:
        text = path.read_text()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for m in _EXPORT_RE.finditer(text):
        out[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
    return out


def ca_environment(profile: Path = HOST_EPICS_PROFILE) -> dict[str, str]:
    """The environment ``caget`` runs with.

    The process environment, with the host profile's ``EPICS_*`` values
    filled in only for names the process environment does not set. The
    profile never overrides a value already present, so an operator who
    exported their own list gets that list.
    """
    env = dict(os.environ)
    if not env.get("EPICS_CA_ADDR_LIST"):
        for key, value in _host_profile_env(profile).items():
            env.setdefault(key, value)
    return env


def find_caget(env: dict[str, str]) -> str | None:
    """Absolute path of the ``caget`` binary, or ``None``.

    ``PATH`` first, then ``$EPICS_BASE/bin/$EPICS_HOST_ARCH`` for a
    process that never had the profile's ``PATH`` edit.
    """
    found = shutil.which(_BINARY, path=env.get("PATH"))
    if found:
        return found
    base, arch = env.get("EPICS_BASE"), env.get("EPICS_HOST_ARCH")
    if base and arch:
        return shutil.which(_BINARY, path=str(Path(base) / "bin" / arch))
    return None


def validate_pv(pv: str) -> str | None:
    """A reason the name is refused, or ``None`` if it is acceptable."""
    if not isinstance(pv, str) or not pv:
        return "PV name must be a non-empty string"
    if pv.startswith("-"):
        return "PV name may not start with '-'"
    if not _PV_RE.match(pv):
        return "PV name contains characters EPICS record names do not use"
    return None


def _parse_wide(pv: str, stdout: str) -> dict:
    line = next((ln for ln in stdout.splitlines() if ln.strip()), "")
    m = _WIDE_RE.match(line)
    if not m:
        return {"pv": pv, "connected": True, "value": line.strip(), "raw": line}
    _name, ts, rest = m.groups()
    status = severity = None
    alarm = _ALARM_TAIL_RE.search(rest)
    if alarm:
        status, severity = alarm.groups()
        rest = rest[: alarm.start()]
    # ``-#`` makes caget lead every value with its element count, scalars
    # included: "1 495.2", "3 1 2 3".
    count_s, _, body = rest.strip().partition(" ")
    value: str | list[str] = body.strip()
    out = {"pv": pv, "connected": True}
    if count_s.isdigit() and int(count_s) != 1:
        value = body.split()
        out["count"] = int(count_s)
    elif not count_s.isdigit():
        value = rest.strip()
    return {
        **out,
        "value": value,
        "timestamp": ts,
        "status": status or "NO_ALARM",
        "severity": severity or "NO_ALARM",
    }


def _get_one(binary: str, env: dict[str, str], pv: str,
             timeout_s: float, max_elements: int) -> dict:
    # One process per PV: given several names, caget prints nothing at all
    # for the ones that connected if any one of them did not.
    argv = [binary, "-w", f"{timeout_s:g}", "-a", "-#", str(max_elements), "--", pv]
    try:
        proc = subprocess.run(
            argv, env=env, capture_output=True, text=True,
            timeout=timeout_s + 5, stdin=subprocess.DEVNULL, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"pv": pv, "connected": False, "error": "caget did not return"}
    except OSError as exc:
        return {"pv": pv, "connected": False, "error": f"could not run caget: {exc}"}
    if proc.returncode != 0 or not proc.stdout.strip():
        msg = (proc.stderr or proc.stdout).strip() or f"caget exited {proc.returncode}"
        return {"pv": pv, "connected": False, "error": msg}
    return _parse_wide(pv, proc.stdout)


def caget(pvs: list[str], *, timeout_s: float = DEFAULT_TIMEOUT_S,
          max_elements: int = DEFAULT_MAX_ELEMENTS) -> dict:
    """Read each PV once. Never writes, never raises for a CA failure.

    Returns ``{ok, results, ca_addr_list, ca_auto_addr_list}``; ``ok`` is
    true when every PV was read. Each result carries ``value`` (the string
    caget printed for a scalar; for an array, a list of up to
    ``max_elements`` element strings plus ``count``), ``timestamp``,
    ``status`` and ``severity``, or ``connected: false``
    and an ``error``. Raises ``ValueError`` only for a malformed request.
    """
    if isinstance(pvs, str):
        pvs = [pvs]
    pvs = list(dict.fromkeys(pvs or []))
    if not pvs:
        raise ValueError("at least one PV name is required")
    if len(pvs) > MAX_PVS:
        raise ValueError(f"at most {MAX_PVS} PVs per call (got {len(pvs)})")
    bad = {pv: why for pv in pvs if (why := validate_pv(pv))}
    if bad:
        raise ValueError("; ".join(f"{pv!r}: {why}" for pv, why in bad.items()))
    timeout_s = float(timeout_s)
    if not 0 < timeout_s <= MAX_TIMEOUT_S:
        raise ValueError(f"timeout_s must be in (0, {MAX_TIMEOUT_S:g}]")
    max_elements = int(max_elements)
    if max_elements < 1:
        raise ValueError("max_elements must be at least 1")

    env = ca_environment()
    summary = {
        "ca_addr_list": env.get("EPICS_CA_ADDR_LIST"),
        "ca_auto_addr_list": env.get("EPICS_CA_AUTO_ADDR_LIST"),
    }
    binary = find_caget(env)
    if binary is None:
        return {"ok": False, "error": "caget not found on PATH or under "
                "$EPICS_BASE/bin/$EPICS_HOST_ARCH; is EPICS base installed "
                "on this host?", "results": [], **summary}

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(pvs))) as pool:
        results = list(pool.map(
            lambda pv: _get_one(binary, env, pv, timeout_s, max_elements), pvs,
        ))
    return {"ok": all(r["connected"] for r in results), "results": results, **summary}


__all__ = ["caget", "ca_environment", "find_caget", "validate_pv", "MAX_PVS"]
