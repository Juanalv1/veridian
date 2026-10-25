"""Deterministic extraction of operations from an OpenAPI 3 document. No AI here."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Only these keys of a Path Item Object are operations; everything else
# (`parameters`, `summary`, `servers`, `$ref`, `x-*`) is ignored.
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    summary: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    operation_id: str = ""

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def extract_operations(spec: dict) -> list[Operation]:
    """Return every `METHOD /path-template` operation in document order."""
    paths = spec.get("paths") if isinstance(spec, dict) else None
    if not isinstance(paths, dict):
        return []
    operations: list[Operation] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        # Document order: paths as written, methods as written within each path.
        for method, raw in item.items():
            if str(method).lower() not in HTTP_METHODS or not isinstance(raw, dict):
                continue
            tags = raw.get("tags")
            operations.append(
                Operation(
                    method=str(method).upper(),
                    path=str(path),
                    summary=_text(raw.get("summary")),
                    description=_text(raw.get("description")),
                    tags=[str(t) for t in tags] if isinstance(tags, list) else [],
                    operation_id=_text(raw.get("operationId")),
                )
            )
    return operations


def load_spec(path: str | Path) -> dict:
    """Load an OpenAPI document from a JSON or YAML file."""
    text = Path(path).read_text(encoding="utf-8")
    if str(path).lower().endswith(".json"):
        return json.loads(text)
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}
