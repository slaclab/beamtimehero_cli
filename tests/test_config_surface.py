"""Every environment variable this package reads must be documented.

The configuration surface is 30-odd environment variables spread across a
dozen modules, and the README table had drifted to covering roughly half of
them — including the ones without which whole tool trees simply error
(``DB_HOST`` for the psql tools, ``SLACK_BOT_TOKEN`` for the Slack tools).
Undocumented configuration is indistinguishable from a broken tool.

``config.example.yaml`` is the authoritative list. This test keeps it that way,
so the failure lands on whoever adds the variable rather than on whoever tries
to deploy six months later.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "beamtimehero_cli"
EXAMPLE = REPO_ROOT / "config.example.yaml"

# Read from the environment but not ours to document: standard variables we
# merely honour, and the pointer to the config file itself.
NOT_OURS = {"XDG_DATA_HOME", "BEAMTIMEHERO_CONFIG"}

# Read indirectly, via a module-level constant rather than a literal at the
# getenv call site, so the source scan below cannot see them.
INDIRECT = {
    "SSRL_COLLECTOR_DIR",   # spec_data/ssrl_backend.py SSRL_COLLECTOR_DIR_ENV
    "SLAC_API_KEY_PRIMARY",  # spec_logs/error_checker.py _KEY_ENVS
    "SLAC_API_KEY",          # spec_logs/error_checker.py _KEY_ENVS
    "BTH_GASMIXER_URL",      # station_services.py SERVICES
    "BTH_XSP32_GUI_URL",     # station_services.py SERVICES
    "BTH_MOTOR_ANALYZER_URL",  # station_services.py SERVICES
}

_READ = re.compile(
    r"""(?:getenv\(|environ\.get\(|environ\[)\s*["']([A-Z_][A-Z0-9_]*)["']"""
)


def _env_vars_read() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        for m in _READ.finditer(path.read_text()):
            found.setdefault(m.group(1), set()).add(
                str(path.relative_to(SRC))
            )
    for name in INDIRECT:
        found.setdefault(name, {"(indirect)"})
    return {k: v for k, v in found.items() if k not in NOT_OURS}


def _documented() -> set[str]:
    """Keys named in config.example.yaml, commented-out ones included.

    A commented key is still documentation — it says "this exists and here is
    its default" — so it counts.
    """
    text = EXAMPLE.read_text()
    return set(re.findall(r"^\s*(?:#\s*)?([A-Z_][A-Z0-9_]*)\s*:", text, re.M))


@pytest.mark.parametrize("name", sorted(_env_vars_read()))
def test_env_var_is_documented(name):
    assert name in _documented(), (
        f"{name} is read in {sorted(_env_vars_read()[name])} but is not in "
        "config.example.yaml. Add it there, with its default and what it is "
        "for — that file is what the README and the agent-integration refdoc "
        "point people at."
    )


def test_example_yaml_documents_nothing_imaginary():
    """A documented variable nobody reads sends people chasing a no-op."""
    stale = _documented() - set(_env_vars_read()) - NOT_OURS
    assert not stale, (
        f"config.example.yaml documents variables this package never reads: "
        f"{sorted(stale)}. Remove them, or add them to NOT_OURS if they are "
        "standard variables we only honour."
    )


def test_example_yaml_is_loadable_and_shaped_right():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(EXAMPLE.read_text())
    assert isinstance(data, dict) and "env" in data, (
        "config.example.yaml must have a top-level 'env:' mapping — that is "
        "what BEAMTIMEHERO_CONFIG loading reads."
    )
    assert isinstance(data["env"], dict)


def test_yaml_config_is_applied_but_never_overrides_the_environment(
    tmp_path, monkeypatch
):
    """The precedence that makes a checked-in baseline safe."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "env:\n"
        "  BEAMTIMEHERO_TEST_FROM_FILE: from_file\n"
        "  BEAMTIMEHERO_TEST_OVERRIDDEN: from_file\n"
    )
    monkeypatch.setenv("BEAMTIMEHERO_CONFIG", str(cfg))
    monkeypatch.setenv("BEAMTIMEHERO_TEST_OVERRIDDEN", "from_env")
    monkeypatch.delenv("BEAMTIMEHERO_TEST_FROM_FILE", raising=False)

    import os

    from beamtimehero_cli.config import _load_yaml_config

    _load_yaml_config()
    assert os.environ["BEAMTIMEHERO_TEST_FROM_FILE"] == "from_file"
    assert os.environ["BEAMTIMEHERO_TEST_OVERRIDDEN"] == "from_env"


def test_a_broken_config_file_does_not_prevent_startup(tmp_path, monkeypatch):
    """A malformed config file must not make the beamline unreachable."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("env: [this, is, a, list]\n")
    monkeypatch.setenv("BEAMTIMEHERO_CONFIG", str(bad))

    from beamtimehero_cli.config import _load_yaml_config

    _load_yaml_config()  # warns, does not raise

    monkeypatch.setenv("BEAMTIMEHERO_CONFIG", str(tmp_path / "does_not_exist.yaml"))
    _load_yaml_config()
