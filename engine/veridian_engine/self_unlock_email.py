"""The branded email to a blocked end user (docs/DESBLOQUEO-POR-CORREO.md), as text and HTML.

Pure functions: the brand, the access detail and the link come from the caller. Every brand value
is escaped for HTML. The IP of the access is never an input, so it cannot reach the message. The
message does not name Veridian: the end user knows the customer's product, not us.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from html import escape

SUBJECT = "Detectamos un acceso sospechoso a tu cuenta"
BUTTON = "Fui yo, desbloquear"
DEFAULT_COLOR = "#2563EB"
TOKEN_TTL_HOURS = 24

_MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre")
_BROWSERS = (("Edg", "Edge"), ("OPR", "Opera"), ("Firefox", "Firefox"), ("Chrome", "Chrome"), ("CriOS", "Chrome"),
             ("Safari", "Safari"))
_SYSTEMS = (("Windows", "Windows"), ("iPhone", "iOS"), ("iPad", "iOS"), ("Android", "Android"),
            ("Mac OS", "macOS"), ("Macintosh", "macOS"), ("CrOS", "ChromeOS"), ("Linux", "Linux"))


def describe_device(user_agent: str | None) -> str:
    """A coarse "Chrome en Windows" from a user agent; never the raw string."""
    ua = user_agent or ""
    browser = next((name for needle, name in _BROWSERS if needle in ua), None)
    system = next((name for needle, name in _SYSTEMS if re.search(needle, ua, re.IGNORECASE)), None)
    if browser and system:
        return f"{browser} en {system}"
    return browser or (f"Dispositivo {system}" if system else "Dispositivo desconocido")


def describe_time(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    return f"{value.day} de {_MONTHS[value.month - 1]} de {value.year}, {value:%H:%M} UTC"


def access_detail(ts: datetime | None, user_agent: str | None) -> dict:
    """What the user sees about the access: when and from which kind of device. No IP, no route."""
    return {"time": describe_time(ts) if ts else None, "device": describe_device(user_agent)}


def build_self_unlock_email(*, brand: Mapping, access: Mapping, link: str) -> tuple[str, str, str]:
    """(subject, text, html). `brand` carries name (required), logo_url, color, support_url, support_email."""
    name = brand["name"]
    color = brand.get("color") or DEFAULT_COLOR
    support = [part for part in (brand.get("support_email"), brand.get("support_url")) if part]
    details = [("Fecha y hora", access.get("time")), ("Dispositivo", access.get("device"))]
    details = [(label, value) for label, value in details if value]
    expiry = f"El enlace vence en {TOKEN_TTL_HOURS} horas y solo funciona una vez."
    ignore = "Si no fuiste tú, no hagas nada: ignora este correo y tu cuenta seguirá protegida."

    text_lines = [SUBJECT, "", f"Bloqueamos un acceso a tu cuenta de {name} porque no se parecía a tu uso habitual.", ""]
    text_lines += [f"{label}: {value}" for label, value in details]
    text_lines += ["", f"Si fuiste tú, confirma aquí ({BUTTON}):", link, "", expiry, ignore]
    if support:
        text_lines += ["", "¿Necesitas ayuda? " + " o ".join(support)]
    text = "\n".join(text_lines)

    rows = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#6b7280">{escape(label)}</td>'
        f'<td style="padding:4px 0;color:#111827">{escape(value)}</td></tr>'
        for label, value in details
    )
    logo = (
        f'<img src="{escape(brand["logo_url"], quote=True)}" alt="{escape(name, quote=True)}" height="40"'
        ' style="display:block;margin:0 0 12px;border:0;max-height:40px">'
        if brand.get("logo_url")
        else ""
    )
    support_html = ""
    if support:
        parts = []
        if brand.get("support_email"):
            parts.append(escape(brand["support_email"]))
        if brand.get("support_url"):
            url = escape(brand["support_url"], quote=True)
            parts.append(f'<a href="{url}" style="color:{escape(color, quote=True)}">{url}</a>')
        support_html = f'<p style="margin:24px 0 0;font-size:13px;color:#6b7280">¿Necesitas ayuda? {" o ".join(parts)}</p>'
    html = (
        '<!doctype html><html lang="es"><body style="margin:0;background:#f3f4f6;'
        'font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;'
        'background:#ffffff;border-radius:8px;padding:32px">'
        f'<tr><td>{logo}<p style="margin:0 0 8px;font-size:14px;color:#6b7280">{escape(name)}</p>'
        f'<h1 style="margin:0 0 16px;font-size:22px;color:#111827">{escape(SUBJECT)}</h1>'
        f'<p style="margin:0 0 16px;color:#374151">Bloqueamos un acceso a tu cuenta de {escape(name)} porque no se '
        'parecía a tu uso habitual.</p>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;font-size:14px">{rows}</table>'
        f'<a href="{escape(link, quote=True)}" style="display:inline-block;background:{escape(color, quote=True)};'
        f'color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:6px;font-weight:600">{escape(BUTTON)}</a>'
        f'<p style="margin:24px 0 0;font-size:13px;color:#6b7280">{escape(expiry)}<br>{escape(ignore)}</p>'
        f'{support_html}</td></tr></table></td></tr></table></body></html>'
    )
    return SUBJECT, text, html
