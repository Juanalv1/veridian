"""Control-plane repositories with their domain rules.

Organizations group projects and hold the only member list: one role per person and
organization, valid for every project of it (docs/DISENO-PRODUCTO.md, "Revisión 2026-10-25").
Invitations and audit belong to the organization; an audit row also names the project it
touched, if any.

Every state change and its audit row are written inside one savepoint, so either both
land or neither does. Functions never commit an enclosing transaction (such as the
one opened by db.connect); they only commit when they started the transaction.
"""

import hashlib
import json
import secrets
import sqlite3
import uuid
from collections.abc import Collection
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from ..keys import generate_key
from .roles import can, can_manage_role

INVITATION_TTL = timedelta(days=7)
DEFAULT_RETENTION_DAYS = 90
ENFORCEMENT_MODES = ("observe", "enforce")


class ControlPlaneError(Exception):
    pass


class PermissionDenied(ControlPlaneError):
    pass


class LastOwnerError(ControlPlaneError):
    """An organization must always keep at least one owner."""


class NotFound(ControlPlaneError):
    pass


class InvitationUnavailable(ControlPlaneError):
    """The invitation does not exist, or was already accepted, revoked or expired."""


@dataclass(frozen=True)
class CreatedKey:
    id: str
    name: str
    prefix: str
    key: str  # shown once, never stored


def format_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def hash_user_id(project_id: str, user_id: str) -> str:
    """Audit stores this instead of the end user's id, so erasure leaves no personal data."""
    return hashlib.sha256(f"{project_id}:{user_id}".encode()).hexdigest()


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
    actor_person_id: str | None,
    actor_kind: str,
    action: str,
    target_type: str | None,
    target_id: str | None,
    now: datetime,
    meta: dict | None = None,
    project_id: str | None = None,
    org_id: str | None = None,
) -> None:
    """Append one audit row. With only `project_id`, the organization is the project's."""
    if org_id is None and project_id is not None:
        row = conn.execute("SELECT org_id FROM project WHERE id = ?", (project_id,)).fetchone()
        org_id = row[0] if row else None
    if target_type == "user" and target_id is not None:
        target_id = hash_user_id(project_id or org_id or "", target_id)
    conn.execute(
        "INSERT INTO audit_log (org_id, project_id, actor_person_id, actor_kind, action, target_type, target_id, meta, ts)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (org_id, project_id, actor_person_id, actor_kind, action, target_type, target_id, json.dumps(meta or {}),
         format_ts(now)),
    )


