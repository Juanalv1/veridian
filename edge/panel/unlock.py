"""The public unlock page (docs/DESBLOQUEO-POR-CORREO.md): `/desbloquear/{token}`, outside the login.

The end user arrives from an email with no session; the token is their proof. The page talks to the engine with
the panel's service credential and the token in a header (a URL would put it in the engine's access logs).

- `GET` only reads (`GET /v1/self-unlock`): mail scanners open links, so it must never unlock.
- The button `POST`s. A form nonce, bound to the token with the session secret, is the CSRF defence: only a page
  this panel rendered for that token carries it. The Origin is checked too, but `Origin: null` passes, because
  `Referrer-Policy: no-referrer` (needed so the token does not leak to the logo host) makes browsers send it.
- Brand values come from the customer: escaped by the template, and the colour, the logo and the support link
  are also checked by shape here before they reach an attribute.
- The token is never logged by the panel; `UnlockPathFilter` also hides it from uvicorn's access log.
"""

import hashlib
import hmac
import logging
import re
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import Settings

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
EMAIL_RE = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")
PATH_RE = re.compile(r"/desbloquear/[^\s?#\"']+")
NAME_MAX = 80
URL_MAX = 2048

# state -> (title, text). `valid` is the question; the others explain why there is nothing to confirm.
MESSAGES = {
    "valid": ("¿Fuiste tú?", "Bloqueamos un acceso a tu cuenta de {name} porque no se parecía a tu uso habitual."),
    "used": ("Este enlace ya se usó",
             "Ya confirmaste este acceso con este enlace. Si todavía no puedes entrar, vuelve a la app e inténtalo otra vez."),
    "closed": ("Este acceso ya no está bloqueado",
               "Ya no hace falta confirmarlo: el acceso se liberó por otra vía. Vuelve a la app."),
    "expired": ("El enlace venció",
                "Los enlaces duran 24 horas. Vuelve a la app e inténtalo otra vez; si te bloquea de nuevo, te enviaremos un correo nuevo."),
}
NOT_FOUND = ("No encontramos este enlace", "Puede que esté incompleto o que ya no exista. Abre el enlace del correo más reciente que recibiste.")
UNAVAILABLE = ("No pudimos verificar el enlace ahora", "Inténtalo de nuevo en unos minutos. Tu cuenta sigue protegida.")
REFUSED = ("No pudimos confirmar la solicitud", "Abre el enlace del correo otra vez y vuelve a pulsar el botón.")
DONE = ("Listo, ya puedes volver a la app", "Confirmamos que fuiste tú en {name}. Ya puedes volver a la app e intentarlo de nuevo.")
FALLBACK_NAME = "tu cuenta"


class UnlockPathFilter(logging.Filter):
    """Hides the token of `/desbloquear/<token>` in access log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(PATH_RE.sub("/desbloquear/<redacted>", a) if isinstance(a, str) else a
                                for a in record.args)
        if isinstance(record.msg, str):
            record.msg = PATH_RE.sub("/desbloquear/<redacted>", record.msg)
        return True


def _https(value) -> str | None:
    if not isinstance(value, str) or len(value) > URL_MAX or not value.startswith("https://"):
        return None
    if re.search(r"[\s<>\"'\\]", value):
        return None
    try:
        return value if urlparse(value).hostname else None
    except ValueError:
        return None


def clean_brand(brand) -> dict:
    """The brand the template may use: text trimmed, links https only, colour #RRGGBB, else dropped."""
    brand = brand if isinstance(brand, dict) else {}
    name = brand.get("name")
    name = name.strip()[:NAME_MAX] if isinstance(name, str) and name.strip() else FALLBACK_NAME
    color = brand.get("color") if isinstance(brand.get("color"), str) and COLOR_RE.match(brand["color"]) else None
    email = brand.get("support_email")
    return {"name": name, "logo_url": _https(brand.get("logo_url")), "color": color,
            "on_color": _text_on(color) if color else None, "support_url": _https(brand.get("support_url")),
            "support_email": email if isinstance(email, str) and len(email) <= 254 and EMAIL_RE.match(email) else None}


