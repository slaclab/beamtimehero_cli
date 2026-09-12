"""The checked-in generated HTML must match what the generators produce now.

``test_docgen.py`` and ``test_docgen_science.py`` assert on the *return value*
of ``render()``, which proves the generators work but says nothing about the
files in ``docs/``. So the pages could sit stale in the repo indefinitely with a
green suite — and since ``science/README.md`` tells a contributor the index is
"generated from the source tree, so a new function appears by existing", stale
is worse than absent: it is a promise the repo stopped keeping.

These tests close that. They compare bytes, so the failure message has to
carry the remedy — nobody can read a diff of a 100 KB single-line-per-row HTML
page and work out what to do.

``docs/agent_surface.schema.json`` is held to the same rule for the same
reason: a consumer checks a surface manifest into its own repo and a reviewer
reads that schema to know what the fields mean, so a schema that lags the model
is worse than no schema. Note that it is generated from ``AgentSurface`` by
pydantic, so a pydantic release that changes schema output will fail this test
— that is the intended signal, and the fix is to regenerate and commit.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from beamtimehero_cli import docgen, docgen_science
from beamtimehero_cli.agent_surface import schema as surface_schema

REPO_ROOT = Path(__file__).resolve().parent.parent

PAGES = [
    pytest.param(
        "docs/science_index.html", docgen_science.render,
        "python -m beamtimehero_cli.docgen_science",
        id="science_index",
    ),
    pytest.param(
        "docs/tool_catalog.html", docgen.render,
        "python -m beamtimehero_cli.docgen",
        id="tool_catalog",
    ),
    pytest.param(
        "docs/agent_surface.schema.json", surface_schema.render,
        "python -m beamtimehero_cli.agent_surface.schema",
        id="agent_surface_schema",
    ),
]


@pytest.mark.parametrize("rel_path,render,command", PAGES)
def test_generated_page_is_current(rel_path, render, command):
    page = REPO_ROOT / rel_path
    assert page.exists(), f"{rel_path} is missing — run `{command}` from the repo root"

    on_disk = page.read_text()
    fresh = render()
    if on_disk == fresh:
        return

    # Point at the first differing line: cheaper to act on than a byte offset.
    old_lines, new_lines = on_disk.splitlines(), fresh.splitlines()
    where = next(
        (i for i, (a, b) in enumerate(zip(old_lines, new_lines), 1) if a != b),
        min(len(old_lines), len(new_lines)) + 1,
    )
    pytest.fail(
        f"{rel_path} is stale — the source tree has moved on.\n"
        f"First difference at line {where} "
        f"({len(old_lines)} lines on disk, {len(new_lines)} regenerated).\n"
        f"Fix: run `{command}` from the repo root and commit the result."
    )
