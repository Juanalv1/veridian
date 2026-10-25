"""Synchronous check of a critical route before the app executes it (contract 9)."""

import logging
import sqlite3
import threading
from dataclasses import dataclass

from pydantic import BaseModel

from . import db
from .ai.judge import _critical_rule_applies, _fallback
from .events import Event, insert_events
from .judging import apply_judgment, needs_judgment, reserve_judgment
from .route_classes import RouteClasses
from .rules import Deviation, evaluate_event
from .settings import Settings

logger = logging.getLogger(__name__)

# The middleware never asks for a second factor: a step_up stops the request like a block.
BLOCKING_DECISIONS = ("block", "step_up")


class CheckRequest(BaseModel):
    event: Event


@dataclass
class CheckResult:
    verdict: str
    decision_id: str | None = None
    # A rule block whose rationale the judge rewrites after the response, as in contract 1.
    explain_later: Deviation | None = None

    def body(self) -> dict:
        return {"verdict": self.verdict, "decision_id": self.decision_id}


ALLOW = CheckResult("allow")


def check_event(
    settings: Settings, route_classes: RouteClasses, write_lock: threading.Lock, client_id: str, event: Event
) -> CheckResult:
    """Decide one critical request now. Never raises: any internal error allows it (fail-open)."""
    try:
        result = _check(settings, route_classes, write_lock, client_id, event)
        if result.verdict == "block":
            # The middleware reported the request before running it; a block means the app never
            # did, so the stored event says 403, as the panel and the activity feed should show.
            with write_lock, db.connect(settings.db_path) as conn:
                conn.execute("UPDATE events SET status = 403 WHERE client_id = ? AND id = ?", (client_id, event.id))
        return result
    except Exception:  # noqa: BLE001 - a Veridian failure must never take the customer's app down
        logger.exception("Synchronous check failed for session %s; allowing", event.session_id)
        return ALLOW


def _check(
    settings: Settings, route_classes: RouteClasses, write_lock: threading.Lock, client_id: str, event: Event
) -> CheckResult:
    with write_lock, db.connect(settings.db_path) as conn:
        if not insert_events(conn, client_id, [event]):
            return current_verdict(conn, client_id, event)  # already seen: never evaluated twice
        deviation = evaluate_event(conn, settings, route_classes, client_id, event)
        if deviation is None:
            return current_verdict(conn, client_id, event)
        if deviation.block_id is not None:
            explain = deviation if settings.judge is not None else None
            return CheckResult("block", deviation.block_id, explain_later=explain)
        if settings.judge is None or not needs_judgment(conn, deviation):
            return current_verdict(conn, client_id, event)
        reserve_judgment(conn, deviation)

    # Outside the lock: ingestion and other checks keep running while the judge thinks.
    judgment = judge_within_budget(settings, deviation)
    decision = "block" if judgment.decision in BLOCKING_DECISIONS else judgment.decision
    with write_lock, db.connect(settings.db_path) as conn:
        decision_id = apply_judgment(conn, settings, deviation, decision, judgment.rationale)
        if decision == "block" and decision_id is not None:
            return CheckResult("block", decision_id)
        # An existing block on the session or IP still wins over the judge's allow.
        listed = current_verdict(conn, client_id, event)
    return listed if listed.verdict == "block" else CheckResult("allow", decision_id)


def judge_within_budget(settings: Settings, deviation: Deviation):
    """Call the judge in a worker thread; past the budget or on error, use its fallback."""
    outcome: dict = {}

    def run() -> None:
        try:
            judge = settings.sync_judge or settings.judge
            outcome["judgment"] = judge(deviation.signals, deviation.profile)
        except Exception:  # noqa: BLE001 - reported below as a fallback
            logger.exception("AI judge failed in synchronous check")

    # Daemon: a judge that outlives the budget is abandoned, never awaited at shutdown.
    worker = threading.Thread(target=run, name="veridian-sync-judge", daemon=True)
    worker.start()
    worker.join(settings.sync_judge_timeout_s)
    if "judgment" in outcome:
        return outcome["judgment"]
    if worker.is_alive():
        logger.warning("AI judge exceeded %.1f s in synchronous check", settings.sync_judge_timeout_s)
    return _fallback(_critical_rule_applies(deviation.signals))


def current_verdict(conn: sqlite3.Connection, client_id: str, event: Event) -> CheckResult:
    """Block when the session or IP is in the current blocklist (contract 2), else allow."""
    row = conn.execute(
        "SELECT id FROM decisions WHERE client_id = ? AND decision = 'block' AND normal = 0"
        " AND (session_id = ? OR (ip != '' AND ip = ?)) ORDER BY seq DESC LIMIT 1",
        (client_id, event.session_id, str(event.ip)),
    ).fetchone()
    return CheckResult("block", row["id"]) if row is not None else ALLOW
