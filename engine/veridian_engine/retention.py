"""Retention and erasure of a project's personal data (docs/DISENO-PRODUCTO.md, gap 7).

Profiles are computed from events when read, so deleting events also deletes what was
learned. Every function runs inside one savepoint together with its audit row, and never
commits an enclosing transaction. An open block (decision 'block', not marked normal) is
the blocklist itself: retention never deletes it or its evidence, and only an explicit
erasure can lift it, which bumps the blocklist version so middlewares fetch the change.
"""

import logging
import sqlite3
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from .control_plane.repository import atomic, record_audit
from .decisions import bump_version, current_version

logger = logging.getLogger(__name__)

OPEN_BLOCK = "decision = 'block' AND normal = 0"

# Events and AI judgments store microseconds, decisions store seconds; each cutoff uses
# its table's fixed-width format so text comparison matches time order.
_EVENT_TS = "%Y-%m-%dT%H:%M:%S.%fZ"
_DECISION_TS = "%Y-%m-%dT%H:%M:%SZ"


def _utc(value: datetime, fmt: str) -> str:
    return value.astimezone(timezone.utc).strftime(fmt)


def _actor_kind(actor: str | None) -> str:
    return "person" if actor else "system"


def purge_expired(conn: sqlite3.Connection, project_id: str, retention_days: int, now: datetime) -> dict:
    """Delete rows older than now - retention_days, except open blocks and their events.

    A row exactly at the cutoff is kept. The blocklist does not change, so its version is
    left alone. Writes a system audit row with counts only, and only when something went.
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    cutoff = now - timedelta(days=retention_days)
    event_cutoff = _utc(cutoff, _EVENT_TS)
    decision_cutoff = _utc(cutoff, _DECISION_TS)
    protected_events = (
        "SELECT de.event_id FROM decision_events de JOIN decisions d"
        " ON d.project_id = de.project_id AND d.id = de.decision_id"
        " WHERE de.project_id = :project_id AND d.decision = 'block' AND d.normal = 0"
    )
    expired_decisions = (
        f"SELECT id FROM decisions WHERE project_id = :project_id AND ts < :decision_cutoff AND NOT ({OPEN_BLOCK})"
    )
    params = {"project_id": project_id, "event_cutoff": event_cutoff, "decision_cutoff": decision_cutoff}
    counts = {}
    with atomic(conn):
        counts["decision_events"] = conn.execute(
            f"DELETE FROM decision_events WHERE project_id = :project_id AND decision_id IN ({expired_decisions})",
            params,
        ).rowcount
        counts["decisions"] = conn.execute(
            f"DELETE FROM decisions WHERE project_id = :project_id AND id IN ({expired_decisions})", params
        ).rowcount
        counts["events"] = conn.execute(
            "DELETE FROM events WHERE project_id = :project_id AND ts < :event_cutoff"
            f" AND id NOT IN ({protected_events})",
            params,
        ).rowcount
        counts["ai_judgments"] = conn.execute(
            "DELETE FROM ai_judgments WHERE project_id = :project_id AND ts < :event_cutoff"
            f" AND event_id NOT IN ({protected_events})",
            params,
        ).rowcount
        counts["user_contacts"] = conn.execute(
            "DELETE FROM user_contact WHERE project_id = :project_id AND updated_at < :event_cutoff", params
        ).rowcount
        # These rows have no timestamp: they go once nothing of their session is left.
        counts["rate_spike_judgments"] = conn.execute(
            "DELETE FROM rate_spike_judgments WHERE project_id = :project_id"
            " AND session_id NOT IN (SELECT session_id FROM events WHERE project_id = :project_id)"
            " AND session_id NOT IN (SELECT session_id FROM decisions WHERE project_id = :project_id)",
            params,
        ).rowcount
        if any(counts.values()):
            record_audit(
                conn,
                project_id=project_id,
                actor_person_id=None,
                actor_kind="system",
                action="retention.purge",
                target_type="project",
                target_id=project_id,
                now=now,
                meta={**counts, "retention_days": retention_days},
            )
    return counts


def delete_user_data(
    conn: sqlite3.Connection, project_id: str, user_id: str, now: datetime, actor: str | None
) -> dict:
    """Erase one end user's events, decisions and auxiliary rows in one project.

    Lifting an open block bumps the blocklist version. The audit target is the hashed
    user_id (decision 12), so the audit keeps no personal data after the erasure.
    """
    params = {"project_id": project_id, "user_id": user_id}
    user_decisions = "SELECT id FROM decisions WHERE project_id = :project_id AND user_id = :user_id"
    user_events = "SELECT id FROM events WHERE project_id = :project_id AND user_id = :user_id"
    with atomic(conn):
        lifts_block = (
            conn.execute(
                f"SELECT 1 FROM decisions WHERE project_id = :project_id AND user_id = :user_id AND {OPEN_BLOCK}",
                params,
            ).fetchone()
            is not None
        )
        conn.execute(
            "DELETE FROM rate_spike_judgments WHERE project_id = :project_id AND session_id IN ("
            "SELECT session_id FROM events WHERE project_id = :project_id AND user_id = :user_id"
            " UNION SELECT session_id FROM decisions WHERE project_id = :project_id AND user_id = :user_id)",
            params,
        )
        conn.execute(
            f"DELETE FROM decision_events WHERE project_id = :project_id AND decision_id IN ({user_decisions})",
            params,
        )
        conn.execute(
            f"DELETE FROM ai_judgments WHERE project_id = :project_id AND event_id IN ({user_events})", params
        )
        events = conn.execute(
            "DELETE FROM events WHERE project_id = :project_id AND user_id = :user_id", params
        ).rowcount
        decisions = conn.execute(
            "DELETE FROM decisions WHERE project_id = :project_id AND user_id = :user_id", params
        ).rowcount
        conn.execute("DELETE FROM user_contact WHERE project_id = :project_id AND user_id = :user_id", params)
        if lifts_block:
            bump_version(conn, project_id)
        deleted = {"events": events, "decisions": decisions}
        record_audit(
            conn,
            project_id=project_id,
            actor_person_id=actor,
            actor_kind=_actor_kind(actor),
            action="user_data.delete",
            target_type="user",
            target_id=user_id,
            now=now,
            meta={**deleted, "blocks_lifted": lifts_block},
        )
        version = current_version(conn, project_id)
    return {"deleted": deleted, "version": version}


def delete_project_data(conn: sqlite3.Connection, project_id: str, now: datetime, actor: str | None) -> dict:
    """Wipe a project's data rows, keeping the project and control-plane rows.

    Replaces the rehearsal reset. The blocklist ends empty and its version moves forward,
    never back to 0, so a middleware polling with an older version gets the empty list.
    """
    with atomic(conn):
        deleted = {}
        for table in ("decision_events", "ai_judgments", "rate_spike_judgments", "user_contact", "events", "decisions"):
            rowcount = conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,)).rowcount
            if table in ("events", "decisions"):
                deleted[table] = rowcount
        bump_version(conn, project_id)
        record_audit(
            conn,
            project_id=project_id,
            actor_person_id=actor,
            actor_kind=_actor_kind(actor),
            action="project_data.delete",
            target_type="project",
            target_id=project_id,
            now=now,
            meta=deleted,
        )
        version = current_version(conn, project_id)
    return {"deleted": {"events": deleted["events"], "decisions": deleted["decisions"]}, "version": version}


def run_purge_once(
    conn_factory: Callable[[], AbstractContextManager[sqlite3.Connection]],
    projects: Iterable[tuple[str, int]],
    now: datetime,
) -> dict:
    """Purge each (project_id, retention_days) in its own connection and transaction.

    One failing project is logged and reported as its exception; the rest still run.
    conn_factory must commit on success, like db.connect.
    """
    results: dict = {}
    for project_id, retention_days in projects:
        try:
            with conn_factory() as conn:
                results[project_id] = purge_expired(conn, project_id, retention_days, now)
        except Exception as exc:  # a background loop must not die on one project
            logger.exception("retention purge failed for project %s", project_id)
            results[project_id] = exc
    return results
