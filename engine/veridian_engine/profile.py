import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from ipaddress import ip_network

from .events import format_ts
from .route_classes import route_key

# An event is learned when it is older than the learn delay and is not part of an
# active block/alert decision, or when it belongs to a decision marked normal.
LEARNED_EVENTS_SQL = """
SELECT e.ts, e.ip, e.device_id, e.method, e.route FROM events e
WHERE e.client_id = :client_id AND e.user_id = :user_id AND (
    (e.ts <= :cutoff AND NOT EXISTS (
        SELECT 1 FROM decision_events de
        JOIN decisions d ON d.client_id = de.client_id AND d.id = de.decision_id
        WHERE de.client_id = e.client_id AND de.event_id = e.id
          AND d.decision IN ('block', 'alert') AND d.normal = 0))
    OR EXISTS (
        SELECT 1 FROM decision_events de
        JOIN decisions d ON d.client_id = de.client_id AND d.id = de.decision_id
        WHERE de.client_id = e.client_id AND de.event_id = e.id AND d.normal = 1)
)
"""

ACTIVE_BLOCK_SQL = """
SELECT 1 FROM decisions
WHERE client_id = ? AND user_id = ? AND decision = 'block' AND normal = 0 LIMIT 1
"""


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

    def to_dict(self, blocked: bool) -> dict:
        return {
            "user_id": self.user_id,
            "history_days": len(self.days),
            "networks": sorted(self.networks),
            "devices": sorted(self.devices),
            "hours": sorted(self.hours),
            "routes": sorted(self.routes),
            "state": "blocked" if blocked else "normal",
        }


def build_profile(
    conn: sqlite3.Connection, client_id: str, user_id: str, now: datetime, learn_delay_min: int
) -> Profile:
    cutoff = format_ts(now - timedelta(minutes=learn_delay_min))
    profile = Profile(user_id)
    rows = conn.execute(LEARNED_EVENTS_SQL, {"client_id": client_id, "user_id": user_id, "cutoff": cutoff})
    for row in rows:
        profile.days.add(row["ts"][:10])
        profile.hours.add(int(row["ts"][11:13]))
        profile.networks.add(network_of(row["ip"]))
        if row["device_id"]:
            profile.devices.add(row["device_id"])
        profile.routes.add(route_key(row["method"], row["route"]))
    return profile


def is_blocked(conn: sqlite3.Connection, client_id: str, user_id: str) -> bool:
    return conn.execute(ACTIVE_BLOCK_SQL, (client_id, user_id)).fetchone() is not None
