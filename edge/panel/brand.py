"""Brand and "Desbloqueo por correo" settings of the session project (docs/DESBLOQUEO-POR-CORREO.md).

Reads `GET /v1/projects/{id}` (any member) and writes `PATCH /v1/projects/{id}` (the engine decides: admin or
owner). A viewer sees the values read-only. Same rules as team.py: the project is always the session's, never a
form field; the engine checks the role on every write and the panel shows what it answers.
"""

import re
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from .team import Team, change_error

NAME_MAX = 80
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
EMAIL_RE = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")
FIELDS = ("brand_name", "brand_logo_url", "brand_color", "brand_support_url", "brand_support_email")

SAVED = "Marca guardada."
SAVED_ON = "Marca guardada. El desbloqueo por correo está activo."
NAME_ERROR = f"El nombre puede tener hasta {NAME_MAX} caracteres."
COLOR_ERROR = "El color tiene que ser un código como #2563EB."
LOGO_ERROR = "La URL del logo tiene que ser https y pública."
SUPPORT_URL_ERROR = "La URL de soporte tiene que ser https."
SUPPORT_EMAIL_ERROR = "Escribe un correo de soporte válido."
FAILED = "No se pudo guardar la marca. Inténtalo de nuevo."


def validate(values: dict) -> dict[str, str]:
    """Field -> message for what is wrong. The engine validates again; this only gives each field its own sentence."""
    errors = {}
    if len(values["brand_name"]) > NAME_MAX:
        errors["brand_name"] = NAME_ERROR
    if values["brand_color"] and not COLOR_RE.match(values["brand_color"]):
        errors["brand_color"] = COLOR_ERROR
    if values["brand_support_url"] and not values["brand_support_url"].lower().startswith("https://"):
        errors["brand_support_url"] = SUPPORT_URL_ERROR
    email = values["brand_support_email"]
    if email and (len(email) > 254 or not EMAIL_RE.match(email)):
        errors["brand_support_email"] = SUPPORT_EMAIL_ERROR
    return errors


class Brand:
    def __init__(self, team: Team) -> None:
        self.t = team

    async def page(self, request: Request, ctx: dict, *, values: dict | None = None, errors: dict | None = None,
                   status: int = 200):
        project = quote(ctx["project"]["id"], safe="")
        response = await self.t.fetch(request, f"/v1/projects/{project}")
        try:
            current = response.json() if response is not None and response.status_code == 200 else None
        except ValueError:
            current = None
        if not isinstance(current, dict):
            return self.t.no_store(await self.t.unreachable(request, "marca"))
        shown = values if values is not None else {
            **{f: current.get(f) or "" for f in FIELDS}, "self_unlock_enabled": current.get("self_unlock_enabled") is True}
        return await self.t.shell(request, "marca.html", "marca", {
            "org": ctx["org"], "project": ctx["project"], "can_edit": ctx["manager"], "values": shown,
            "errors": errors or {}, "name_max": NAME_MAX, "enforcing": current.get("enforcement") == "enforce"}, status)

    def router(self) -> APIRouter:
        router, me = APIRouter(), self

        @router.get("/marca", response_class=HTMLResponse)
        async def brand(request: Request):
            early, ctx = await me.t.where(request)
            return early if early is not None else await me.page(request, ctx)

        @router.post("/marca", response_class=HTMLResponse)
        async def save(request: Request, brand_name: str = Form(""), brand_logo_url: str = Form(""),
                       brand_color: str = Form(""), brand_support_url: str = Form(""),
                       brand_support_email: str = Form(""), self_unlock_enabled: str | None = Form(None)):
            early, ctx = await me.t.where(request)
            if early is not None:
                return early
            values = {"brand_name": brand_name.strip(), "brand_logo_url": brand_logo_url.strip(),
                      "brand_color": brand_color.strip(), "brand_support_url": brand_support_url.strip(),
                      "brand_support_email": brand_support_email.strip(), "self_unlock_enabled": self_unlock_enabled is not None}
            errors = validate(values)
            if errors:
                return await me.page(request, ctx, values=values, errors=errors, status=422)
            project = quote(ctx["project"]["id"], safe="")
            # "" clears a brand field in the engine; the toggle is always sent, so an unchecked box turns it off.
            status, body = await me.t.write(request, "PATCH", f"/v1/projects/{project}", values)
            if status == 200:
                return me.t.done(request, SAVED_ON if values["self_unlock_enabled"] else SAVED, "/marca")
            if status == 422 and isinstance(body, dict) and body.get("error") == "unsafe_url":
                return await me.page(request, ctx, values=values, errors={"brand_logo_url": LOGO_ERROR}, status=422)
            if status == 422:
                return await me.page(request, ctx, values=values, errors={"form": FAILED}, status=422)
            if status == 403:
                return me.t.refused(request, "Tu rol no permite cambiar la marca. Solo un admin u owner puede hacerlo.", "/marca")
            if status is None:
                return me.t.no_store(await me.t.unreachable(request, "marca"))
            return me.t.refused(request, change_error(status, body), "/marca")

        return router
