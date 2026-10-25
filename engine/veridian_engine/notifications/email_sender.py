"""Background worker that drains the `kind = 'email'` outbox rows (U17).

Same loop, retries and failure isolation as the webhook sender; only what is sent differs.
One row is one recipient. The preference is checked again at send time: an address that
left the organization, lost owner/admin or turned `notify_email` off is not mailed.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from urllib.parse import quote

from ..decisions import triggering_access
from ..mailer import NullMailer, build_block_alert, select_recipients
from .outbox import notified_members
from .sender import WebhookSender
from .webhook import FAILED, DeliveryResult, Notification

NO_PROVIDER = "no email provider configured"


class EmailSender(WebhookSender):
    KIND = "email"
    THREAD_NAME = "veridian-email"

    def __init__(self, db_path: str, *, mailer, **kwargs):
        # The webhook transport is never used here.
        super().__init__(db_path, transport=None, **kwargs)
        self._mailer = mailer

    def _prepare(self, conn, notification: Notification) -> Notification | tuple[str, str, str]:
        def failed(reason: str) -> Notification:
            return replace(notification, status=FAILED, next_attempt_at=None, last_error=reason)

        if isinstance(self._mailer, NullMailer):
            return failed(NO_PROVIDER)
        recipient = notification.recipient
        if not recipient or recipient not in select_recipients(notified_members(conn, notification.project_id)):
            return failed("recipient no longer notified")
        decision = conn.execute(
            "SELECT * FROM decisions WHERE project_id = ? AND id = ?",
            (notification.project_id, notification.decision_id),
        ).fetchone()
        if decision is None:
            return failed("decision no longer exists")
        project = conn.execute("SELECT name FROM project WHERE id = ?", (notification.project_id,)).fetchone()
        access = triggering_access(conn, notification.project_id, notification.decision_id)
        subject, text = build_block_alert(
            project_name=project["name"] if project else notification.project_id,
            decision={**dict(decision), "route": f"{access['method']} {access['route']}" if access else ""},
            panel_url=f"{self._panel_base_url.rstrip('/')}/decisiones/{quote(str(decision['id']), safe='')}",
            enforced=notification.enforced,
        )
        return (recipient, subject, text)

    def _attempt(self, notification: Notification, prepared: tuple) -> DeliveryResult:
        recipient, subject, text = prepared
        # A provider error raises here and counts as a failed attempt (process_due).
        self._mailer.send(recipient, subject, text)
        return DeliveryResult(delivered=True)


def build_email_sender(settings, write_lock: threading.Lock | None = None) -> EmailSender | None:
    """The app's email sender, or None when notifications are off (`Settings.webhooks`)."""
    if not settings.webhooks:
        return None
    return EmailSender(
        settings.db_path,
        mailer=settings.mailer or NullMailer(),
        clock=settings.clock,
        panel_base_url=settings.panel_base_url,
        poll_interval_s=settings.webhook_poll_s,
        write_lock=write_lock,
    )
