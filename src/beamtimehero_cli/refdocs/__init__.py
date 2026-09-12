"""Reference doc registry — bundled markdown shipped with the package.

Public API:
    list_docs() -> list[(name, description)]
    get_doc(name) -> str   # raises KeyError / FileNotFoundError
    register_doc(name, path, description)

Defaults under :mod:`beamtimehero_cli.refdocs.defaults` self-register on
import. Consumers can add their own via ``register_doc``.
"""
from __future__ import annotations

from pathlib import Path

_DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"

_DOCS: dict[str, dict] = {
    "getting-started": {
        "file": _DEFAULTS_DIR / "getting-started.md",
        "description": "Quick overview of the beamtimehero CLI surface.",
    },
    "action-log": {
        "file": _DEFAULTS_DIR / "action-log.md",
        "description": "Action-log schema and how each CLI invocation is recorded.",
    },
    "agent-integration": {
        "file": _DEFAULTS_DIR / "agent-integration.md",
        "description": "How to drive this CLI from an LLM agent: the two integration modes, the schema export, and why it is safe to hand a model.",
    },
    "profiles": {
        "file": _DEFAULTS_DIR / "profiles.md",
        "description": "Agent profiles: curated per-agent CLI surfaces over the master catalog.",
    },
    "agent-surfaces": {
        "file": _DEFAULTS_DIR / "agent-surfaces.md",
        "description": "Declaring a restricted agent surface (`AgentSurface`/`build_surface`) and generating the parser, schemas, executor, permission pattern, prompt and manifest from it.",
    },
    "counter-selection": {
        "file": _DEFAULTS_DIR / "counter-selection.md",
        "description": "Load-bearing convention: multi-scan tools must accept an explicit signal counter and normalization mode; why auto-select + edge-step fails off-XAS (e.g. XRS).",
    },
    "xrs-analysis": {
        "file": _DEFAULTS_DIR / "xrs-analysis.md",
        "description": "X-ray Raman (XRS) analysis: the energy-loss reduction pipeline and interpretation tools on the dedicated `xrs` branch, and why the XAS tools are wrong for XRS by construction.",
    },
}


def register_doc(name: str, path: str | Path, description: str) -> None:
    """Register an additional reference doc.

    Consumers call this at startup to surface project-specific docs through
    the same ``beamtimehero ref`` command.
    """
    _DOCS[name] = {"file": Path(path), "description": description}


def list_docs() -> list[tuple[str, str]]:
    return [(name, info["description"]) for name, info in _DOCS.items()]


def get_doc(name: str) -> str:
    if name not in _DOCS:
        raise KeyError(name)
    return Path(_DOCS[name]["file"]).read_text()


def has_doc(name: str) -> bool:
    return name in _DOCS


def doc_path(name: str) -> Path:
    return Path(_DOCS[name]["file"])
