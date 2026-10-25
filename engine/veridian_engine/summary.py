"""Panel overview: range summary (contract 6) and the user list (contract 7)."""

import sqlite3
from datetime import datetime, timezone

from .decisions import blocklist, format_decision_ts
from .events import format_ts
from .profile import build_profile

DECISION_TYPES = ("allow", "alert", "step_up", "block")


def as_utc(value: datetime) -> datetime:
    """Query params without an offset are read as UTC, like every ts in the contracts."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_summary(conn: sqlite3.Connection, client_id: str, since: datetime, until: datetime) -> dict:
    """Counts for [since, until). Events are stored with microseconds, decisions to the second."""
    events_range = {"client_id": client_id, "since": format_ts(since), "until": format_ts(until)}
    decisions_range = {
        "client_id": client_id,
        "since": format_decision_ts(since),
        "until": format_decision_ts(until),
    }

    totals = conn.execute(
        "SELECT COUNT(*) AS accesses, COUNT(DISTINCT user_id) AS users FROM events"
        " WHERE client_id = :client_id AND ts >= :since AND ts < :until",
        events_range,
    ).fetchone()
    reviewed = conn.execute(
        "SELECT COUNT(*) FROM ai_judgments WHERE client_id = :client_id AND ts >= :since AND ts < :until",
        events_range,
    ).fetchone()[0]

    counts = dict.fromkeys(DECISION_TYPES, 0)
    for row in conn.execute(
        "SELECT decision, COUNT(*) AS n FROM decisions"
        " WHERE client_id = :client_id AND ts >= :since AND ts < :until GROUP BY decision",
        decisions_range,
    ):
        counts[row["decision"]] = row["n"]

    open_blocks = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE client_id = ? AND decision = 'block' AND normal = 0", (client_id,)
    ).fetchone()[0]
    listed = blocklist(conn, client_id)

    blocks_by_hour = {
        row["hour"]: row["n"]
        for row in conn.execute(
            "SELECT substr(ts, 1, 13) AS hour, COUNT(*) AS n FROM decisions"
            " WHERE client_id = :client_id AND decision = 'block' AND ts >= :since AND ts < :until"
            " GROUP BY hour",
            decisions_range,
        )
    }
    by_hour = [
        {"hour": f"{row['hour']}:00:00Z", "accesses": row["n"], "blocks": blocks_by_hour.get(row["hour"], 0)}
        for row in conn.execute(
            "SELECT substr(ts, 1, 13) AS hour, COUNT(*) AS n FROM events"
            " WHERE client_id = :client_id AND ts >= :since AND ts < :until GROUP BY hour ORDER BY hour",
            events_range,
        )
    ]

    return {
        "since": format_decision_ts(since),
        "until": format_decision_ts(until),
        "accesses": totals["accesses"],
        "users": totals["users"],
        "reviewed_by_ai": reviewed,
        "decisions": counts,
        "blocks_active": {
            "decisions": open_blocks,
            "sessions": len(listed["sessions"]),
            "ips": len(listed["ips"]),
        },
        "by_hour": by_hour,
    }


USERS_SQL = """
SELECT e.user_id, MAX(e.ts) AS last_seen,
       (SELECT COUNT(*) FROM decisions d
        WHERE d.client_id = e.client_id AND d.user_id = e.user_id
          AND d.decision = 'block' AND d.normal = 0) AS open_blocks
FROM events e WHERE e.client_id = ?
GROUP BY e.user_id
"""


def list_users(conn: sqlite3.Connection, client_id: str, now: datetime, learn_delay_min: int) -> list[dict]:
    """Every user with events: open blocks first, then the most recently seen."""
    rows = conn.execute(USERS_SQL, (client_id,)).fetchall()
    rows = sorted(rows, key=lambda row: row["last_seen"], reverse=True)
    rows.sort(key=lambda row: row["open_blocks"] == 0)
    result = []
    for row in rows:
        profile = build_profile(conn, client_id, row["user_id"], now, learn_delay_min)
        result.append(
            {
                "user_id": row["user_id"],
                "state": "blocked" if row["open_blocks"] else "normal",
                "history_days": len(profile.days),
                "networks": len(profile.networks),
                "devices": len(profile.devices),
                # Stored with microseconds; the panel gets the decision ts shape.
                "last_seen": row["last_seen"][:19] + "Z",
                "open_blocks": row["open_blocks"],
            }
        )
    return result
