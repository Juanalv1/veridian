"""Background worker that drains the `kind = 'email'` outbox rows (U17).

Same loop, retries and failure isolation as the webhook sender; only what is sent differs.
One row is one recipient. The preference is checked again at send time: an address that
left the organization, lost owner/admin or turned `notify_email` off is not mailed.
"""

from __future__ import annotations

import threading
from contextlib import nullcontext
from dataclasses import replace
from urllib.parse import quote

from .. import db
from ..decisions import triggering_access
from ..mailer import NullMailer, build_block_alert, select_recipients
from ..self_unlock import PURPOSE as SELF_UNLOCK
from ..self_unlock import access_of, issue_token, project_self_unlock, user_email
from ..self_unlock_email import access_detail, build_self_unlock_email
from .outbox import notified_members, parse_ts
from .sender import WebhookSender
from .webhook import FAILED, DeliveryResult, Notification

NO_PROVIDER = "no email provider configured"
DEFAULT_PANEL_URL = "https://veridian-panel.onrender.com"


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
        if notification.purpose == SELF_UNLOCK:
            return self._prepare_self_unlock(conn, notification, failed)
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

    def _prepare_self_unlock(self, conn, notification: Notification, failed) -> Notification | tuple:
        """The branded email to the blocked user. Everything is checked again at send time: the feature
        may have been turned off, the decision marked normal or the address erased since it was queued."""
        project = project_self_unlock(conn, notification.project_id)
        if project is None:
            return failed("self-unlock is off")
        decision = conn.execute(
            "SELECT user_id, decision, normal FROM decisions WHERE project_id = ? AND id = ?",
            (notification.project_id, notification.decision_id),
        ).fetchone()
        if decision is None or decision["decision"] != "block" or decision["normal"]:
            return failed("decision no longer open")
        recipient = user_email(conn, notification.project_id, decision["user_id"])
        if not recipient:
            return failed("no user email")
        access = access_of(conn, notification.project_id, notification.decision_id)
        detail = access_detail(
            parse_ts(access["ts"]) if access else None, access["user_agent"] if access else None
        )
        brand = {
            "name": project["brand_name"] or project["name"],
            "logo_url": project["brand_logo_url"],
            "color": project["brand_color"],
            "support_url": project["brand_support_url"],
            "support_email": project["brand_support_email"],
        }
        return (recipient, brand, detail, decision["user_id"])

    def _attempt(self, notification: Notification, prepared: tuple) -> DeliveryResult:
        if notification.purpose == SELF_UNLOCK:
            return self._send_self_unlock(notification, prepared)
        recipient, subject, text = prepared
        # A provider error raises here and counts as a failed attempt (process_due).
        self._mailer.send(recipient, subject, text)
        return DeliveryResult(delivered=True)

    def _send_self_unlock(self, notification: Notification, prepared: tuple) -> DeliveryResult:
        recipient, brand, detail, user_id = prepared
        # The token is born here, so the plain value exists only in this email. A retry replaces it.
        with self._write_lock or nullcontext(), db.connect(self._db_path) as conn:
            token = issue_token(conn, notification_id=notification.id, project_id=notification.project_id,
                                decision_id=notification.decision_id, user_id=user_id, now=self._clock())
        base = (self._panel_base_url or DEFAULT_PANEL_URL).rstrip("/")
        subject, text, html = build_self_unlock_email(brand=brand, access=detail, link=f"{base}/desbloquear/{token}")
        self._mailer.send(recipient, subject, text, html=html, from_name=brand["name"])
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
