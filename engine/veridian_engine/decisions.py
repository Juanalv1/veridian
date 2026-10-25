import json
import sqlite3
import uuid
from datetime import datetime, timezone


def format_decision_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_decision(
    conn: sqlite3.Connection,
    client_id: str,
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
        "INSERT INTO decisions (client_id, id, ts, user_id, session_id, ip, decision, signals, rationale)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            client_id,
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
        "INSERT INTO decision_events (client_id, decision_id, event_id) VALUES (?, ?, ?)",
        [(client_id, decision_id, event_id) for event_id in event_ids],
    )
    return decision_id


def session_has_block(conn: sqlite3.Connection, client_id: str, session_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM decisions WHERE client_id = ? AND session_id = ? AND decision = 'block'",
        (client_id, session_id),
    ).fetchone()
    return row is not None


def list_decisions(conn: sqlite3.Connection, client_id: str, limit: int) -> list[dict]:
    """Newest first."""
    rows = conn.execute(
        "SELECT * FROM decisions WHERE client_id = ? ORDER BY seq DESC LIMIT ?", (client_id, limit)
    ).fetchall()
    result = []
    for row in rows:
        event_rows = conn.execute(
            "SELECT event_id FROM decision_events WHERE client_id = ? AND decision_id = ? ORDER BY event_id",
            (client_id, row["id"]),
        )
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
            }
        )
    return result


def mark_normal(conn: sqlite3.Connection, client_id: str, decision_id: str) -> dict | None:
    """Mark a decision as normal use. Lifting a block bumps the blocklist version."""
    row = conn.execute(
        "SELECT decision, normal FROM decisions WHERE client_id = ? AND id = ?", (client_id, decision_id)
    ).fetchone()
    if row is None:
        return None
    if not row["normal"]:
        conn.execute("UPDATE decisions SET normal = 1 WHERE client_id = ? AND id = ?", (client_id, decision_id))
        if row["decision"] == "block":
            bump_version(conn, client_id)
    return {"id": decision_id, "normal": True, "version": current_version(conn, client_id)}


def current_version(conn: sqlite3.Connection, client_id: str) -> int:
    row = conn.execute("SELECT version FROM blocklist_versions WHERE client_id = ?", (client_id,)).fetchone()
    return row["version"] if row else 0


def bump_version(conn: sqlite3.Connection, client_id: str) -> None:
    conn.execute(
        "INSERT INTO blocklist_versions (client_id, version) VALUES (?, 1)"
        " ON CONFLICT (client_id) DO UPDATE SET version = version + 1",
        (client_id,),
    )


def blocklist(conn: sqlite3.Connection, client_id: str) -> dict:
    """Full blocklist. "users" stays empty: blocking the user would lock out the owner too."""
    rows = conn.execute(
        "SELECT session_id, ip FROM decisions WHERE client_id = ? AND decision = 'block' AND normal = 0",
        (client_id,),
    ).fetchall()
    return {
        "version": current_version(conn, client_id),
        "users": [],
        "sessions": sorted({row["session_id"] for row in rows}),
        "ips": sorted({row["ip"] for row in rows}),
    }


def reset_client(conn: sqlite3.Connection, client_id: str) -> int:
    """Delete the client's events and decisions (the profile is learned from them).

    The blocklist ends empty, and its version moves forward instead of back to 0, so a
    middleware polling with an older version gets the empty list, not a stale 304.
    """
    for table in ("decision_events", "decisions", "events"):
        conn.execute(f"DELETE FROM {table} WHERE client_id = ?", (client_id,))
    bump_version(conn, client_id)
    return current_version(conn, client_id)
