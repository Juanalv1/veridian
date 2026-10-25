"""Self-unlock by email, page side: read a token and unlock with it (docs/DESBLOQUEO-POR-CORREO.md).

The token is the end user's proof. `read_token` never changes anything (mail scanners open links);
`unlock` reuses `decisions.mark_normal`, so the blocklist entry goes away, the version moves and the
profile learns exactly as when an admin marks the decision normal, and audits `decision.self_unlocked`.
Functions never commit: the caller's transaction does.
"""

import hmac
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .control_plane.repository import record_audit
from .decisions import mark_normal
from .notifications.outbox import format_ts
from .self_unlock import access_of, hash_token
from .self_unlock_email import access_detail

VALID, USED, CLOSED, EXPIRED = "valid", "used", "closed", "expired"


@dataclass(frozen=True)
class TokenView:
    state: str
    project_id: str
    decision_id: str
    expires_at: str
    brand: dict
    access: dict


def _brand(project: sqlite3.Row | None, project_id: str) -> dict:
    if project is None:
        return {"name": project_id, "logo_url": None, "color": None, "support_url": None, "support_email": None}
    return {
        "name": project["brand_name"] or project["name"],
        "logo_url": project["brand_logo_url"],
        "color": project["brand_color"],
        "support_url": project["brand_support_url"],
        "support_email": project["brand_support_email"],
    }


def read_token(conn: sqlite3.Connection, token: str, now: datetime) -> TokenView | None:
    """State and page data of a token, or None when it is unknown. Read-only."""
    if not token:
        return None
    digest = hash_token(token)
    row = conn.execute("SELECT * FROM self_unlock_token WHERE token_hash = ?", (digest,)).fetchone()
    if row is None or not hmac.compare_digest(row["token_hash"], digest):
        return None
    decision = conn.execute(
        "SELECT decision, normal FROM decisions WHERE project_id = ? AND id = ?",
        (row["project_id"], row["decision_id"]),
    ).fetchone()
    if row["used_at"] is not None:
        state = USED
    elif decision is None or decision["decision"] != "block" or decision["normal"]:
        state = CLOSED  # an admin (or an erasure) got there first
    elif format_ts(now) >= row["expires_at"]:
        state = EXPIRED
    else:
        state = VALID
    project = conn.execute("SELECT * FROM project WHERE id = ?", (row["project_id"],)).fetchone()
    access = access_of(conn, row["project_id"], row["decision_id"])
    detail = access_detail(
        datetime.fromisoformat(access["ts"]) if access else None, access["user_agent"] if access else None
    )
    return TokenView(state, row["project_id"], row["decision_id"], row["expires_at"],
                     _brand(project, row["project_id"]), detail)


def unlock(conn: sqlite3.Connection, token: str, now: datetime) -> TokenView | None:
    """Spend the token and mark its decision normal. Returns the view as it was: `state == VALID`
    means it just unlocked; any other state changed nothing. None: unknown token.

    The caller holds the write lock; the conditional UPDATE still makes the single use explicit."""
    view = read_token(conn, token, now)
    if view is None or view.state != VALID:
        return view
    spent = conn.execute(
        "UPDATE self_unlock_token SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
        (format_ts(now), hash_token(token)),
    ).rowcount
    if spent != 1:
        return TokenView(USED, *view[1:])
    mark_normal(conn, view.project_id, view.decision_id)
    record_audit(
        conn,
        actor_person_id=None,
        actor_kind="system",
        action="decision.self_unlocked",
        target_type="decision",
        target_id=view.decision_id,
        now=now,
        meta={"via": "email"},
        project_id=view.project_id,
    )
    return view
