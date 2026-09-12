"""Write the ``AgentSurface`` JSON schema to ``docs/``.

    python -m beamtimehero_cli.agent_surface.schema

A surface spec is checked into the consumer's repo and its dump is
checked into a manifest, so the shape has to be documented somewhere a
reviewer can look without reading pydantic. The schema is generated
rather than written, and ``tests/test_docs_fresh.py`` fails if the file
on disk has drifted — the same contract the two generated HTML pages are
held to.

Kept in its own module so the generator is importable without the
package's ``__init__`` resolving anything lazily, and so ``-o`` exists
for anyone who wants the schema somewhere else.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from beamtimehero_cli.agent_surface.spec import AgentSurface

DEFAULT_OUTPUT = "docs/agent_surface.schema.json"


def render() -> str:
    """The schema as the bytes that belong on disk.

    ``sort_keys`` because the file is diffed by humans: a reordering that
    pydantic is free to make between releases should not read as a
    change to the surface format.
    """
    schema = AgentSurface.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m beamtimehero_cli.agent_surface.schema",
        description="Write the AgentSurface JSON schema.",
    )
    parser.add_argument(
        "-o", "--output", default=DEFAULT_OUTPUT,
        help=f"Output path (default: {DEFAULT_OUTPUT}, relative to cwd).",
    )
    args = parser.parse_args(argv)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = render()
    out.write_text(text)
    print(f"wrote {out} — {len(json.loads(text)['properties'])} surface fields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
