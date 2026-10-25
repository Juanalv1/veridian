import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from ipaddress import ip_network

from .events import format_ts
from .route_classes import route_key

# An event is learned when it is older than the learn delay and is not part of an
# active block/alert decision, or when it belongs to a decision marked normal.
LEARNED_EVENTS_SQL = """
SELECT e.id, e.ts, e.session_id, e.ip, e.device_id, e.method, e.route FROM events e
WHERE e.project_id = :project_id AND e.user_id = :user_id AND (
    (e.ts <= :cutoff AND NOT EXISTS (
        SELECT 1 FROM decision_events de
        JOIN decisions d ON d.project_id = de.project_id AND d.id = de.decision_id
        WHERE de.project_id = e.project_id AND de.event_id = e.id
          AND d.decision IN ('block', 'alert') AND d.normal = 0))
    OR EXISTS (
        SELECT 1 FROM decision_events de
        JOIN decisions d ON d.project_id = de.project_id AND d.id = de.decision_id
        WHERE de.project_id = e.project_id AND de.event_id = e.id AND d.normal = 1)
)
"""

ACTIVE_BLOCK_SQL = """
SELECT 1 FROM decisions
WHERE project_id = ? AND user_id = ? AND decision = 'block' AND normal = 0 LIMIT 1
"""


RATE_WINDOW = timedelta(seconds=60)


def network_of(ip: str) -> str:
    """/24 for IPv4, /64 for IPv6."""
    prefix = 24 if ":" not in ip else 64
    return str(ip_network(f"{ip}/{prefix}", strict=False))


@dataclass
class Profile:
    user_id: str
    days: set[str] = field(default_factory=set)
    networks: set[str] = field(default_factory=set)
    devices: set[str] = field(default_factory=set)
    hours: set[int] = field(default_factory=set)
    routes: set[str] = field(default_factory=set)  # "METHOD /route/template"
    transitions: set[tuple[str, str]] = field(default_factory=set)  # (previous route key, route key)
    usual_max_rate_per_min: int = 0

    def to_dict(self, blocked: bool) -> dict:
        return {
            "user_id": self.user_id,
            "history_days": len(self.days),
            "networks": sorted(self.networks),
            "devices": sorted(self.devices),
            "hours": sorted(self.hours),
            "routes": sorted(self.routes),
            "usual_max_rate_per_min": self.usual_max_rate_per_min,
            "state": "blocked" if blocked else "normal",
        }


def build_profile(
    conn: sqlite3.Connection, project_id: str, user_id: str, now: datetime, learn_delay_min: int
) -> Profile:
    cutoff = format_ts(now - timedelta(minutes=learn_delay_min))
    profile = Profile(user_id)
    rows = conn.execute(LEARNED_EVENTS_SQL, {"project_id": project_id, "user_id": user_id, "cutoff": cutoff})
    sessions: dict[str, list] = {}
    for row in rows:
        sessions.setdefault(row["session_id"], []).append(row)
        profile.days.add(row["ts"][:10])
        profile.hours.add(int(row["ts"][11:13]))
        profile.networks.add(network_of(row["ip"]))
        if row["device_id"]:
            profile.devices.add(row["device_id"])
        profile.routes.add(route_key(row["method"], row["route"]))
    for session_rows in sessions.values():
        session_rows.sort(key=lambda row: (row["ts"], row["id"]))
        keys = [route_key(row["method"], row["route"]) for row in session_rows]
        profile.transitions.update(zip(keys, keys[1:]))
        times = [datetime.fromisoformat(row["ts"]) for row in session_rows]
        profile.usual_max_rate_per_min = max(profile.usual_max_rate_per_min, max_rate_per_min(times))
    return profile


def max_rate_per_min(times: list[datetime]) -> int:
    """Most events in any 60 s window ending at an event. `times` must be sorted."""
    best = start = 0
    for end, current in enumerate(times):
        while times[start] <= current - RATE_WINDOW:
            start += 1
        best = max(best, end - start + 1)
    return best


def is_blocked(conn: sqlite3.Connection, project_id: str, user_id: str) -> bool:
    return conn.execute(ACTIVE_BLOCK_SQL, (project_id, user_id)).fetchone() is not None
