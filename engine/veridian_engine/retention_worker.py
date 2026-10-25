"""U13: background thread that applies retention (retention.py) on a fixed interval.

Each pass purges every live project with its own `retention_days`, and wipes the data rows of
projects deleted with DELETE /v1/projects/{id} (only while rows remain, so it audits once).
The first pass runs one interval after start: starting the engine never deletes anything.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

from . import db
from .retention import delete_project_data, run_purge_once

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 3600.0


class RetentionWorker:
    def __init__(self, db_path: str, *, clock: Callable[[], datetime], interval_s: float = DEFAULT_INTERVAL_S,
                 write_lock: threading.Lock | None = None):
        self._db_path = db_path
        self._clock = clock
        self._interval_s = interval_s
        # The app's lock: a pass deletes rows, so it must not interleave with ingestion.
        self._write_lock = write_lock or threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.passes = 0

    def start(self) -> None:
        if self.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="veridian-retention", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            self.run_once()

    def run_once(self) -> dict:
        """One pass. Returns the purge result per live project; never raises."""
        try:
            with self._write_lock:
                return self._run_once()
        except Exception:  # noqa: BLE001 - the worker must survive any error
            log.exception("retention pass failed")
            return {}
        finally:
            self.passes += 1

    def _run_once(self) -> dict:
        now = self._clock()
        with db.connect(self._db_path) as conn:
            live = [(r[0], r[1]) for r in conn.execute("SELECT id, retention_days FROM project WHERE deleted_at IS NULL")]
            deleted = [r[0] for r in conn.execute(
                "SELECT p.id FROM project p WHERE p.deleted_at IS NOT NULL AND (EXISTS"
                " (SELECT 1 FROM events e WHERE e.project_id = p.id) OR EXISTS"
                " (SELECT 1 FROM decisions d WHERE d.project_id = p.id))"
            )]
            for project_id in deleted:
                delete_project_data(conn, project_id, now, actor=None)
        return run_purge_once(lambda: db.connect(self._db_path), live, now)


def build_retention_worker(settings, write_lock: threading.Lock | None = None) -> RetentionWorker | None:
    """The app's worker, or None when retention is off (`Settings.retention`)."""
    if not settings.retention:
        return None
    return RetentionWorker(settings.db_path, clock=settings.clock, interval_s=settings.retention_interval_s,
                           write_lock=write_lock)
