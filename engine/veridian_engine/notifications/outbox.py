"""Persistence of the `notifications` outbox (migration 0005).

`enqueue_block_notification` runs inside the transaction that creates a block decision, so a
block is never lost by a failed send. Functions never commit: the caller's transaction does.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

from .webhook import Notification, new_notification

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def format_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(TS_FORMAT)


def parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def new_notification_id() -> str:
    return f"ntf_{uuid.uuid4().hex}"


def enqueue_block_notification(
    conn: sqlite3.Connection, project_id: str, decision_id: str, now: datetime
) -> str | None:
    """Queue a webhook for a block decision if the project has an enabled endpoint.

    The project's mode is copied into the row: a project in `observe` notifies too, marked
    not enforced, and a later mode change does not alter what retries send.
    """
    endpoint = conn.execute(
        "SELECT 1 FROM webhook_endpoint WHERE project_id = ? AND enabled = 1", (project_id,)
    ).fetchone()
    if endpoint is None:
        return None
    project = conn.execute("SELECT enforcement FROM project WHERE id = ?", (project_id,)).fetchone()
    # A project without a row enforces (project_settings.get_project_flags).
    enforced = project is None or project["enforcement"] == "enforce"
    notification = new_notification(new_notification_id(), project_id, decision_id, now, enforced=enforced)
    created_at = format_ts(now)
    conn.execute(
        "INSERT INTO notifications (id, project_id, kind, decision_id, status, attempts, next_attempt_at,"
        " last_error, enforced, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            notification.id,
            notification.project_id,
            notification.kind,
            notification.decision_id,
            notification.status,
            notification.attempts,
            format_ts(notification.next_attempt_at),
            notification.last_error,
            int(notification.enforced),
            created_at,
            created_at,
        ),
    )
    return notification.id


def _from_row(row) -> Notification:
    return Notification(
        id=row["id"],
        project_id=row["project_id"],
        decision_id=row["decision_id"],
        kind=row["kind"],
        status=row["status"],
        attempts=row["attempts"],
        next_attempt_at=parse_ts(row["next_attempt_at"]),
        last_error=row["last_error"],
        enforced=bool(row["enforced"]),
    )


def due_notifications(conn: sqlite3.Connection, now: datetime, limit: int, kind: str = "webhook") -> list[Notification]:
    """Pending rows whose next attempt is due, oldest first. Across projects: the worker is global."""
    rows = conn.execute(
        "SELECT * FROM notifications WHERE kind = ? AND status = 'pending' AND next_attempt_at <= ?"
        " ORDER BY next_attempt_at, id LIMIT ?",
        (kind, format_ts(now), limit),
    ).fetchall()
    return [_from_row(row) for row in rows]


def save_notification(conn: sqlite3.Connection, notification: Notification, now: datetime) -> None:
    conn.execute(
        "UPDATE notifications SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?, updated_at = ?"
        " WHERE id = ?",
        (
            notification.status,
            notification.attempts,
            format_ts(notification.next_attempt_at) if notification.next_attempt_at else None,
            notification.last_error,
            format_ts(now),
            notification.id,
        ),
    )


def list_notifications(conn: sqlite3.Connection, project_id: str, limit: int) -> list[dict]:
    """Newest first; what `GET .../webhook/deliveries` will show."""
    rows = conn.execute(
        "SELECT id, kind, decision_id, status, attempts, next_attempt_at, last_error, created_at, updated_at"
        " FROM notifications WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]
