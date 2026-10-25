import re
import sqlite3
from dataclasses import dataclass

import yaml

ROUTE_CLASSES = ("critical", "sensitive", "normal")
KEY_PATTERN = re.compile(r"^[A-Z]+ /\S*$")


def route_key(method: str, route: str) -> str:
    """Canonical "METHOD /route/template" key used for classification and the profile."""
    return f"{method.upper()} {route}"


@dataclass(frozen=True)
class RouteClasses:
    """Maps "METHOD /route/template" to a route class."""

    mapping: dict[str, str]

    @classmethod
    def load(cls, path: str) -> "RouteClasses":
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        mapping: dict[str, str] = {}
        for key, route_class in (data.get("routes") or {}).items():
            if not KEY_PATTERN.match(str(key)):
                raise ValueError(f"Route key {key!r} in {path} must look like 'POST /route/template'")
            if route_class not in ROUTE_CLASSES:
                raise ValueError(f"Unknown route class {route_class!r} for {key!r} in {path}")
            mapping[str(key)] = route_class
        return cls(mapping)

    @classmethod
    def for_project(cls, conn: sqlite3.Connection, project_id: str) -> "RouteClasses":
        """The project's own classification (route_rules)."""
        rows = conn.execute("SELECT method, route, class FROM route_rules WHERE project_id = ?", (project_id,))
        return cls({route_key(row[0], row[1]): row[2] for row in rows})

    def classify(self, method: str, route: str) -> str:
        return self.mapping.get(route_key(method, route), "normal")

    def listing(self) -> list[dict]:
        """Classified routes only (never "normal"), critical first, then by route and method."""
        rows = []
        for key, route_class in self.mapping.items():
            if route_class == "normal":
                continue
            method, route = key.split(" ", 1)
            rows.append({"method": method, "route": route, "class": route_class})
        order = {name: rank for rank, name in enumerate(ROUTE_CLASSES)}
        return sorted(rows, key=lambda r: (order[r["class"]], r["route"], r["method"]))


def replace_rules(conn: sqlite3.Connection, project_id: str, mapping: dict[str, str]) -> None:
    """Replace every rule of the project and mark it seeded. The caller owns the transaction."""
    conn.execute("DELETE FROM route_rules WHERE project_id = ?", (project_id,))
    conn.executemany(
        "INSERT INTO route_rules (project_id, method, route, class) VALUES (?, ?, ?, ?)",
        [(project_id, *key.split(" ", 1), route_class) for key, route_class in mapping.items()],
    )
    conn.execute("UPDATE project SET routes_seeded = 1 WHERE id = ?", (project_id,))


def seed_unseeded(conn: sqlite3.Connection, template: RouteClasses) -> list[str]:
    """Give the template to every project that never had rules. Returns the seeded ids."""
    ids = [row[0] for row in conn.execute("SELECT id FROM project WHERE routes_seeded = 0 ORDER BY rowid")]
    for project_id in ids:
        replace_rules(conn, project_id, template.mapping)
    return ids
