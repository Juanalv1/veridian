"""The real email provider: Resend's HTTP API (https://resend.com/docs/api-reference/emails/send-email).

Selected by `mailer_from_env` when both RESEND_API_KEY and VERIDIAN_MAIL_FROM are set; otherwise the
engine keeps the NullMailer. The key is read once, kept only in the Authorization header and never
logged, printed or put in an error message. Errors carry the HTTP status only: a response body can echo
the recipient's address.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

import httpx

from .mailer import Mailer, NullMailer

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_NAME = "Veridian"  # sender name of messages that bring none (the admin alerts)
TIMEOUT_S = 10.0
_ADDRESS = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$")


def _display_name(name: str | None) -> str:
    """The visible name, without anything that could end the name part of the `From` header."""
    cleaned = re.sub(r"[\r\n<>\",;]", " ", name or "")
    return re.sub(r"\s+", " ", cleaned).strip() or DEFAULT_NAME


class ResendMailer:
    def __init__(self, api_key: str, mail_from: str, *, client: httpx.Client | None = None):
        self._api_key = api_key
        self._mail_from = mail_from
        self._client = client or httpx.Client(timeout=TIMEOUT_S)

    def __repr__(self) -> str:
        return f"ResendMailer(mail_from={self._mail_from!r})"

    def send(
        self, to: str, subject: str, text: str, html: str | None = None, from_name: str | None = None
    ) -> None:
        payload = {
            "from": f"{_display_name(from_name)} <{self._mail_from}>",
            "to": [to],
            "subject": subject,
            "text": text,
        }
        if html:
            payload["html"] = html
        try:
            response = self._client.post(
                RESEND_URL, json=payload, headers={"Authorization": f"Bearer {self._api_key}"}
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"resend request failed: {type(exc).__name__}") from None
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"resend rejected the message: HTTP {response.status_code}")


def mailer_from_env(environ: Mapping[str, str]) -> Mailer:
    """ResendMailer when RESEND_API_KEY and a valid VERIDIAN_MAIL_FROM are set, else NullMailer."""
    api_key = environ.get("RESEND_API_KEY", "").strip()
    mail_from = environ.get("VERIDIAN_MAIL_FROM", "").strip()
    if not api_key or not mail_from:
        return NullMailer()
    if not _ADDRESS.match(mail_from):
        logger.warning("VERIDIAN_MAIL_FROM is not an email address; email stays off")
        return NullMailer()
    return ResendMailer(api_key, mail_from)
