import json
import sqlite3
import uuid
from datetime import datetime, timezone


def format_decision_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_decision(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    ts: datetime,
    user_id: str,
    session_id: str,
    ip: str,
    decision: str,
    signals: dict,
    rationale: str,
    event_ids: list[str],
) -> str:
    cursor = conn.execute(
        "INSERT INTO decisions (project_id, id, ts, user_id, session_id, ip, decision, signals, rationale)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            f"pending_{uuid.uuid4().hex}",
            format_decision_ts(ts),
            user_id,
            session_id,
            ip,
            decision,
            json.dumps(signals),
            rationale,
        ),
    )
    decision_id = f"d_{cursor.lastrowid}"
    conn.execute("UPDATE decisions SET id = ? WHERE seq = ?", (decision_id, cursor.lastrowid))
    conn.executemany(
        "INSERT INTO decision_events (project_id, decision_id, event_id) VALUES (?, ?, ?)",
        [(project_id, decision_id, event_id) for event_id in event_ids],
    )
    return decision_id


def session_has_block(conn: sqlite3.Connection, project_id: str, session_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM decisions WHERE project_id = ? AND session_id = ? AND decision = 'block'",
        (project_id, session_id),
    ).fetchone()
    return row is not None


def list_decisions(conn: sqlite3.Connection, project_id: str, limit: int) -> list[dict]:
    """Newest first."""
    rows = conn.execute(
        "SELECT * FROM decisions WHERE project_id = ? ORDER BY seq DESC LIMIT ?", (project_id, limit)
    ).fetchall()
    result = []
    for row in rows:
        event_rows = conn.execute(
            "SELECT event_id FROM decision_events WHERE project_id = ? AND decision_id = ? ORDER BY event_id",
            (project_id, row["id"]),
        ).fetchall()
        result.append(
            {
                "id": row["id"],
                "ts": row["ts"],
                "user_id": row["user_id"],
                "decision": row["decision"],
                "signals": json.loads(row["signals"]),
                "rationale": row["rationale"],
                "event_ids": [event_row["event_id"] for event_row in event_rows],
                "status": "marked_normal" if row["normal"] else "open",
                "access": triggering_access(conn, project_id, row["id"]),
            }
        )
    return result


def triggering_access(conn: sqlite3.Connection, project_id: str, decision_id: str) -> dict | None:
    """The latest event behind a decision, as the panel compares it with the profile (contract 3)."""
    event = conn.execute(
        "SELECT e.ts, e.ip, e.device_id, e.session_id, e.method, e.route FROM decision_events d"
        " JOIN events e ON e.project_id = d.project_id AND e.id = d.event_id"
        " WHERE d.project_id = ? AND d.decision_id = ? ORDER BY e.ts DESC LIMIT 1",
        (project_id, decision_id),
    ).fetchone()
    if event is None:
        return None
    access = dict(event)
    access["ts"] = format_decision_ts(datetime.fromisoformat(access["ts"]))
    return access


def mark_normal(conn: sqlite3.Connection, project_id: str, decision_id: str) -> dict | None:
    """Mark a decision as normal use. Lifting a block bumps the blocklist version."""
    row = conn.execute(
        "SELECT decision, normal FROM decisions WHERE project_id = ? AND id = ?", (project_id, decision_id)
    ).fetchone()
    if row is None:
        return None
    if not row["normal"]:
        conn.execute("UPDATE decisions SET normal = 1 WHERE project_id = ? AND id = ?", (project_id, decision_id))
        if row["decision"] == "block":
            bump_version(conn, project_id)
    return {"id": decision_id, "normal": True, "version": current_version(conn, project_id)}


def current_version(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute("SELECT version FROM blocklist_versions WHERE project_id = ?", (project_id,)).fetchone()
    return row["version"] if row else 0


def bump_version(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        "INSERT INTO blocklist_versions (project_id, version) VALUES (?, 1)"
        " ON CONFLICT (project_id) DO UPDATE SET version = version + 1",
        (project_id,),
    )


def blocklist(conn: sqlite3.Connection, project_id: str) -> dict:
    """Full blocklist. "users" stays empty: blocking the user would lock out the owner too."""
    rows = conn.execute(
        "SELECT session_id, ip FROM decisions WHERE project_id = ? AND decision = 'block' AND normal = 0",
        (project_id,),
    ).fetchall()
    return {
        "version": current_version(conn, project_id),
        "users": [],
        "sessions": sorted({row["session_id"] for row in rows}),
        # A session-only block (the owner's own network) stores no ip.
        "ips": sorted({row["ip"] for row in rows if row["ip"]}),
    }


def reset_client(conn: sqlite3.Connection, project_id: str) -> int:
    """Delete the client's events and decisions (the profile is learned from them).

    The blocklist ends empty, and its version moves forward instead of back to 0, so a
    middleware polling with an older version gets the empty list, not a stale 304.
    """
    for table in ("decision_events", "decisions", "events", "rate_spike_judgments", "ai_judgments"):
        conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))
    bump_version(conn, project_id)
    return current_version(conn, project_id)
