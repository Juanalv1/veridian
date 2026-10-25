"""Classify every route of an OpenAPI document as critical / sensitive / normal.

Usage (from engine/):
    python scripts/classify_openapi.py path/to/openapi.yaml [--out routes.generated.yaml]

Output is a plain YAML mapping `"METHOD /path": class`, with the model's
reason as a trailing comment. Needs ANTHROPIC_API_KEY; model from
VERIDIAN_MODEL. Without a key every route comes out `normal` (fallback).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veridian_engine.openapi import RouteClass, classify, extract_operations, load_spec  # noqa: E402


def render(classes: dict[str, RouteClass]) -> str:
    lines = []
    for route, rc in classes.items():
        # json.dumps gives a valid double-quoted YAML scalar for the key.
        reason = " ".join(rc.reason.split())
        lines.append(f"{json.dumps(route)}: {rc.route_class}" + (f"  # {reason}" if reason else ""))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", help="OpenAPI 3 document (JSON or YAML)")
    parser.add_argument("--out", help="write the mapping here instead of stdout")
    args = parser.parse_args(argv)

    operations = extract_operations(load_spec(args.spec))
    output = render(classify(operations))
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
