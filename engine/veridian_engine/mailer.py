"""Block-alert email to a project's admins, behind a provider-agnostic Mailer.

The provider is an open decision, so only a null and a fake implementation exist.
Members and the decision are plain data; database wiring belongs to the caller.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

NOTIFIED_ROLES = frozenset({"owner", "admin"})


class Mailer(Protocol):
    def send(
        self, to: str, subject: str, text: str, html: str | None = None, from_name: str | None = None
    ) -> None:
        """`html` is an optional rich alternative to `text`; `from_name` the visible sender name
        (the address is the provider's configuration)."""
        ...


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None
    from_name: str | None = None


class NullMailer:
    """Accepts every message and discards it (no provider configured)."""

    def send(
        self, to: str, subject: str, text: str, html: str | None = None, from_name: str | None = None
    ) -> None:
        return None


@dataclass
class FakeMailer:
    """Records messages for tests; raises for addresses listed in `fail_for`."""

    fail_for: set[str] = field(default_factory=set)
    sent: list[EmailMessage] = field(default_factory=list)

    def send(
        self, to: str, subject: str, text: str, html: str | None = None, from_name: str | None = None
    ) -> None:
        if to in self.fail_for:
            raise RuntimeError(f"simulated delivery failure for {to}")
        self.sent.append(EmailMessage(to=to, subject=subject, text=text, html=html, from_name=from_name))


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def select_recipients(members: Iterable[Any]) -> list[str]:
    """Emails of owners and admins whose `notify_email` preference is on, in input order."""
    recipients: list[str] = []
    for member in members:
        email = _field(member, "email")
        if email and _field(member, "role") in NOTIFIED_ROLES and _field(member, "notify_email"):
            if email not in recipients:
                recipients.append(email)
    return recipients


def build_block_alert(
    *, project_name: str, decision: Mapping[str, Any], panel_url: str, enforced: bool = True
) -> tuple[str, str]:
    """Subject and plain-text body in Spanish. Never includes the IP or request bodies."""
    subject = f"[Veridian] Acceso bloqueado en {project_name}"
    observed = (
        []
        if enforced
        else ["El proyecto está en modo observe: el bloqueo se registró, pero no se aplicó.", ""]
    )
    text = "\n".join(
        [
            f"Veridian bloqueó un acceso en el proyecto {project_name}.",
            "",
            *observed,
            f"Usuario: {decision.get('user_id', '')}",
            f"Ruta: {decision.get('route', '')}",
            f"Hora (UTC): {decision.get('ts', '')}",
            f"Motivo: {decision.get('rationale', '')}",
            "",
            f"Ver el detalle en el panel: {panel_url}",
            "",
            "Recibes este correo porque eres owner o admin del proyecto."
            " Puedes desactivar estos avisos en tus preferencias del panel.",
        ]
    )
    return subject, text


def notify_admins_of_block(
    mailer: Mailer,
    members: Iterable[Any],
    *,
    project_name: str,
    decision: Mapping[str, Any],
    panel_url: str,
) -> int:
    """Send one alert per eligible admin; returns how many sends succeeded. Never raises on send failure."""
    if decision.get("decision", "block") != "block":
        return 0
    subject, text = build_block_alert(project_name=project_name, decision=decision, panel_url=panel_url)
    delivered = 0
    for recipient in select_recipients(members):
        try:
            mailer.send(recipient, subject, text)
        except Exception:
            logger.warning("block alert email failed for decision %s", decision.get("id"), exc_info=True)
            continue
        delivered += 1
    return delivered
