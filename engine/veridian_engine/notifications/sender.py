"""Background worker that drains the webhook outbox (docs/DISENO-PRODUCTO.md, "Notificaciones de bloqueo").

One daemon thread in the engine process. Each pass reads the due rows, sends them with no
database connection open, then records the outcomes. Nothing here can raise into a request:
ingestion and POST /v1/check only insert outbox rows, and every error in a pass is logged
and swallowed. Delivery is at least once (a crash between sending and saving resends).
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace

from .. import db
from ..decisions import triggering_access
from .outbox import due_notifications, parse_ts, save_notification
from .secret_box import decrypt_secret
from .webhook import (
    FAILED,
    HttpxTransport,
    RETRY_DELAYS,
    Clock,
    DeliveryResult,
    Notification,
    Resolver,
    Transport,
    build_block_payload,
    default_resolver,
    deliver,
    process_due,
)

log = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_BATCH_SIZE = 50


class WebhookSender:
    def __init__(
        self,
        db_path: str,
        *,
        transport: Transport,
        clock: Clock,
        resolver: Resolver = default_resolver,
        panel_base_url: str = "",
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        batch_size: int = DEFAULT_BATCH_SIZE,
        delays: tuple[int, ...] = RETRY_DELAYS,
        write_lock: threading.Lock | None = None,
    ):
        self._db_path = db_path
        self._transport = transport
        self._clock = clock
        self._resolver = resolver
        self._panel_base_url = panel_base_url
        self._poll_interval_s = poll_interval_s
        self._batch_size = batch_size
        self._delays = delays
        self._write_lock = write_lock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle ---

    def start(self) -> None:
        if self.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="veridian-webhooks", daemon=True)
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

    # --- one pass ---

    def run_once(self) -> int:
        """Send every due notification once. Return how many were attempted. Never raises."""
        try:
            return self._run_once()
        except Exception:  # noqa: BLE001 - the worker must survive any error
            log.exception("webhook sender pass failed")
            return 0

    def _run_once(self) -> int:
        now = self._clock()
        with db.connect(self._db_path) as conn:
            due = due_notifications(conn, now, self._batch_size)
            prepared = {notification.id: self._prepare(conn, notification) for notification in due}

        terminal = [p for p in prepared.values() if isinstance(p, Notification)]
        sendable = [n for n in due if not isinstance(prepared[n.id], Notification)]
        attempted = process_due(sendable, now, lambda n: self._attempt(n, prepared[n.id]), self._delays)

        with self._write_lock or nullcontext(), db.connect(self._db_path) as conn:
            for notification in [*terminal, *attempted]:
                save_notification(conn, notification, now)
        return len(attempted)

    def _prepare(self, conn, notification: Notification) -> Notification | tuple[str, str, dict]:
        """What to send for one row, read while the connection is open. A row that can never
        be sent (endpoint removed or disabled, decision deleted) comes back already failed."""
        endpoint = conn.execute(
            "SELECT url, secret_enc FROM webhook_endpoint WHERE project_id = ? AND enabled = 1",
            (notification.project_id,),
        ).fetchone()
        if endpoint is None:
            return replace(notification, status=FAILED, next_attempt_at=None, last_error="webhook endpoint removed")
        decision = conn.execute(
            "SELECT * FROM decisions WHERE project_id = ? AND id = ?",
            (notification.project_id, notification.decision_id),
        ).fetchone()
        if decision is None:
            return replace(notification, status=FAILED, next_attempt_at=None, last_error="decision no longer exists")
        project = conn.execute("SELECT name FROM project WHERE id = ?", (notification.project_id,)).fetchone()
        created_at = conn.execute(
            "SELECT created_at FROM notifications WHERE id = ?", (notification.id,)
        ).fetchone()["created_at"]
        access = triggering_access(conn, notification.project_id, notification.decision_id)
        route = f"{access['method']} {access['route']}" if access else None
        payload = build_block_payload(
            notification_id=notification.id,
            # The enqueue time, so every retry carries the same body.
            created_at=parse_ts(created_at),
            project_id=notification.project_id,
            project_name=project["name"] if project else notification.project_id,
            decision=dict(decision),
            route=route,
            panel_base_url=self._panel_base_url,
            enforced=notification.enforced,
        )
        return (endpoint["url"], endpoint["secret_enc"], payload)

    def _attempt(self, notification: Notification, prepared: tuple) -> DeliveryResult:
        url, secret_enc, payload = prepared
        # A decryption error (missing or rotated key) raises here and counts as a failed attempt.
        secret = decrypt_secret(secret_enc)
        return deliver(
            url,
            secret,
            payload,
            delivery_id=notification.id,
            transport=self._transport,
            clock=self._clock,
            resolver=self._resolver,
        )


def build_webhook_sender(settings, write_lock: threading.Lock | None = None) -> WebhookSender | None:
    """The app's sender, or None when webhooks are off (`Settings.webhooks`)."""
    if not settings.webhooks:
        return None
    return WebhookSender(
        settings.db_path,
        transport=settings.webhook_transport or HttpxTransport(),
        clock=settings.clock,
        resolver=settings.webhook_resolver or default_resolver,
        panel_base_url=settings.panel_base_url,
        poll_interval_s=settings.webhook_poll_s,
        write_lock=write_lock,
    )


@asynccontextmanager
async def webhook_lifespan(app):
    """FastAPI lifespan: the sender in `app.state.webhook_sender` runs while the app does."""
    sender = getattr(app.state, "webhook_sender", None)
    if sender is not None:
        sender.start()
    try:
        yield
    finally:
        if sender is not None:
            sender.stop()
