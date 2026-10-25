"""Self-unlock by email, store side (docs/DESBLOQUEO-POR-CORREO.md): when to email the blocked user
and the single-use token that proves they control the mailbox.

A `block` decision queues one `notifications` row (`purpose = 'self_unlock'`) in the transaction
that creates it, when the project enforces and has the feature on, the user has an address and
none was queued for them in the last 15 minutes. The worker sends it later: the token is created
at send time, so the plain token exists only in the email. Only its SHA-256 is stored.
Functions never commit: the caller's transaction does.
"""

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta

from .notifications.outbox import _insert, format_ts, new_notification_id
from .notifications.webhook import new_notification

PURPOSE = "self_unlock"
MAIL_INTERVAL = timedelta(minutes=15)  # at most one email per user in this time. Not measured.
TOKEN_TTL = timedelta(hours=24)  # design value, not measured.


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_email(conn: sqlite3.Connection, project_id: str, user_id: str) -> str | None:
    row = conn.execute(
        "SELECT email FROM user_contact WHERE project_id = ? AND user_id = ?", (project_id, user_id)
    ).fetchone()
    return row["email"] if row else None


def project_self_unlock(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
    """The project row when the feature is on and the project enforces, else None. An `observe`
    project blocks nobody, so there is nothing for the user to unlock."""
    return conn.execute(
        "SELECT * FROM project WHERE id = ? AND deleted_at IS NULL AND self_unlock_enabled = 1"
        " AND enforcement = 'enforce'",
        (project_id,),
    ).fetchone()


def self_unlock_applies(conn: sqlite3.Connection, project_id: str, user_id: str) -> bool:
    """Whether a blocked user gets (or got) the unlock email: what the middleware tells them."""
    return project_self_unlock(conn, project_id) is not None and user_email(conn, project_id, user_id) is not None


def enqueue_self_unlock_email(
    conn: sqlite3.Connection, project_id: str, decision_id: str, user_id: str, now: datetime
) -> str | None:
    """Queue the email for a block decision, or None when it does not apply or the user was
    already mailed in the last 15 minutes."""
    if not self_unlock_applies(conn, project_id, user_id):
        return None
    last = conn.execute(
        "SELECT last_mail_at FROM user_contact WHERE project_id = ? AND user_id = ?", (project_id, user_id)
    ).fetchone()["last_mail_at"]
    if last and now - datetime.fromisoformat(last) < MAIL_INTERVAL:
        return None
    notification = new_notification(new_notification_id(), project_id, decision_id, now, kind="email",
                                    purpose=PURPOSE)
    _insert(conn, notification, now)
    conn.execute(
        "UPDATE user_contact SET last_mail_at = ? WHERE project_id = ? AND user_id = ?",
        (format_ts(now), project_id, user_id),
    )
    return notification.id


def issue_token(
    conn: sqlite3.Connection, *, notification_id: str, project_id: str, decision_id: str, user_id: str, now: datetime
) -> str:
    """A new random token for the notification; its unused predecessors (a failed attempt) are
    deleted so one email never leaves several live links."""
    conn.execute("DELETE FROM self_unlock_token WHERE notification_id = ? AND used_at IS NULL", (notification_id,))
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO self_unlock_token (token_hash, project_id, decision_id, user_id, notification_id, created_at,"
        " expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (hash_token(token), project_id, decision_id, user_id, notification_id, format_ts(now),
         format_ts(now + TOKEN_TTL)),
    )
    return token


def access_of(conn: sqlite3.Connection, project_id: str, decision_id: str) -> dict | None:
    """Time and user agent of the latest event behind a decision. The IP is not selected."""
    row = conn.execute(
        "SELECT e.ts, e.user_agent FROM decision_events d JOIN events e"
        " ON e.project_id = d.project_id AND e.id = d.event_id"
        " WHERE d.project_id = ? AND d.decision_id = ? ORDER BY e.ts DESC LIMIT 1",
        (project_id, decision_id),
    ).fetchone()
    return dict(row) if row else None