def _text_on(color: str) -> str:
    """Black or white, whichever reads better on `color` (WCAG relative luminance)."""
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return "#0f1513" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 else "#ffffff"


class SelfUnlock:
    def __init__(self, settings: Settings, bearer: str, client: httpx.AsyncClient, templates: Jinja2Templates) -> None:
        self.settings, self.bearer, self.client, self.templates = settings, bearer, client, templates

    def nonce(self, token: str) -> str:
        return hmac.new(self.settings.session_secret.encode(), f"unlock-form:{token}".encode(), hashlib.sha256).hexdigest()

    def origin_ok(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is None or origin == "null":
            return True
        if self.settings.public_url:
            return origin.rstrip("/") == self.settings.public_url
        return urlparse(origin).netloc == request.headers.get("host", "")

    async def engine(self, method: str, token: str) -> tuple[int | None, dict | None]:
        """(status, json) of the engine's answer; (None, None) when it cannot be reached. Never logs the token."""
        headers = {"Authorization": f"Bearer {self.bearer}", "X-Veridian-Unlock-Token": token}
        try:
            r = await self.client.request(method, f"{self.settings.engine_url}/v1/self-unlock", headers=headers)
        except httpx.HTTPError:
            return None, None
        try:
            body = r.json()
        except ValueError:
            body = None
        return r.status_code, body if isinstance(body, dict) else None

    def page(self, request: Request, kind: str, copy: tuple[str, str], *, brand: dict | None = None,
             access: dict | None = None, nonce: str | None = None, status: int = 200):
        brand = clean_brand(brand) if brand is not None else None
        title, body = copy
        shown = {k: (v.strip() if isinstance(v, str) else None) for k, v in (access or {}).items() if k in ("time", "device")}
        response = self.templates.TemplateResponse(request, "desbloquear.html", {
            "kind": kind, "brand": brand, "title": title, "text": body.format(name=brand["name"] if brand else FALLBACK_NAME),
            "access": shown, "nonce": nonce}, status_code=status)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def unavailable(self, request: Request):
        return self.page(request, "unavailable", UNAVAILABLE, status=503)

    def missing(self, request: Request):
        return self.page(request, "missing", NOT_FOUND, status=404)

    async def show(self, request: Request, token: str, status: int = 200):
        code, body = await self.engine("GET", token)
        if code is None or code >= 500:
            return self.unavailable(request)
        if code == 404 or body is None or code != 200:
            return self.missing(request) if code == 404 else self.unavailable(request)
        state = body.get("state")
        if state not in MESSAGES:
            return self.unavailable(request)
        valid = state == "valid"
        return self.page(request, state, MESSAGES[state], brand=body.get("brand"), access=body.get("access"),
                         nonce=self.nonce(token) if valid else None, status=status)

    def router(self) -> APIRouter:
        router, me = APIRouter(), self

        @router.get("/desbloquear/{token}", response_class=HTMLResponse)
        async def view(request: Request, token: str):
            if not TOKEN_RE.match(token):
                return me.missing(request)
            return await me.show(request, token)

        @router.post("/desbloquear/{token}", response_class=HTMLResponse)
        async def confirm(request: Request, token: str, nonce: str = Form("")):
            if not TOKEN_RE.match(token):
                return me.missing(request)
            if not me.origin_ok(request) or not hmac.compare_digest(nonce.encode(), me.nonce(token).encode()):
                return me.page(request, "refused", REFUSED, status=403)
            code, body = await me.engine("POST", token)
            if code == 200 and body is not None and body.get("unlocked") is True:
                return me.page(request, "done", DONE, brand=body.get("brand"))
            if code == 404:
                return me.missing(request)
            if code == 409:
                return await me.show(request, token, status=409)  # the state page says why nothing changed
            return me.unavailable(request)

        return router
