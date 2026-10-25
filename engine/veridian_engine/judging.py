"""Turn AI judgments of deviations into decisions. Runs after ingestion has answered."""

import logging
import sqlite3
import threading

from . import db
from .decisions import bump_version, create_decision, session_has_block
from .rules import Deviation
from .settings import Settings

logger = logging.getLogger(__name__)


def latest_per_session(deviations: list[Deviation]) -> list[Deviation]:
    """One deviation per session: the latest, which carries the longest path (and any block)."""
    latest: dict[tuple[str, str], Deviation] = {}
    for deviation in deviations:
        latest[(deviation.client_id, deviation.event.session_id)] = deviation
    return list(latest.values())


def judge_deviations(settings: Settings, write_lock: threading.Lock, deviations: list[Deviation]) -> None:
    """Ask the judge about each deviation and record the result. Never raises."""
    for deviation in deviations:
        try:
            judgment = settings.judge(deviation.signals, deviation.profile)
            with write_lock, db.connect(settings.db_path) as conn:
                apply_judgment(conn, settings, deviation, judgment.decision, judgment.rationale)
        except Exception:  # noqa: BLE001 - a judge failure must never break ingestion
            logger.exception("AI judgment failed for session %s", deviation.event.session_id)


def apply_judgment(
    conn: sqlite3.Connection, settings: Settings, deviation: Deviation, decision: str, rationale: str
) -> str | None:
    """Record an AI decision. Return the id of the decision created or updated, if any."""
    event = deviation.event
    if deviation.block_id is not None:
        # The critical rule already blocked: the AI only explains it.
        if decision != "block":
            return None
        conn.execute(
            "UPDATE decisions SET rationale = ? WHERE client_id = ? AND id = ?",
            (rationale, deviation.client_id, deviation.block_id),
        )
        return deviation.block_id

    if session_has_block(conn, deviation.client_id, event.session_id):
        return None
    already = conn.execute(
        "SELECT 1 FROM decisions WHERE client_id = ? AND session_id = ? AND decision = ?",
        (deviation.client_id, event.session_id, decision),
    ).fetchone()
    if already is not None:
        return None

    decision_id = create_decision(
        conn,
        deviation.client_id,
        ts=settings.clock(),
        user_id=event.user_id,
        session_id=event.session_id,
        ip=str(event.ip),
        decision=decision,
        signals=deviation.signals,
        rationale=rationale,
        event_ids=[event.id],
    )
    if decision == "block":
        bump_version(conn, deviation.client_id)
    return decision_id
