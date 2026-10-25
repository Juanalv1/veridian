"""Automatic route classification (contract 16, HANDOFF Dev B item 6).

The SDK reports the app's routes (method, route template, OpenAPI summary and description;
never code). New or changed ones are queued; a background worker classifies them with the AI
(`openapi.classify`) and writes the result into the project's `route_rules`, which
GET /v1/routes already serves to the middleware.

- An AI failure never touches the rules: the route keeps its last known class, or stays
  `normal` when it never had one, and is retried on the next report (user decision).
- The AI writes only routes without an `admin` (U7 PUT) or `template` (routes.yaml) rule:
  a person's correction always wins (user decision).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime

from pydantic import BaseModel, Field

from . import db
from .events import format_ts
from .openapi.classify import RouteClass, classify
from .openapi.extract import Operation

log = logging.getLogger(__name__)

PENDING, CLASSIFIED, FAILED = "pending", "classified", "failed"
# classify() never raises: it answers "normal" with one of these reasons when it could not decide.
FAILURE_PREFIXES = ("fallback:", "defaulted:")
PROTECTED_SOURCES = ("admin", "template")
BATCH = 200

Classifier = Callable[[list[Operation]], dict[str, RouteClass]]


class ReportedRoute(BaseModel):
    """One route of contract 16: what the OpenAPI document says, never code."""

    method: str = Field(pattern=r"^[A-Z]+$", max_length=10)
    route: str = Field(pattern=r"^/\S*$", max_length=300)
    summary: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=2000)


class RouteCatalogIn(BaseModel):
    routes: list[ReportedRoute] = Field(max_length=1000)


def fingerprint(method: str, route: str, summary: str, description: str) -> str:
    raw = json.dumps([method, route, summary, description], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_report(conn: sqlite3.Connection, project_id: str, routes: list[dict], now: datetime) -> int:
    """Store a report. New, changed and previously failed routes become pending; routes left
    out leave the catalog (their rules stay). Returns how many are pending now."""
    current = {
        (row["method"], row["route"]): row
        for row in conn.execute(
            "SELECT method, route, fingerprint, status FROM route_catalog WHERE project_id = ?", (project_id,)
        )
    }
    ts = format_ts(now)
    reported = set()
    for item in routes:
        key = (item["method"], item["route"])
        reported.add(key)
        new_print = fingerprint(item["method"], item["route"], item["summary"], item["description"])
        existing = current.get(key)
        if existing is not None and existing["fingerprint"] == new_print and existing["status"] != FAILED:
            continue
        conn.execute(
            "INSERT INTO route_catalog (project_id, method, route, summary, description, fingerprint, status,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)"
            " ON CONFLICT (project_id, method, route) DO UPDATE SET summary = excluded.summary,"
            " description = excluded.description, fingerprint = excluded.fingerprint, status = 'pending',"
            " updated_at = excluded.updated_at",
            (project_id, item["method"], item["route"], item["summary"], item["description"], new_print, ts),
        )
    for key in current.keys() - reported:
        conn.execute("DELETE FROM route_catalog WHERE project_id = ? AND method = ? AND route = ?", (project_id, *key))
    return conn.execute(
        "SELECT COUNT(*) FROM route_catalog WHERE project_id = ? AND status = 'pending'", (project_id,)
    ).fetchone()[0]


def protect_unlisted(conn: sqlite3.Connection, project_id: str) -> None:
    """After an admin replaced the rules: every catalog route the admin left out is `normal`
    by the admin's choice, so the AI does not bring it back. The caller owns the transaction."""
    conn.execute(
        "INSERT INTO route_rules (project_id, method, route, class, source)"
        " SELECT c.project_id, c.method, c.route, 'normal', 'admin' FROM route_catalog c"
        " WHERE c.project_id = ? AND NOT EXISTS (SELECT 1 FROM route_rules r WHERE r.project_id = c.project_id"
        " AND r.method = c.method AND r.route = c.route)",
        (project_id,),
    )


