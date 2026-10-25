"""Pages that manage an organization and its project: members and invitations, API keys, audit log
(docs/DISENO-PRODUCTO.md, contracts 11, 12 and 15).

Same rule as accounts.py: nothing here trusts the browser. The organization and the project are always the
ones of the session project (looked up through `GET /v1/me/orgs`), never a form field; the engine checks the
role again on every call and the panel shows what it answers. The session holds no role, no member and no key.
Reads here are not cached: these are admin pages, and a key list must never be served from a cache.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from .accounts import NAME_MAX, ROLE_LABELS, UNREACHABLE, Accounts, clean_name, engine_error
from .fmt import ago, expires_in, short_date, stamp

ROLES = ("viewer", "admin", "owner")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MANAGERS = ("owner", "admin")

NO_ACCESS_PROJECT = "No tienes acceso a ese proyecto."
NO_PERMISSION = "No tienes permiso para hacer ese cambio."
EMAIL_ERROR = "Escribe un correo válido."
ROLE_ERROR = "Elige un rol."
ALREADY_MEMBER = "Esa persona ya es miembro de esta organización."
OWNER_ONLY_INVITE = "Solo un owner puede invitar a otro owner."
INVITE_FORBIDDEN = "No tienes permiso para invitar personas."
SELF_CHANGE = "No puedes cambiar tu propio rol ni quitarte a ti mismo."
LAST_OWNER = "Siempre tiene que quedar al menos un owner."
MEMBER_GONE = "Esa persona ya no está en la organización."
INVITATION_GONE = "Esa invitación ya no existe."
KEY_NAME_ERROR = f"Escribe un nombre para la clave (hasta {NAME_MAX} caracteres)."
KEY_GONE = "Esa clave ya no existe."
NO_KEYS_LEFT = "Ya no queda ninguna clave activa: el middleware no puede reportar y Veridian no protege la app hasta que crees otra."

WEBHOOK_UNSAFE = "La URL tiene que ser https y pública."
WEBHOOK_GONE = "Este proyecto ya no tiene webhook."
WEBHOOK_URL_MAX = 2048
DELIVERY_LABELS = {"delivered": "Entregada", "pending": "Pendiente", "failed": "Fallida"}

AUDIT_PAGE = 50
AUDIT_NO_ACCESS = "El registro de cambios lo ven los admins y owners de {org}."
# The "Qué" filter: action prefixes. Ajustes and data deletions join when the engine audits them.
WHAT = {"bloqueos": ("decision.",), "claves": ("api_key.",), "miembros": ("member.", "invitation."),
        "webhook": ("webhook.",)}
WHAT_OPTIONS = (("", "Todo"), ("bloqueos", "Bloqueos levantados"), ("claves", "Claves"),
                ("miembros", "Miembros e invitaciones"), ("webhook", "Webhook"))

Shell = Callable[..., Awaitable]  # (request, template, section, ctx, status=200) -> response


def label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def describe(entry: dict, org: str) -> dict:
    """One audit entry as a sentence: who, what they did, to what, and where (docs/DISENO-PRODUCTO.md, AuditLog)."""
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    where = entry.get("project_name") or org
    role = label(str(meta.get("role", "")))
    verb, target, detail = {
        "decision.marked_normal": ("levantó el bloqueo de la decisión", entry.get("target_id"),
                                   f"{where} · El perfil aprende ese acceso."),
        "api_key.created": ("creó la clave", meta.get("prefix"), f"{where} · {meta.get('name')}"),
        "api_key.revoked": ("revocó la clave", meta.get("prefix"), f"{where} · {meta.get('name')}"),
        "webhook.set": ("configuró el webhook", meta.get("url"), where),
        "webhook.enable": ("activó el webhook" if meta.get("enabled") else "pausó el webhook", "", where),
        "webhook.delete": ("eliminó el webhook", "", where),
        "webhook.test": ("envió una prueba del webhook", "", where),
        "decision.self_unlocked": ("", entry.get("target_id"),
                                   f"{where} · Confirmó desde el correo. El perfil aprende ese acceso."),
        "project.update": ("cambió la marca o el desbloqueo por correo de", "", where),
        "member.role_changed": ("cambió el rol de", meta.get("email"),
                                f"{where} · de {label(str(meta.get('from')))} a {label(str(meta.get('to')))}"),
        "member.removed": ("quitó a", meta.get("email"), f"{where} · era {role}"),
        "invitation.created": ("invitó a", meta.get("email"), f"{where} · como {role}"),
        "invitation.resent": ("reenvió la invitación a", meta.get("email"), f"{where} · como {role}"),
        "invitation.revoked": ("canceló la invitación a", meta.get("email"), f"{where} · como {role}"),
    }.get(str(entry.get("action")), (str(entry.get("action")), entry.get("target_id"), where))
    actor = ("Desbloqueado por el usuario" if entry.get("action") == "decision.self_unlocked"
             else entry.get("actor_name") or "Alguien")
    return {"seq": entry.get("seq"), "ts": stamp(entry.get("ts")), "actor": actor,
            "verb": verb, "target": target or "", "detail": detail}


def change_error(status: int | None, body) -> str:
    """The sentence for a refused member or invitation change."""
    code = body.get("error") if isinstance(body, dict) else None
    if status is None:
        return UNREACHABLE
    if status == 403:
        return NO_PERMISSION
    if status == 409 and code == "cannot_change_self":
        return SELF_CHANGE
    if status == 409 and code == "last_owner":
        return LAST_OWNER
    if status == 404:
        return MEMBER_GONE
    return engine_error(status)


class Team:
    def __init__(self, accounts: Accounts, shell: Shell, now: Callable[[], datetime]) -> None:
        self.a, self.shell, self.now = accounts, shell, now

    # -- engine --------------------------------------------------------------------------------------

    async def fetch(self, request: Request, path: str, params: dict | None = None) -> httpx.Response | None:
        try:
            return await self.a.client.get(f"{self.a.base}{path}", params=params, headers=self.a.headers(request))
        except httpx.HTTPError:
            return None

    @staticmethod
    def items(response: httpx.Response | None, key: str) -> list[dict] | None:
        """`response.json()[key]` as a list of objects; None when the call failed or has another shape."""
        try:
            if response is None or response.status_code != 200:
                return None
            rows = response.json()[key]
            return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else None
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

    async def where(self, request: Request):
        """(early response, context). Context: the org and project of the session project, and the person's role."""
        orgs = await self.a.load_orgs(request)
        if orgs is None:
            return await self.unreachable(request), None
        pid = request.session.get("project")
        found = next(((o, p) for o in orgs for p in o["projects"] if p["id"] == pid), None)
        if found is None:
            self.a.flash(request, "err", NO_ACCESS_PROJECT)
            return self.a.back(), None
        org, project = found
        return None, {"org": org, "project": project, "role": org["role"], "manager": org["role"] in MANAGERS}

    async def unreachable(self, request: Request, section: str = "miembros", status: int = 502):
        return await self.shell(request, "equipo_error.html", section, {"engine_error": UNREACHABLE}, status)

    async def write(self, request: Request, method: str, path: str, body: dict | None = None):
        return await self.a.write(request, method, path, body)

    def done(self, request: Request, text: str, to: str):
        self.a.flash(request, "ok", text)
        return self.a.back(to)

    def refused(self, request: Request, text: str, to: str):
        self.a.flash(request, "err", text)
        return self.a.back(to)

    # -- members ---------------------------------------------------------------------------------------

    async def members_page(self, request: Request, ctx: dict, *, error: str | None = None, email: str = "",
                           role: str = "viewer", status: int = 200):
        base = f"/v1/orgs/{quote(ctx['org']['id'], safe='')}"
        members_r, invites_r = await asyncio.gather(self.fetch(request, f"{base}/members"),
                                                    self.fetch(request, f"{base}/invitations"))
        members, invites = self.items(members_r, "members"), self.items(invites_r, "invitations")
        if members is None or invites is None:
            if members_r is not None and members_r.status_code == 403:
                return await self.shell(request, "equipo_error.html", "miembros", {"engine_error": NO_ACCESS_PROJECT}, 403)
            return await self.unreachable(request)
        mine, actor = request.session["person"], ctx["role"]
        owners = sum(1 for m in members if m.get("role") == "owner")
        now = self.now()
        rows = []
        for m in members:
            target = m.get("role")
            is_self = m.get("person_id") == mine
            editable = ctx["manager"] and not is_self and (actor == "owner" or target != "owner")
            joined = ago(m.get("joined_at"), now)
            rows.append({
                "person_id": m.get("person_id"), "name": m.get("name") or m.get("email"), "email": m.get("email"),
                "role": target, "role_label": label(target), "self": is_self, "editable": editable,
                "note": "único owner" if target == "owner" and owners == 1 else f"entró {joined}" if joined else ""})
        pending = [{"id": i.get("id"), "email": i.get("email"), "role": i.get("role"), "role_label": label(i.get("role")),
                    "expired": i.get("expired") is True, "expires": expires_in(i.get("expires_at"), now),
                    "actions": ctx["manager"] and (actor == "owner" or i.get("role") != "owner")} for i in invites]
        assignable = ("viewer", "admin", "owner") if actor == "owner" else ("viewer", "admin")
        return await self.shell(request, "miembros.html", "miembros", {
            "org": ctx["org"], "members": rows, "invitations": pending, "can_manage": ctx["manager"],
            "is_viewer": ctx["role"] == "viewer", "roles": [(r, label(r)) for r in assignable],
            "invite_error": error, "invite_email": email, "invite_role": role}, status)

    # -- API keys ------------------------------------------------------------------------------------------

    @staticmethod
    def no_store(response):
        """A key is shown once: nothing between the engine and the person may keep a copy of that response."""
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    async def connection(self, request: Request, project: str) -> str:
        """`connected` once the project has received an access, `waiting` before, `unknown` when the engine cannot
        say. Read from the activity the panel already uses: one row is enough."""
        accesses = self.items(await self.fetch(request, "/v1/activity", {"limit": 1}), "activity")
        return "unknown" if accesses is None else "connected" if accesses else "waiting"

    async def keys_page(self, request: Request, ctx: dict, *, created: dict | None = None, error: str | None = None,
                        name: str = "", status: int = 200):
        project = quote(ctx["project"]["id"], safe="")
        response, connection = await asyncio.gather(self.fetch(request, f"/v1/projects/{project}/api-keys"),
                                                    self.connection(request, project))
        if response is not None and response.status_code == 403:
            return self.no_store(await self.shell(request, "claves.html", "claves", {
                "denied": True, "org": ctx["org"], "project": ctx["project"]}, 403))
        keys = self.items(response, "api_keys")
        if keys is None:
            return self.no_store(await self.unreachable(request, "claves"))
        now = self.now()
        rows = []
        for k in keys:
            active = k.get("status") == "active"
            who = f", por {k['created_by_name']}" if k.get("created_by_name") else ""
            if active:
                last = ago(k.get("last_used_at"), now) or "sin uso todavía"
            else:
                by = f" por {k['revoked_by_name']}" if k.get("revoked_by_name") else ""
                last = f"revocada el {short_date(k.get('revoked_at'))}{by}"
            rows.append({"id": k.get("id"), "name": k.get("name"), "prefix": k.get("prefix"), "active": active,
                         "created": f"{short_date(k.get('created_at'))}{who}", "last": last})
        return self.no_store(await self.shell(request, "claves.html", "claves", {
            "denied": False, "org": ctx["org"], "project": ctx["project"], "keys": rows,
            "any_active": any(r["active"] for r in rows), "connection": connection, "created": created, "error": error, "name": name}, status))

    # -- webhook -------------------------------------------------------------------------------------------

    async def webhook_page(self, request: Request, ctx: dict, *, created: dict | None = None, error: str | None = None,
                           detail: str = "", url: str = "", status: int = 200):
        base = f"/v1/projects/{quote(ctx['project']['id'], safe='')}/webhook"
        current, deliveries = await asyncio.gather(self.fetch(request, base),
                                                   self.fetch(request, f"{base}/deliveries", {"limit": 50}))
        if current is not None and current.status_code == 403:
            return self.no_store(await self.shell(request, "webhook.html", "webhook", {
                "denied": True, "org": ctx["org"], "project": ctx["project"]}, 403))
        rows = self.items(deliveries, "deliveries")
        try:
            hook = current.json()["webhook"] if current is not None and current.status_code == 200 else False
        except (ValueError, KeyError, TypeError):
            hook = False
        if rows is None or not (hook is None or isinstance(hook, dict)):
            return self.no_store(await self.unreachable(request, "webhook"))
        shown = [{"status": DELIVERY_LABELS.get(d.get("status"), str(d.get("status") or "")),
                  "attempts": d.get("attempts"), "decision": d.get("decision_id") or "",
                  "error": d.get("last_error") or "", "created": stamp(d.get("created_at"))} for d in rows]
        return self.no_store(await self.shell(request, "webhook.html", "webhook", {
            "denied": False, "org": ctx["org"], "project": ctx["project"], "webhook": hook, "deliveries": shown,
            "created": created, "error": error, "detail": detail, "url": url, "url_max": WEBHOOK_URL_MAX}, status))

    # -- audit ---------------------------------------------------------------------------------------------

    async def audit_page(self, request: Request, ctx: dict, project: str, what: str, who: str, before: str):
        org = ctx["org"]
        project = project if project in {p["id"] for p in org["projects"]} else ""
        what = what if what in WHAT else ""
        cursor = int(before) if before.isdigit() and int(before) > 0 else None
        params: dict = {"limit": AUDIT_PAGE, **({"project": project} if project else {}), **({"before": cursor} if cursor else {})}
        base = f"/v1/orgs/{quote(org['id'], safe='')}"
        audit, members = await asyncio.gather(self.fetch(request, f"{base}/audit", params), self.fetch(request, f"{base}/members"))
        if audit is not None and audit.status_code == 403:
            return await self.shell(request, "auditoria.html", "auditoria", {
                "denied": True, "org": org, "no_access": AUDIT_NO_ACCESS.format(org=org["name"])}, 403)
        rows = self.items(audit, "entries")
        if rows is None:
            return await self.unreachable(request, "auditoria")
        try:
            newer = audit.json().get("next_before")
        except ValueError:
            newer = None
        shown = [e for e in rows
                 if (not what or str(e.get("action", "")).startswith(WHAT[what])) and (not who or e.get("actor_person_id") == who)]
        people = self.items(members, "members") or []
        names = [m.get("name") or m.get("email") for m in people]
        options = [{"id": m.get("person_id"), "label": f"{n} ({m.get('email')})" if names.count(n) > 1 else n}
                   for m, n in zip(people, names)]
        older = None
        if isinstance(newer, int) and not isinstance(newer, bool):
            older = "/auditoria?" + urlencode({k: v for k, v in (("project", project), ("what", what), ("who", who),
                                                                  ("before", newer)) if v})
        return await self.shell(request, "auditoria.html", "auditoria", {
            "denied": False, "org": org, "entries": [describe(e, org["name"]) for e in shown], "older": older,
            "filtered": bool(what or who), "what": what, "who": who, "project": project,
            "what_options": WHAT_OPTIONS, "who_options": options, "projects": org["projects"]})

    # -- routes ------------------------------------------------------------------------------------------

    def router(self) -> APIRouter:
        router, me = APIRouter(), self

        @router.get("/miembros", response_class=HTMLResponse)
        async def members(request: Request):
            early, ctx = await me.where(request)
            return early if early is not None else await me.members_page(request, ctx)

        @router.post("/miembros/invitar", response_class=HTMLResponse)
        async def invite(request: Request, email: str = Form(""), role: str = Form("")):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            address = email.strip().lower()
            if not EMAIL_RE.match(address) or len(address) > 254:
                return await me.members_page(request, ctx, error=EMAIL_ERROR, email=email, role=role or "viewer", status=400)
            if role not in ROLES:
                return await me.members_page(request, ctx, error=ROLE_ERROR, email=email, status=400)
            org = quote(ctx["org"]["id"], safe="")
            status, body = await me.write(request, "POST", f"/v1/orgs/{org}/invitations", {"email": address, "role": role})
            if status == 201:
                return me.done(request, f"Invitación creada para {address}.", "/miembros")
            code = body.get("error") if isinstance(body, dict) else None
            if status == 409 and code == "already_member":
                error = ALREADY_MEMBER
            elif status == 403:
                error = OWNER_ONLY_INVITE if role == "owner" else INVITE_FORBIDDEN
            elif status is None:
                error = UNREACHABLE
            else:
                error = engine_error(status)
            return await me.members_page(request, ctx, error=error, email=email, role=role,
                                         status=status if status in (403, 409) else 502)

        @router.post("/miembros/{person_id}/rol")
        async def change_role(request: Request, person_id: str, role: str = Form("")):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            if role not in ROLES:
                return me.refused(request, ROLE_ERROR, "/miembros")
            org = quote(ctx["org"]["id"], safe="")
            status, body = await me.write(request, "PATCH", f"/v1/orgs/{org}/members/{quote(person_id, safe='')}", {"role": role})
            if status == 200 and isinstance(body, dict):
                return me.done(request, f"Rol actualizado: {body.get('email')} ahora es {label(role)}.", "/miembros")
            return me.refused(request, change_error(status, body), "/miembros")

        @router.post("/miembros/{person_id}/quitar")
        async def remove(request: Request, person_id: str):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            org = quote(ctx["org"]["id"], safe="")
            status, body = await me.write(request, "DELETE", f"/v1/orgs/{org}/members/{quote(person_id, safe='')}")
            if status == 204:
                return me.done(request, f"Persona quitada de {ctx['org']['name']}.", "/miembros")
            return me.refused(request, change_error(status, body), "/miembros")

        @router.post("/invitaciones/{inv_id}/cancelar")
        async def cancel(request: Request, inv_id: str):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            org = quote(ctx["org"]["id"], safe="")
            status, body = await me.write(request, "DELETE", f"/v1/orgs/{org}/invitations/{quote(inv_id, safe='')}")
            if status == 204:
                return me.done(request, "Invitación cancelada.", "/miembros")
            return me.refused(request, INVITATION_GONE if status == 404 else change_error(status, body), "/miembros")

        @router.post("/invitaciones/{inv_id}/reenviar")
        async def resend(request: Request, inv_id: str):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            org = quote(ctx["org"]["id"], safe="")
            listed = me.items(await me.fetch(request, f"/v1/orgs/{org}/invitations"), "invitations")
            if listed is None:
                return me.refused(request, UNREACHABLE, "/miembros")
            inv = next((i for i in listed if i.get("id") == inv_id), None)
            if inv is None:
                return me.refused(request, INVITATION_GONE, "/miembros")
            # Inviting the same email and role again replaces the pending invitation with a fresh one.
            status, body = await me.write(request, "POST", f"/v1/orgs/{org}/invitations",
                                          {"email": inv["email"], "role": inv["role"]})
            if status == 201:
                return me.done(request, f"Invitación reenviada a {inv['email']}.", "/miembros")
            return me.refused(request, change_error(status, body), "/miembros")

        @router.get("/auditoria", response_class=HTMLResponse)
        async def audit(request: Request, project: str = "", what: str = "", who: str = "", before: str = ""):
            early, ctx = await me.where(request)
            return early if early is not None else await me.audit_page(request, ctx, project, what, who, before)

        @router.get("/claves", response_class=HTMLResponse)
        async def keys(request: Request):
            early, ctx = await me.where(request)
            return early if early is not None else await me.keys_page(request, ctx)

        @router.get("/claves/conexion", response_class=HTMLResponse)
        async def connection(request: Request):
            """The connection state alone, polled by the keys page until the first access arrives."""
            early, ctx = await me.where(request)
            if early is not None:
                return early
            state = await me.connection(request, ctx["project"]["id"])
            return me.no_store(me.a.templates.TemplateResponse(request, "partials/conexion.html", {"connection": state}))

        @router.post("/claves/nueva", response_class=HTMLResponse)
        async def create_key(request: Request, name: str = Form("")):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            clean = clean_name(name)
            if clean is None:
                return await me.keys_page(request, ctx, error=KEY_NAME_ERROR, name=name, status=400)
            project = quote(ctx["project"]["id"], safe="")
            status, body = await me.write(request, "POST", f"/v1/projects/{project}/api-keys", {"name": clean})
            if status == 201 and isinstance(body, dict) and isinstance(body.get("key"), str):
                # The only place the plaintext exists: this response. It is not stored, cached or logged.
                return await me.keys_page(request, ctx, created={"name": str(body.get("name") or clean), "key": body["key"]})
            if status == 403:
                return await me.keys_page(request, ctx)  # the engine says no: the page shows the no-access state
            if status is None:
                return me.no_store(await me.unreachable(request, "claves"))
            return await me.keys_page(request, ctx, error=engine_error(status), name=name, status=502)

        @router.post("/claves/{key_id}/revocar")
        async def revoke_key(request: Request, key_id: str):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            project = quote(ctx["project"]["id"], safe="")
            status, body = await me.write(request, "POST", f"/v1/projects/{project}/api-keys/{quote(key_id, safe='')}/revoke")
            if status != 200 or not isinstance(body, dict):
                return me.refused(request, KEY_GONE if status == 404 else change_error(status, body), "/claves")
            text = f"Clave «{body.get('name')}» revocada. Dejó de funcionar al instante."
            left = me.items(await me.fetch(request, f"/v1/projects/{project}/api-keys"), "api_keys")
            if left is not None and not any(k.get("status") == "active" for k in left):
                me.a.flash(request, "warn", f"Clave «{body.get('name')}» revocada. {NO_KEYS_LEFT}")
                return me.a.back("/claves")
            return me.done(request, text, "/claves")

        @router.get("/webhook", response_class=HTMLResponse)
        async def webhook(request: Request):
            early, ctx = await me.where(request)
            return early if early is not None else await me.webhook_page(request, ctx)

        @router.post("/webhook", response_class=HTMLResponse)
        async def save_webhook(request: Request, url: str = Form("")):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            clean = url.strip()
            project = quote(ctx["project"]["id"], safe="")
            status, body = await me.write(request, "PUT", f"/v1/projects/{project}/webhook", {"url": clean})
            if status == 200 and isinstance(body, dict) and isinstance(body.get("secret"), str):
                # The only place the signing secret exists: this response. It is not stored, cached or logged.
                return await me.webhook_page(request, ctx, created={"secret": body["secret"]})
            if status == 422 and isinstance(body, dict) and body.get("error") == "unsafe_url":
                return await me.webhook_page(request, ctx, error=WEBHOOK_UNSAFE, detail=str(body.get("detail") or ""),
                                             url=clean, status=422)
            if status == 403:
                return await me.webhook_page(request, ctx)  # the engine says no: the page shows the no-access state
            if status is None:
                return me.no_store(await me.unreachable(request, "webhook"))
            return await me.webhook_page(request, ctx, error=engine_error(status), url=clean, status=502)

        @router.post("/webhook/estado")
        async def toggle_webhook(request: Request, enabled: str = Form("")):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            on = enabled == "true"
            project = quote(ctx["project"]["id"], safe="")
            status, body = await me.write(request, "PATCH", f"/v1/projects/{project}/webhook", {"enabled": on})
            if status == 200:
                return me.done(request, "Webhook activado." if on else "Webhook pausado: Veridian no envía avisos.", "/webhook")
            return me.refused(request, WEBHOOK_GONE if status == 404 else change_error(status, body), "/webhook")

        @router.post("/webhook/eliminar")
        async def delete_webhook(request: Request):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            project = quote(ctx["project"]["id"], safe="")
            status, body = await me.write(request, "DELETE", f"/v1/projects/{project}/webhook")
            if status == 204:
                return me.done(request, "Webhook eliminado. Veridian ya no avisa a esa URL.", "/webhook")
            return me.refused(request, WEBHOOK_GONE if status == 404 else change_error(status, body), "/webhook")

        @router.post("/webhook/prueba")
        async def test_webhook(request: Request):
            early, ctx = await me.where(request)
            if early is not None:
                return early
            project = quote(ctx["project"]["id"], safe="")
            status, body = await me.write(request, "POST", f"/v1/projects/{project}/webhook/test")
            if status != 200 or not isinstance(body, dict):
                return me.refused(request, WEBHOOK_GONE if status == 404 else change_error(status, body), "/webhook")
            if body.get("delivered") is True:
                return me.done(request, f"La prueba llegó (HTTP {body.get('status_code')}).", "/webhook")
            return me.refused(request, f"La prueba no llegó: {body.get('error') or 'sin respuesta'}", "/webhook")

        return router
