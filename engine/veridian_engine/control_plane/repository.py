"""Control-plane repositories with their domain rules.

Every state change and its audit row are written inside one savepoint, so either both
land or neither does. Functions never commit an enclosing transaction (such as the
one opened by db.connect); they only commit when they started the transaction.
"""

import hashlib
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from .roles import can, can_manage_role

INVITATION_TTL = timedelta(days=7)
DEFAULT_RETENTION_DAYS = 90
ENFORCEMENT_MODES = ("observe", "enforce")
API_KEY_PREFIX_LENGTH = 12


class ControlPlaneError(Exception):
    pass


class PermissionDenied(ControlPlaneError):
    pass


class LastOwnerError(ControlPlaneError):
    """A project must always keep at least one owner."""


class NotFound(ControlPlaneError):
    pass


class InvitationUnavailable(ControlPlaneError):
    """The invitation does not exist, or was already accepted, revoked or expired."""


def format_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def hash_user_id(project_id: str, user_id: str) -> str:
    """Audit stores this instead of the end user's id, so erasure leaves no personal data."""
    return hashlib.sha256(f"{project_id}:{user_id}".encode()).hexdigest()


def hash_api_key(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[None]:
    name = f"cp_{uuid.uuid4().hex}"
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    conn.execute(f"RELEASE {name}")


def record_audit(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    actor_person_id: str | None,
    actor_kind: str,
    action: str,
    target_type: str | None,
    target_id: str | None,
    now: datetime,
    meta: dict | None = None,
) -> None:
    if target_type == "user" and target_id is not None:
        target_id = hash_user_id(project_id, target_id)
    conn.execute(
        "INSERT INTO audit_log (project_id, actor_person_id, actor_kind, action, target_type, target_id, meta, ts)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, actor_person_id, actor_kind, action, target_type, target_id, json.dumps(meta or {}), format_ts(now)),
    )


def _audit_person(conn, project_id: str, actor_id: str, action: str, target_type: str, target_id: str, now, meta=None):
    record_audit(
        conn,
        project_id=project_id,
        actor_person_id=actor_id,
        actor_kind="person",
        action=action,
        target_type=target_type,
        target_id=target_id,
        now=now,
        meta=meta,
    )


# --- people ---


def upsert_person(conn: sqlite3.Connection, *, google_sub: str, email: str, name: str, now: datetime) -> str:
    """Create or refresh the person behind a verified Google login. Returns the person id."""
    ts = format_ts(now)
    with atomic(conn):
        conn.execute(
            "INSERT INTO person (id, google_sub, email, name, created_at, last_login_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (google_sub) DO UPDATE SET"
            " email = excluded.email, name = excluded.name, last_login_at = excluded.last_login_at",
            (new_id("per"), google_sub, email, name, ts, ts),
        )
        return conn.execute("SELECT id FROM person WHERE google_sub = ?", (google_sub,)).fetchone()[0]


# --- projects and memberships ---


def get_role(conn: sqlite3.Connection, project_id: str, person_id: str) -> str | None:
    row = conn.execute(
        "SELECT role FROM membership WHERE project_id = ? AND person_id = ?", (project_id, person_id)
    ).fetchone()
    return row[0] if row else None


def _require(conn, project_id: str, actor_id: str, action: str) -> str:
    role = get_role(conn, project_id, actor_id)
    if not can(role, action):
        raise PermissionDenied(action)
    return role


def _owner_count(conn, project_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM membership WHERE project_id = ? AND role = 'owner'", (project_id,)
    ).fetchone()[0]


def create_project(
    conn: sqlite3.Connection,
    *,
    creator_id: str,
    name: str,
    enforcement: str,
    now: datetime,
    ai_enabled: bool = True,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> str:
    """Create a project with its creator as owner. The caller picks enforcement (decision 6)."""
    if enforcement not in ENFORCEMENT_MODES:
        raise ValueError(f"enforcement must be one of {ENFORCEMENT_MODES}")
    project_id = new_id("prj")
    ts = format_ts(now)
    with atomic(conn):
        conn.execute(
            "INSERT INTO project (id, name, retention_days, enforcement, ai_enabled, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, name, retention_days, enforcement, int(ai_enabled), ts),
        )
        conn.execute(
            "INSERT INTO membership (project_id, person_id, role, created_at) VALUES (?, ?, 'owner', ?)",
            (project_id, creator_id, ts),
        )
        _audit_person(conn, project_id, creator_id, "project.create", "project", project_id, now)
    return project_id


def set_member_role(
    conn: sqlite3.Connection, *, actor_id: str, project_id: str, person_id: str, role: str, now: datetime
) -> None:
    with atomic(conn):
        current = get_role(conn, project_id, person_id)
        if current is None:
            raise NotFound("membership")
        actor_role = get_role(conn, project_id, actor_id)
        if not (can_manage_role(actor_role, current) and can_manage_role(actor_role, role)):
            raise PermissionDenied("manage_members")
        if current == "owner" and role != "owner" and _owner_count(conn, project_id) <= 1:
            raise LastOwnerError(project_id)
        conn.execute(
            "UPDATE membership SET role = ? WHERE project_id = ? AND person_id = ?", (role, project_id, person_id)
        )
        _audit_person(conn, project_id, actor_id, "member.role", "person", person_id, now, {"from": current, "to": role})


def remove_member(conn: sqlite3.Connection, *, actor_id: str, project_id: str, person_id: str, now: datetime) -> None:
    with atomic(conn):
        current = get_role(conn, project_id, person_id)
        if current is None:
            raise NotFound("membership")
        if not can_manage_role(get_role(conn, project_id, actor_id), current):
            raise PermissionDenied("manage_members")
        if current == "owner" and _owner_count(conn, project_id) <= 1:
            raise LastOwnerError(project_id)
        conn.execute("DELETE FROM membership WHERE project_id = ? AND person_id = ?", (project_id, person_id))
        _audit_person(conn, project_id, actor_id, "member.remove", "person", person_id, now, {"role": current})


# --- invitations ---


def create_invitation(
    conn: sqlite3.Connection, *, actor_id: str, project_id: str, email: str, role: str, now: datetime
) -> str:
    if not can_manage_role(get_role(conn, project_id, actor_id), role):
        raise PermissionDenied("manage_members")
    invitation_id = new_id("inv")
    with atomic(conn):
        conn.execute(
            "INSERT INTO invitation (id, project_id, email, role, invited_by, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (invitation_id, project_id, email.strip().lower(), role, actor_id, format_ts(now), format_ts(now + INVITATION_TTL)),
        )
        _audit_person(conn, project_id, actor_id, "invitation.create", "invitation", invitation_id, now, {"role": role})
    return invitation_id


def revoke_invitation(
    conn: sqlite3.Connection, *, actor_id: str, project_id: str, invitation_id: str, now: datetime
) -> None:
    with atomic(conn):
        row = conn.execute(
            "SELECT role FROM invitation WHERE id = ? AND project_id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
            (invitation_id, project_id),
        ).fetchone()
        if row is None:
            raise InvitationUnavailable(invitation_id)
        if not can_manage_role(get_role(conn, project_id, actor_id), row[0]):
            raise PermissionDenied("manage_members")
        conn.execute("UPDATE invitation SET revoked_at = ? WHERE id = ?", (format_ts(now), invitation_id))
        _audit_person(conn, project_id, actor_id, "invitation.revoke", "invitation", invitation_id, now)


def accept_pending_invitations(
    conn: sqlite3.Connection, *, person_id: str, verified_email: str, now: datetime
) -> list[str]:
    """Accept every live invitation for this verified email (run at login). Returns accepted ids.

    An invitation for a project the person already belongs to is skipped, so it never
    changes an existing role.
    """
    ts = format_ts(now)
    accepted = []
    with atomic(conn):
        rows = conn.execute(
            "SELECT id, project_id, role FROM invitation"
            " WHERE email = ? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > ?"
            " ORDER BY created_at, id",
            (verified_email.strip().lower(), ts),
        ).fetchall()
        for invitation_id, project_id, role in rows:
            if get_role(conn, project_id, person_id) is not None:
                continue
            conn.execute(
                "INSERT INTO membership (project_id, person_id, role, created_at) VALUES (?, ?, ?, ?)",
                (project_id, person_id, role, ts),
            )
            conn.execute("UPDATE invitation SET accepted_at = ? WHERE id = ?", (ts, invitation_id))
            _audit_person(conn, project_id, person_id, "invitation.accept", "invitation", invitation_id, now, {"role": role})
            accepted.append(invitation_id)
    return accepted


# --- API keys ---


def create_api_key(
    conn: sqlite3.Connection, *, actor_id: str, project_id: str, name: str, now: datetime
) -> tuple[str, str]:
    """Returns (key_id, secret). The secret is shown once; only its hash is stored."""
    _require(conn, project_id, actor_id, "manage_keys")
    key_id = new_id("key")
    secret = f"vrd_{secrets.token_urlsafe(32)}"
    with atomic(conn):
        conn.execute(
            "INSERT INTO api_key (id, project_id, name, prefix, key_hash, scope, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'sdk', ?, ?)",
            (key_id, project_id, name, secret[:API_KEY_PREFIX_LENGTH], hash_api_key(secret), actor_id, format_ts(now)),
        )
        _audit_person(conn, project_id, actor_id, "api_key.create", "api_key", key_id, now)
    return key_id, secret


def revoke_api_key(conn: sqlite3.Connection, *, actor_id: str, project_id: str, key_id: str, now: datetime) -> None:
    _require(conn, project_id, actor_id, "manage_keys")
    with atomic(conn):
        updated = conn.execute(
            "UPDATE api_key SET revoked_at = ? WHERE id = ? AND project_id = ? AND revoked_at IS NULL",
            (format_ts(now), key_id, project_id),
        ).rowcount
        if updated == 0:
            raise NotFound("api_key")
        _audit_person(conn, project_id, actor_id, "api_key.revoke", "api_key", key_id, now)
