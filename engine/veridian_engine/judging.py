"""Turn AI judgments of deviations into decisions. Runs after ingestion has answered."""

import logging
import sqlite3
import threading

from . import db
from .decisions import bump_version, create_decision, session_has_block
from .events import format_ts
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
            with write_lock, db.connect(settings.db_path) as conn:
                if not needs_judgment(conn, deviation):
                    continue
                if deviation.signals.get("rate_spike"):
                    # Marked before the call, so a concurrent batch does not judge the same spike.
                    conn.execute(
                        "INSERT OR IGNORE INTO rate_spike_judgments (client_id, session_id) VALUES (?, ?)",
                        (deviation.client_id, deviation.event.session_id),
                    )
                # One row per real model call, for the panel's reviewed_by_ai count.
                conn.execute(
                    "INSERT INTO ai_judgments (client_id, event_id, ts) VALUES (?, ?, ?)",
                    (deviation.client_id, deviation.event.id, format_ts(deviation.event.ts)),
                )
            judgment = settings.judge(deviation.signals, deviation.profile)
            with write_lock, db.connect(settings.db_path) as conn:
                apply_judgment(conn, settings, deviation, judgment.decision, judgment.rationale)
        except Exception:  # noqa: BLE001 - a judge failure must never break ingestion
            logger.exception("AI judgment failed for session %s", deviation.event.session_id)


def needs_judgment(conn: sqlite3.Connection, deviation: Deviation) -> bool:
    """Ask the AI once per session; a rule block, a never-used risky route or the session's
    first rate spike asks again.

    Batches arrive every ~2 s, so re-judging the same deviation would multiply model calls
    and give a non-deterministic judge many chances to flip a legitimate session.
    """
    if deviation.block_id is not None:
        return True
    signals = deviation.signals
    if signals.get("route_never_used") and signals.get("route_class") in ("critical", "sensitive"):
        return True
    if signals.get("rate_spike"):
        spike_judged = conn.execute(
            "SELECT 1 FROM rate_spike_judgments WHERE client_id = ? AND session_id = ?",
            (deviation.client_id, deviation.event.session_id),
        ).fetchone()
        if spike_judged is None:
            return True
    judged = conn.execute(
        "SELECT 1 FROM decisions WHERE client_id = ? AND session_id = ?",
        (deviation.client_id, deviation.event.session_id),
    ).fetchone()
    return judged is None


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

    # On a known network the owner shares the IP: blocking it would lock the victim out,
    # so only the session is blocked. An empty ip stays out of the blocklist.
    ip = str(event.ip) if deviation.signals.get("new_network") else ""
    decision_id = create_decision(
        conn,
        deviation.client_id,
        ts=settings.clock(),
        user_id=event.user_id,
        session_id=event.session_id,
        ip=ip,
        decision=decision,
        signals=deviation.signals,
        rationale=rationale,
        event_ids=[event.id],
    )
    if decision == "block":
        bump_version(conn, deviation.client_id)
    return decision_id
