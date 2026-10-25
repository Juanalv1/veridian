import re
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