def list_catalog(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """What the panel shows: each reported route with its effective class and who set it."""
    rows = conn.execute(
        "SELECT c.method, c.route, c.summary, c.status, c.ai_class, c.ai_reason, c.last_error, c.classified_at,"
        " r.class AS rule_class, r.source FROM route_catalog c LEFT JOIN route_rules r"
        " ON r.project_id = c.project_id AND r.method = c.method AND r.route = c.route"
        " WHERE c.project_id = ? ORDER BY c.route, c.method",
        (project_id,),
    ).fetchall()
    return [
        {
            "method": row["method"],
            "route": row["route"],
            "summary": row["summary"],
            "status": row["status"],
            "class": row["rule_class"] or "normal",
            "source": row["source"],
            "ai_class": row["ai_class"],
            "ai_reason": row["ai_reason"],
            "last_error": row["last_error"],
            "classified_at": row["classified_at"],
        }
        for row in rows
    ]


def _pending(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    by_project: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(
        "SELECT * FROM route_catalog WHERE status = 'pending' ORDER BY project_id, route, method LIMIT ?", (BATCH,)
    ):
        by_project.setdefault(row["project_id"], []).append(row)
    return by_project


def _save(conn: sqlite3.Connection, row: sqlite3.Row, result: RouteClass | None, now: datetime) -> None:
    still = conn.execute(
        "SELECT fingerprint FROM route_catalog WHERE project_id = ? AND method = ? AND route = ?",
        (row["project_id"], row["method"], row["route"]),
    ).fetchone()
    if still is None or still["fingerprint"] != row["fingerprint"]:
        return  # reported again while the AI was answering: the new version is pending
    ts = format_ts(now)
    if result is None or result.reason.startswith(FAILURE_PREFIXES):
        conn.execute(
            "UPDATE route_catalog SET status = 'failed', last_error = ?, updated_at = ?"
            " WHERE project_id = ? AND method = ? AND route = ?",
            (result.reason if result else "missing from classifier output", ts,
             row["project_id"], row["method"], row["route"]),
        )
        return
    conn.execute(
        "UPDATE route_catalog SET status = 'classified', ai_class = ?, ai_reason = ?, last_error = NULL,"
        " classified_at = ?, updated_at = ? WHERE project_id = ? AND method = ? AND route = ?",
        (result.route_class, result.reason, ts, ts, row["project_id"], row["method"], row["route"]),
    )
    conn.execute(
        "INSERT INTO route_rules (project_id, method, route, class, source) VALUES (?, ?, ?, ?, 'ai')"
        " ON CONFLICT (project_id, method, route) DO UPDATE SET class = excluded.class"
        " WHERE route_rules.source NOT IN ('admin', 'template')",
        (row["project_id"], row["method"], row["route"], result.route_class),
    )


class RouteClassifier:
    """Daemon thread that classifies pending catalog routes. Never raises into a request."""

    def __init__(
        self,
        db_path: str,
        *,
        clock: Callable[[], datetime],
        classify_fn: Classifier = classify,
        poll_interval_s: float = 5.0,
        write_lock: threading.Lock | None = None,
    ):
        self._db_path = db_path
        self._clock = clock
        self._classify = classify_fn
        self._poll_interval_s = poll_interval_s
        self._write_lock = write_lock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="veridian-routes", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self._poll_interval_s)

    def run_once(self) -> int:
        """Classify the pending routes once. Return how many were sent to the AI. Never raises."""
        try:
            return self._run_once()
        except Exception:  # noqa: BLE001 - the worker must survive any error
            log.exception("route classification pass failed")
            return 0

    def _run_once(self) -> int:
        with db.connect(self._db_path) as conn:
            pending = _pending(conn)
        attempted = 0
        for rows in pending.values():
            operations = [
                Operation(method=r["method"], path=r["route"], summary=r["summary"], description=r["description"])
                for r in rows
            ]
            # No connection or lock is held while the AI answers.
            try:
                results = self._classify(operations)
            except Exception as exc:  # noqa: BLE001 - a custom classifier may raise; count it as a failure
                results = {op.key: RouteClass("normal", f"fallback: classifier raised ({type(exc).__name__})")
                           for op in operations}
            now = self._clock()
            with self._write_lock or nullcontext(), db.connect(self._db_path) as conn:
                for row, op in zip(rows, operations):
                    _save(conn, row, results.get(op.key), now)
            attempted += len(rows)
        return attempted


def build_route_classifier(settings, write_lock: threading.Lock | None = None) -> RouteClassifier | None:
    """The app's worker, or None when automatic classification is off (`Settings.auto_classify`)."""
    if not settings.auto_classify:
        return None
    return RouteClassifier(
        settings.db_path,
        clock=settings.clock,
        classify_fn=settings.route_classifier or classify,
        poll_interval_s=settings.classify_poll_s,
        write_lock=write_lock,
    )