def _audit_person(conn, actor_id: str, action: str, target_type: str, target_id: str, now, *, org_id=None,
                  project_id=None, meta=None):
    record_audit(
        conn,
        org_id=org_id,
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


def get_person(conn: sqlite3.Connection, person_id: str) -> dict | None:
    row = conn.execute("SELECT id, email, name FROM person WHERE id = ?", (person_id,)).fetchone()
    return {"id": row[0], "email": row[1], "name": row[2]} if row else None


def person_name_by_sub(conn: sqlite3.Connection, google_sub: str) -> str | None:
    row = conn.execute("SELECT name FROM person WHERE google_sub = ?", (google_sub,)).fetchone()
    return row[0] if row else None


# --- organizations and roles ---


def get_role(conn: sqlite3.Connection, org_id: str, person_id: str) -> str | None:
    """The person's role in a live organization, or None."""
    row = conn.execute(
        "SELECT m.role FROM membership m JOIN organization o ON o.id = m.org_id"
        " WHERE m.org_id = ? AND m.person_id = ? AND o.deleted_at IS NULL",
        (org_id, person_id),
    ).fetchone()
    return row[0] if row else None


def project_org(conn: sqlite3.Connection, project_id: str) -> str | None:
    """The organization of a live project in a live organization, or None."""
    row = conn.execute(
        "SELECT p.org_id FROM project p JOIN organization o ON o.id = p.org_id"
        " WHERE p.id = ? AND p.deleted_at IS NULL AND o.deleted_at IS NULL",
        (project_id,),
    ).fetchone()
    return row[0] if row else None


def role_in_project(conn: sqlite3.Connection, project_id: str, person_id: str) -> str | None:
    """There are no project roles: this is the person's role in the project's organization."""
    org_id = project_org(conn, project_id)
    return get_role(conn, org_id, person_id) if org_id else None


def _require_org(conn, org_id: str, actor_id: str, action: str) -> str:
    role = get_role(conn, org_id, actor_id)
    if not can(role, action):
        raise PermissionDenied(action)
    return role


def _require_project(conn, project_id: str, actor_id: str, action: str) -> str:
    """Returns the project's organization id after checking the actor's role in it."""
    org_id = project_org(conn, project_id)
    if org_id is None or not can(get_role(conn, org_id, actor_id), action):
        raise PermissionDenied(action)
    return org_id


def _owner_count(conn, org_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM membership WHERE org_id = ? AND role = 'owner'", (org_id,)).fetchone()[0]


def create_organization(conn: sqlite3.Connection, *, creator_id: str, name: str, now: datetime) -> str:
    """Create an organization with its creator as owner. Who may create one is the caller's rule."""
    org_id = new_id("org")
    ts = format_ts(now)
    with atomic(conn):
        conn.execute("INSERT INTO organization (id, name, created_at) VALUES (?, ?, ?)", (org_id, name, ts))
        conn.execute(
            "INSERT INTO membership (org_id, person_id, role, created_at) VALUES (?, ?, 'owner', ?)",
            (org_id, creator_id, ts),
        )
        _audit_person(conn, creator_id, "org.create", "org", org_id, now, org_id=org_id)
    return org_id


def get_organization(conn: sqlite3.Connection, org_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, name, created_at FROM organization WHERE id = ? AND deleted_at IS NULL", (org_id,)
    ).fetchone()
    return {"id": row[0], "name": row[1], "created_at": row[2]} if row else None


def rename_organization(conn: sqlite3.Connection, *, actor_id: str, org_id: str, name: str, now: datetime) -> None:
    with atomic(conn):
        _require_org(conn, org_id, actor_id, "edit_org")
        conn.execute("UPDATE organization SET name = ? WHERE id = ?", (name, org_id))
        _audit_person(conn, actor_id, "org.update", "org", org_id, now, org_id=org_id, meta={"name": name})


def list_org_projects(conn: sqlite3.Connection, org_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, org_id, name, retention_days, enforcement, ai_enabled, created_at FROM project"
        " WHERE org_id = ? AND deleted_at IS NULL ORDER BY created_at, rowid",
        (org_id,),
    ).fetchall()
    return [_project_dict(row) for row in rows]


def list_orgs_for(conn: sqlite3.Connection, person_id: str) -> list[dict]:
    """The person's live organizations, with their role and the projects of each ({id, name})."""
    rows = conn.execute(
        "SELECT o.id, o.name, m.role FROM membership m JOIN organization o ON o.id = m.org_id"
        " WHERE m.person_id = ? AND o.deleted_at IS NULL ORDER BY o.created_at, o.rowid",
        (person_id,),
    ).fetchall()
    return [
        {"id": row[0], "name": row[1], "role": row[2],
         "projects": [{"id": p["id"], "name": p["name"]} for p in list_org_projects(conn, row[0])]}
        for row in rows
    ]


def bootstrap_owner(
    conn: sqlite3.Connection, *, person_id: str, email: str, owners: Collection[str], now: datetime
) -> list[str]:
    """Make the person owner of every live organization that has no owner, if their verified
    email is on the bootstrap list (VERIDIAN_BOOTSTRAP_OWNERS). Returns the org ids taken."""
    if email.strip().lower() not in {o.strip().lower() for o in owners}:
        return []
    ts = format_ts(now)
    taken = []
    with atomic(conn):
        orphans = conn.execute(
            "SELECT id FROM organization o WHERE deleted_at IS NULL AND NOT EXISTS"
            " (SELECT 1 FROM membership m WHERE m.org_id = o.id AND m.role = 'owner') ORDER BY created_at, rowid"
        ).fetchall()
        for (org_id,) in orphans:
            conn.execute(
                "INSERT INTO membership (org_id, person_id, role, created_at) VALUES (?, ?, 'owner', ?)"
                " ON CONFLICT (org_id, person_id) DO UPDATE SET role = 'owner'",
                (org_id, person_id, ts),
            )
            record_audit(
                conn, org_id=org_id, actor_person_id=None, actor_kind="system", action="org.bootstrap_owner",
                target_type="person", target_id=person_id, now=now,
            )
            taken.append(org_id)
    return taken


# --- projects ---


def _project_dict(row) -> dict:
    return {"id": row[0], "org_id": row[1], "name": row[2], "retention_days": row[3], "enforcement": row[4],
            "ai_enabled": bool(row[5]), "created_at": row[6]}


def get_project(conn: sqlite3.Connection, project_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, org_id, name, retention_days, enforcement, ai_enabled, created_at FROM project"
        " WHERE id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchone()
    return _project_dict(row) if row else None


def create_project(
    conn: sqlite3.Connection,
    *,
    actor_id: str,
    org_id: str,
    name: str,
    enforcement: str,
    now: datetime,
    ai_enabled: bool = True,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> str:
    """Create a project in an organization; the actor must be its admin or owner. The caller
    picks enforcement (decision 6)."""
    if enforcement not in ENFORCEMENT_MODES:
        raise ValueError(f"enforcement must be one of {ENFORCEMENT_MODES}")
    project_id = new_id("prj")
    with atomic(conn):
        _require_org(conn, org_id, actor_id, "create_project")
        conn.execute(
            "INSERT INTO project (id, org_id, name, retention_days, enforcement, ai_enabled, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, org_id, name, retention_days, enforcement, int(ai_enabled), format_ts(now)),
        )
        _audit_person(conn, actor_id, "project.create", "project", project_id, now, org_id=org_id, project_id=project_id)
    return project_id


def update_project(
    conn: sqlite3.Connection,
    *,
    actor_id: str,
    project_id: str,
    changes: dict,
    now: datetime,
) -> None:
    """Change name and retention_days here; enforcement and ai_enabled go through
    project_settings.set_project_flags (it bumps the blocklist version), called by the caller
    inside the same transaction. This function checks the role and writes the audit row."""
    with atomic(conn):
        org_id = _require_project(conn, project_id, actor_id, "edit_settings")
        if "name" in changes:
            conn.execute("UPDATE project SET name = ? WHERE id = ?", (changes["name"], project_id))
        if "retention_days" in changes:
            conn.execute("UPDATE project SET retention_days = ? WHERE id = ?", (changes["retention_days"], project_id))
        _audit_person(conn, actor_id, "project.update", "project", project_id, now, org_id=org_id,
                      project_id=project_id, meta=changes)


def delete_project(conn: sqlite3.Connection, *, actor_id: str, project_id: str, now: datetime) -> None:
    """Soft delete (deleted_at): the project leaves every read and its keys stop authenticating.
    The data stays until the retention purge removes it (U13)."""
    with atomic(conn):
        org_id = _require_project(conn, project_id, actor_id, "delete_project")
        conn.execute("UPDATE project SET deleted_at = ? WHERE id = ?", (format_ts(now), project_id))
        _audit_person(conn, actor_id, "project.delete", "project", project_id, now, org_id=org_id,
                      project_id=project_id)


# --- members ---


def set_member_role(
    conn: sqlite3.Connection, *, actor_id: str, org_id: str, person_id: str, role: str, now: datetime
) -> None:
    with atomic(conn):
        current = get_role(conn, org_id, person_id)
        if current is None:
            raise NotFound("membership")
        actor_role = get_role(conn, org_id, actor_id)
        if not (can_manage_role(actor_role, current) and can_manage_role(actor_role, role)):
            raise PermissionDenied("manage_members")
        if current == "owner" and role != "owner" and _owner_count(conn, org_id) <= 1:
            raise LastOwnerError(org_id)
        conn.execute("UPDATE membership SET role = ? WHERE org_id = ? AND person_id = ?", (role, org_id, person_id))
        _audit_person(conn, actor_id, "member.role", "person", person_id, now, org_id=org_id,
                      meta={"from": current, "to": role})


def remove_member(conn: sqlite3.Connection, *, actor_id: str, org_id: str, person_id: str, now: datetime) -> None:
    with atomic(conn):
        current = get_role(conn, org_id, person_id)
        if current is None:
            raise NotFound("membership")
        if not can_manage_role(get_role(conn, org_id, actor_id), current):
            raise PermissionDenied("manage_members")
        if current == "owner" and _owner_count(conn, org_id) <= 1:
            raise LastOwnerError(org_id)
        conn.execute("DELETE FROM membership WHERE org_id = ? AND person_id = ?", (org_id, person_id))
        _audit_person(conn, actor_id, "member.remove", "person", person_id, now, org_id=org_id, meta={"role": current})


# --- invitations ---


def create_invitation(
    conn: sqlite3.Connection, *, actor_id: str, org_id: str, email: str, role: str, now: datetime
) -> str:
    if not can_manage_role(get_role(conn, org_id, actor_id), role):
        raise PermissionDenied("manage_members")
    invitation_id = new_id("inv")
    with atomic(conn):
        conn.execute(
            "INSERT INTO invitation (id, org_id, email, role, invited_by, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (invitation_id, org_id, email.strip().lower(), role, actor_id, format_ts(now), format_ts(now + INVITATION_TTL)),
        )
        _audit_person(conn, actor_id, "invitation.create", "invitation", invitation_id, now, org_id=org_id,
                      meta={"role": role})
    return invitation_id


def revoke_invitation(conn: sqlite3.Connection, *, actor_id: str, org_id: str, invitation_id: str, now: datetime) -> None:
    with atomic(conn):
        row = conn.execute(
            "SELECT role FROM invitation WHERE id = ? AND org_id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
            (invitation_id, org_id),
        ).fetchone()
        if row is None:
            raise InvitationUnavailable(invitation_id)
        if not can_manage_role(get_role(conn, org_id, actor_id), row[0]):
            raise PermissionDenied("manage_members")
        conn.execute("UPDATE invitation SET revoked_at = ? WHERE id = ?", (format_ts(now), invitation_id))
        _audit_person(conn, actor_id, "invitation.revoke", "invitation", invitation_id, now, org_id=org_id)


def accept_pending_invitations(
    conn: sqlite3.Connection, *, person_id: str, verified_email: str, now: datetime
) -> list[str]:
    """Accept every live invitation for this verified email (run at login). Returns accepted ids.

    An invitation to an organization the person already belongs to is skipped, so it never
    changes an existing role.
    """
    ts = format_ts(now)
    accepted = []
    with atomic(conn):
        rows = conn.execute(
            "SELECT i.id, i.org_id, i.role FROM invitation i JOIN organization o ON o.id = i.org_id"
            " WHERE i.email = ? AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > ?"
            " AND o.deleted_at IS NULL ORDER BY i.created_at, i.id",
            (verified_email.strip().lower(), ts),
        ).fetchall()
        for invitation_id, org_id, role in rows:
            if get_role(conn, org_id, person_id) is not None:
                continue
            conn.execute(
                "INSERT INTO membership (org_id, person_id, role, created_at) VALUES (?, ?, ?, ?)",
                (org_id, person_id, role, ts),
            )
            conn.execute("UPDATE invitation SET accepted_at = ? WHERE id = ?", (ts, invitation_id))
            _audit_person(conn, person_id, "invitation.accept", "invitation", invitation_id, now, org_id=org_id,
                          meta={"role": role})
            accepted.append(invitation_id)
    return accepted


# --- API keys (contract 12) ---


def create_api_key(conn: sqlite3.Connection, *, actor_id: str, project_id: str, name: str, now: datetime) -> CreatedKey:
    """Create an `sdk` key in the `keys.generate_key` format. Only its hash and prefix are stored;
    the full key is returned once."""
    generated = generate_key()
    key_id = new_id("key")
    with atomic(conn):
        org_id = _require_project(conn, project_id, actor_id, "manage_keys")
        conn.execute(
            "INSERT INTO api_key (id, project_id, name, prefix, key_hash, scope, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'sdk', ?, ?)",
            (key_id, project_id, name, generated.prefix, generated.key_hash, actor_id, format_ts(now)),
        )
        _audit_person(conn, actor_id, "api_key.create", "api_key", key_id, now, org_id=org_id, project_id=project_id)
    return CreatedKey(id=key_id, name=name, prefix=generated.prefix, key=generated.key)


_KEY_COLUMNS = ("id", "name", "prefix", "created_by", "created_at", "last_used_at", "revoked_at")


def list_api_keys(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """Every key of the project, newest first. Never the key nor its hash."""
    rows = conn.execute(
        f"SELECT {', '.join(_KEY_COLUMNS)} FROM api_key WHERE project_id = ? ORDER BY created_at DESC, rowid DESC",
        (project_id,),
    ).fetchall()
    return [dict(zip(_KEY_COLUMNS, row)) for row in rows]


def get_api_key(conn: sqlite3.Connection, project_id: str, key_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {', '.join(_KEY_COLUMNS)} FROM api_key WHERE id = ? AND project_id = ?", (key_id, project_id)
    ).fetchone()
    return dict(zip(_KEY_COLUMNS, row)) if row else None


def revoke_api_key(conn: sqlite3.Connection, *, actor_id: str, project_id: str, key_id: str, now: datetime) -> None:
    with atomic(conn):
        org_id = _require_project(conn, project_id, actor_id, "manage_keys")
        updated = conn.execute(
            "UPDATE api_key SET revoked_at = ? WHERE id = ? AND project_id = ? AND revoked_at IS NULL",
            (format_ts(now), key_id, project_id),
        ).rowcount
        if updated == 0:
            raise NotFound("api_key")
        _audit_person(conn, actor_id, "api_key.revoke", "api_key", key_id, now, org_id=org_id, project_id=project_id)
