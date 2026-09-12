"""Shim: the parser snapshot now ships in the library.

It started here because the first thing that needed it was a test. But
every consumer migrating onto ``build_surface`` needs the same value for
its own parity test, and a helper under ``tests/`` is not installable —
so it moved to ``beamtimehero_cli.agent_surface.snapshot``, which is the
one place in the package allowed to read argparse privates.

This module stays as the import path Phase 1's tests already use. New
tests should import from the library:

    from beamtimehero_cli.agent_surface.snapshot import snapshot_parser

Importing this now imports ``beamtimehero_cli.agent_surface`` (pydantic
and ``cli.trees``, nothing that reads ``config``). The import-order
subprocess test in ``test_registration_seams.py`` still holds, because by
the time it reaches ``observe()`` it has already imported the library in
whichever order that test is exercising.
"""
from __future__ import annotations

from beamtimehero_cli.agent_surface.snapshot import (
    DISPATCH_DEFAULTS,
    canonical,
    snapshot_parser,
    strip_keys,
)

__all__ = ["DISPATCH_DEFAULTS", "canonical", "snapshot_parser", "strip_keys"]
