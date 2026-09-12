"""Declare an agent's CLI surface once; generate everything else from it.

    from beamtimehero_cli.agent_surface import AgentSurface, build_surface

    SURFACE = AgentSurface(
        name="blaligner",
        layout="nested",
        branches=("tool", "spec-read", "spec-write", "spec-file"),
        write_tools={"move_motor", "run_motor_scan"},
        motors={"Sx", "Sy", "Sz"},
        phase="sample_alignment",
    )

    build = build_surface(SURFACE, catalogue)      # at entry point
    branch_subs = build.attach(root_subparsers)    # the parser branch
    build.manifest(), build.prompt_fragment(), build.bash_pattern()

**Import order matters, and this package is built for it.** Only
``spec.py`` is imported eagerly, and it reaches pydantic and
``cli.trees`` (which imports nothing) and nothing else. So

    from beamtimehero_cli.agent_surface import AgentSurface

leaves ``beamtimehero_cli.config`` unimported, which is what lets
``chemcatal`` declare its surface in a module that loads before it has
finished setting the environment variables ``config`` reads at import.
``build_surface``, ``Catalogue``, ``SurfaceBuild`` and ``snapshot_parser``
resolve through the module ``__getattr__`` below — same spelling, same
objects, resolved on first use. ``tests/test_agent_surface.py`` proves
the property in a subprocess; keep it true.

Where things live:

* ``spec.py`` — ``AgentSurface``, ``SurfaceError``, validation.
* ``catalogue.py`` — ``Catalogue``/``ResolvedTool``: a snapshot of the
  registries, taken after registration.
* ``compose.py`` — the pure steps (select, filter, rename) plus the two
  impure ones (build a parser, build an executor).
* ``guard.py`` — the motor allow-list, as an executor hook.
* ``build.py`` — ``build_surface`` and the six artifacts.
* ``snapshot.py`` — the parity helper, and the only argparse-private walk.
* ``schema.py`` — writes ``docs/agent_surface.schema.json``.
"""
from __future__ import annotations

import importlib

from beamtimehero_cli.agent_surface.spec import (
    AgentSurface,
    SurfaceError,
    ToolPath,
    check_against_catalogue,
)

# name -> (submodule, attribute). Resolved on first attribute access and
# cached in this module's globals, so the second lookup is a plain global.
_LAZY: dict[str, tuple[str, str]] = {
    "MANIFEST_VERSION": ("build", "MANIFEST_VERSION"),
    "MARKER_BEGIN": ("build", "MARKER_BEGIN"),
    "MARKER_END": ("build", "MARKER_END"),
    "MOTOR_ARG_KEYS": ("guard", "MOTOR_ARG_KEYS"),
    "Catalogue": ("catalogue", "Catalogue"),
    "ResolvedTool": ("catalogue", "ResolvedTool"),
    "SurfaceBuild": ("build", "SurfaceBuild"),
    "build_surface": ("build", "build_surface"),
    "kebab": ("catalogue", "kebab"),
    "motor_arg_keys_of": ("guard", "motor_arg_keys_of"),
    "motor_guard": ("guard", "motor_guard"),
    "snapshot_parser": ("snapshot", "snapshot_parser"),
    "strip_keys": ("snapshot", "strip_keys"),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _LAZY[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    value = getattr(importlib.import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    "AgentSurface",
    "Catalogue",
    "MANIFEST_VERSION",
    "MARKER_BEGIN",
    "MARKER_END",
    "MOTOR_ARG_KEYS",
    "ResolvedTool",
    "SurfaceBuild",
    "SurfaceError",
    "ToolPath",
    "build_surface",
    "check_against_catalogue",
    "kebab",
    "motor_arg_keys_of",
    "motor_guard",
    "snapshot_parser",
    "strip_keys",
]
