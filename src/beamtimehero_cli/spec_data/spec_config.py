"""Read SPEC motor and counter configuration from the config file."""

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SPEC_CONFIG_PATH = Path("/usr/local/lib/spec.d/spec/config")

# ``MOT007 = EPICS_M2:8/8  -89  1  800  50  -10  250  0 0x003  Sr  Sr``
#
# Eleven whitespace-separated fields after the ``=``, per the header SPEC
# writes above the block: controller, steps, sign, slew, base, back, accl,
# nada, flags, mnemonic, name. The mnemonic is what every tool means by
# "motor" (it is what ``umv`` takes), so it is field ten, i.e. the
# second-to-last. ``MOTPAR:`` lines also begin with ``MOT`` and are not
# motors, which is why this anchors on the digits and the ``=``.
_MOT_LINE = re.compile(r"^MOT\d+\s*=\s*(?P<fields>.+)$")
_MOT_FIELD_COUNT = 11
_MOT_MNEMONIC_INDEX = -2


def _read_config() -> str:
    """Read the SPEC config file."""
    if not SPEC_CONFIG_PATH.is_file():
        raise FileNotFoundError(f"SPEC config not found: {SPEC_CONFIG_PATH}")
    return SPEC_CONFIG_PATH.read_text()


def get_motor_config() -> str:
    """Extract the motor configuration section from the SPEC config file.

    Returns the header and all MOTnnn lines.
    """
    text = _read_config()
    lines = text.splitlines()

    collecting = False
    result = []
    for line in lines:
        if line.startswith("# Motor"):
            collecting = True
            result.append(line)
            continue
        if collecting:
            if line.startswith("# Counter"):
                break
            if line.startswith("MOT") or line.strip() == "":
                result.append(line)

    if not result:
        return "No motor configuration found in SPEC config."
    return "\n".join(result)


def get_counter_config() -> str:
    """Extract the counter configuration section from the SPEC config file.

    Returns the header and all CNTnnn lines.
    """
    text = _read_config()
    lines = text.splitlines()

    collecting = False
    result = []
    for line in lines:
        if line.startswith("# Counter"):
            collecting = True
            result.append(line)
            continue
        if collecting:
            if line.startswith("#"):
                break
            if line.startswith("CNT") or line.strip() == "":
                result.append(line)

    if not result:
        return "No counter configuration found in SPEC config."
    return "\n".join(result)


def parse_known_motors(text: str) -> "frozenset":
    """Motor mnemonics from the text of a SPEC config file.

    Split out from :func:`known_motors` so the grammar can be tested
    against a captured excerpt (``tests/data/spec_config_motors.txt``)
    on a machine that has no SPEC installation — which is every machine
    except the beamline host.
    """
    out = set()
    for line in text.splitlines():
        match = _MOT_LINE.match(line)
        if not match:
            continue
        fields = match.group("fields").split()
        if len(fields) != _MOT_FIELD_COUNT:
            logger.warning(
                "unexpected MOT line shape (%d fields, expected %d): %r",
                len(fields), _MOT_FIELD_COUNT, line,
            )
            continue
        out.add(fields[_MOT_MNEMONIC_INDEX])
    return frozenset(out)


def known_motors() -> "frozenset | None":
    """Every motor mnemonic this station has, or ``None`` if unknowable.

    ``None`` is the normal answer away from the beamline: the SPEC config
    lives at ``/usr/local/lib/spec.d/spec/config`` on the beamline host
    and nowhere else, and there is no other enumerable motor list in this
    library. ``get_motor_config()`` returns the raw text of that same
    file, which is documentation, not a set.

    The distinction between ``None`` and an empty set is load-bearing:
    ``agent_surface`` skips motor validation on ``None`` and records
    ``motors_validated: false`` in the manifest, whereas an empty set
    would fail every surface that declares a motor. Nothing in the
    library calls this — a consumer opts in with
    ``Catalogue.default(known_motors=spec_config.known_motors())``.
    """
    try:
        text = _read_config()
    except (FileNotFoundError, OSError) as e:
        logger.debug("no SPEC config to read motors from: %s", e)
        return None
    motors = parse_known_motors(text)
    return motors or None


def mock_motors() -> "frozenset":
    """The motors the SPEC mock answers for.

    The off-beamline counterpart of :func:`known_motors`, for a consumer
    that wants its surface specs validated in CI. It is the mock's own
    motor set unioned with any ``SPEC_MOCK_MOTORS`` override, so a
    station that replaces the set wholesale still validates — and the
    BL15-2 defaults, which are what the library's own tests assume, stay
    in the set either way.

    Not a substitute for :func:`known_motors`: the mock's 22 names are a
    plausible BL15-2 subset, not the station's config. A surface
    validated only against this can still name a motor the beamline does
    not have.
    """
    import json

    from beamtimehero_cli.spec_control.transport import _mock_default_positions

    names = set(_mock_default_positions())
    raw = os.environ.get("SPEC_MOCK_MOTORS", "").strip()
    if raw:
        try:
            override = json.loads(raw)
        except (ValueError, TypeError):
            override = None
        if isinstance(override, dict):
            names |= {str(k) for k in override}
    return frozenset(names)
