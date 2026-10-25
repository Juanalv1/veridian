"""Persistence of the `notifications` outbox (migration 0005).

`enqueue_block_notification` runs inside the transaction that creates a block decision, so a
block is never lost by a failed send. Functions never commit: the caller's transaction does.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

from ..mailer import select_recipients
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
    notification = new_notification(
        new_notification_id(), project_id, decision_id, now, enforced=_project_enforces(conn, project_id)
    )
    _insert(conn, notification, now)
    return notification.id


def enqueue_block_emails(conn: sqlite3.Connection, project_id: str, decision_id: str, now: datetime) -> list[str]:
    """Queue one alert email per owner/admin of the project's organization with `notify_email` on."""
    enforced = _project_enforces(conn, project_id)
    ids = []
    for email in select_recipients(notified_members(conn, project_id)):
        notification = new_notification(
            new_notification_id(), project_id, decision_id, now, kind="email", enforced=enforced, recipient=email
        )
        _insert(conn, notification, now)
        ids.append(notification.id)
    return ids


def notified_members(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """Members of the project's organization with their role and preference, oldest first."""
    rows = conn.execute(
        "SELECT p.email, m.role, m.notify_email FROM project pr"
        " JOIN membership m ON m.org_id = pr.org_id JOIN person p ON p.id = m.person_id"
        " WHERE pr.id = ? ORDER BY m.created_at, p.id",
        (project_id,),
    ).fetchall()
    return [{"email": row["email"], "role": row["role"], "notify_email": bool(row["notify_email"])} for row in rows]


def _project_enforces(conn: sqlite3.Connection, project_id: str) -> bool:
    project = conn.execute("SELECT enforcement FROM project WHERE id = ?", (project_id,)).fetchone()
    # A project without a row enforces (project_settings.get_project_flags).
    return project is None or project["enforcement"] == "enforce"


def _insert(conn: sqlite3.Connection, notification: Notification, now: datetime) -> None:
    created_at = format_ts(now)
    conn.execute(
        "INSERT INTO notifications (id, project_id, kind, decision_id, status, attempts, next_attempt_at,"
        " last_error, enforced, recipient, purpose, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            notification.recipient,
            notification.purpose,
            created_at,
            created_at,
        ),
    )


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
        recipient=row["recipient"],
        purpose=row["purpose"],
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


def list_notifications(conn: sqlite3.Connection, project_id: str, limit: int, kind: str = "webhook") -> list[dict]:
    """Newest first; what `GET .../webhook/deliveries` shows. Email rows (and their recipients)
    are never listed there."""
    rows = conn.execute(
        "SELECT id, kind, decision_id, status, attempts, next_attempt_at, last_error, created_at, updated_at"
        " FROM notifications WHERE project_id = ? AND kind = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (project_id, kind, limit),
    ).fetchall()
    return [dict(row) for row in rows]
