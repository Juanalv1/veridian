"""Account pages: the organization and project selector, creation of both, deletion of an organization,
and the data behind the sidebar switcher (docs/DISENO-PRODUCTO.md, contract 10).

Nothing here trusts the browser. Every id that comes from a form is checked against what the engine says
the person belongs to (`GET /v1/me/orgs`), and the engine checks the role again on each write. The session
keeps only ids (`org`, `project`): never a role or a name that could go stale.

These calls name the person (`X-Veridian-Actor`) and, when there is one, the session project: they also work
before any project exists. Cached reads are keyed by person and project, like the panel's data reads.
"""

import asyncio
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .cache import TTLCache

ROLE_LABELS = {"owner": "Owner", "admin": "Admin", "viewer": "Viewer"}
MODES = {"enforce": ("Bloquea", "enforce"), "observe": ("Solo observa", "observe")}
CONTEXT_TTL = 5.0  # the sidebar card and the selector tolerate a few seconds; every write below drops the cache
NAME_MAX = 80

UNREACHABLE = "No se pudo conectar con el motor. Inténtalo de nuevo en unos minutos."
NO_ACCESS_PROJECT = "No tienes acceso a ese proyecto."
NO_ACCESS_ORG = "No tienes acceso a esa organización."
PILOT_LIMIT = ("Crear organizaciones está limitado durante el piloto. "
               "Si necesitas una, escríbenos a [correo de soporte].")
NAME_ORG_ERROR = "Escribe un nombre para la organización (hasta 80 caracteres)."
NAME_COMPANY_ERROR = "Escribe el nombre de tu empresa (hasta 80 caracteres)."
NAME_PROJECT_ERROR = "Escribe un nombre para el proyecto (hasta 80 caracteres)."
MODE_ERROR = "Elige cómo empieza el proyecto."
CREATE_PROJECT_FORBIDDEN = "Solo un admin u owner puede crear proyectos en esta organización."
WELCOME = "Listo: creamos «{name}» y su primer proyecto. Crea tu primera clave para conectar tu app."
FIRST_PROJECT_FAILED = ("Creamos tu organización, pero no su primer proyecto. Créalo aquí para seguir.")
MODE_NOT_SAVED = ("El proyecto se creó, pero no se pudo guardar el modo ni el uso de IA. "
                  "Revísalos en los ajustes del proyecto.")
NAME_MISMATCH = "El nombre no coincide. No se eliminó nada."
ORG_NOT_EMPTY = "La organización todavía tiene proyectos. Elimínalos primero, uno por uno."
DELETE_FORBIDDEN = "Solo un owner puede eliminar la organización."
MODE_SAVED = {"enforce": "«{name}» ahora bloquea: Veridian frena los accesos sospechosos.",
              "observe": "«{name}» ahora solo observa: Veridian registra, pero no bloquea nada."}
MODE_FORBIDDEN = "Solo un admin u owner puede cambiar el modo de un proyecto."


def engine_error(status: int) -> str:
    return f"El motor respondió con error {status}. Inténtalo de nuevo."


def parse_orgs(body) -> list[dict] | None:
    """`{orgs: [{id, name, role, projects: [{id, name}]}]}`; None if the shape is not that."""
    try:
        orgs = body["orgs"]
        out = []
        for o in orgs:
            projects = [{"id": p["id"], "name": str(p.get("name") or p["id"])} for p in o["projects"]]
            if not isinstance(o["id"], str) or not all(isinstance(p["id"], str) for p in projects):
                return None
            out.append({"id": o["id"], "name": str(o["name"]), "role": o["role"], "projects": projects})
        return out
    except (KeyError, TypeError, AttributeError):
        return None


def project_count(n: int) -> str:
    return "Sin proyectos" if n == 0 else "1 proyecto" if n == 1 else f"{n} proyectos"


def clean_name(value: str | None) -> str | None:
    name = (value or "").strip()
    return name if 1 <= len(name) <= NAME_MAX else None


class Accounts:
    def __init__(self, base: str, bearer: str, client: httpx.AsyncClient, templates: Jinja2Templates,
                 cache: TTLCache, session_guard) -> None:
        self.base, self.bearer, self.client, self.templates, self.cache = base, bearer, client, templates, cache
        self.session_guard = session_guard

    # -- engine --------------------------------------------------------------------------------------

    def headers(self, request: Request) -> dict[str, str]:
        """Service credential and actor; the session project too when there is one (the engine ignores it on
        these routes, and every call the panel makes then names the same project)."""
        headers = {"Authorization": f"Bearer {self.bearer}", "X-Veridian-Actor": request.session["person"]}
        if request.session.get("project"):
            headers["X-Veridian-Project"] = request.session["project"]
        return headers

    async def read(self, request: Request, path: str) -> httpx.Response | None:
        headers = self.headers(request)
        key = (headers["X-Veridian-Actor"], headers.get("X-Veridian-Project", ""), path, ())
        try:
            return await self.cache.get(key, CONTEXT_TTL, lambda: self.client.get(f"{self.base}{path}", headers=headers))
        except httpx.HTTPError:
            return None

    async def write(self, request: Request, method: str, path: str, body: dict | None = None):
        """(status, json) of a write, or (None, None) when the engine cannot be reached. The cache is dropped
        whatever the answer was: what it held may no longer be true."""
        try:
            r = await self.client.request(method, f"{self.base}{path}", headers=self.headers(request), json=body)
        except httpx.HTTPError:
            return None, None
        self.cache.invalidate()
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None

    async def load_orgs(self, request: Request) -> list[dict] | None:
        r = await self.read(request, "/v1/me/orgs")
        if r is None or r.status_code != 200:
            return None
        try:
            return parse_orgs(r.json())
        except ValueError:
            return None

    async def load_org_projects(self, request: Request, org: dict) -> list[dict]:
        """The org's projects with their mode; the bare references from `me/orgs` if that call fails."""
        r = await self.read(request, f"/v1/orgs/{quote(org['id'], safe='')}/projects")
        try:
            if r is not None and r.status_code == 200:
                listed = {p["id"]: p for p in r.json()["projects"]}
                return [{**p, "enforcement": listed.get(p["id"], {}).get("enforcement")} for p in org["projects"]]
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        return [{**p, "enforcement": None} for p in org["projects"]]

    async def switcher(self, request: Request) -> dict | None:
        """What the sidebar card shows: org, role, project and mode of the session project. None if unknown."""
        pid = request.session.get("project")
        if not pid:
            return None
        orgs, project = await asyncio.gather(self.load_orgs(request), self.read(request, f"/v1/projects/{quote(pid, safe='')}"))
        found = next(((o, p) for o in orgs or [] for p in o["projects"] if p["id"] == pid), None)
        if found is None:
            return None
        org, proj = found
        mode = None
        try:
            if project is not None and project.status_code == 200:
                mode = MODES.get(project.json().get("enforcement"))
        except (ValueError, AttributeError):
            pass
        return {"org": org["name"], "role": ROLE_LABELS.get(org["role"], org["role"]), "role_key": org["role"],
                "project": proj["name"],
                "mode": mode[0] if mode else None, "mode_kind": mode[1] if mode else None}

    async def role(self, request: Request) -> str | None:
        """The person's role in the org of the session project (`owner`, `admin`, `viewer`), None if unknown. Used
        only to decide what to offer: the engine checks the role again on every write."""
        pid = request.session.get("project")
        orgs = await self.load_orgs(request)
        return next((o["role"] for o in orgs or [] for p in o["projects"] if p["id"] == pid), None)

    async def switcher_guard(self, request: Request) -> None:
        """A dependency of the panel pages: starts the card's lookup so it runs while the page reads its own
        data (`await request.state.switcher`). A poll (htmx) swaps only #live, so it skips the lookup."""
        skip = request.method != "GET" or request.headers.get("HX-Request") == "true"
        request.state.switcher = None if skip else asyncio.ensure_future(self.switcher(request))

    # -- session and rendering ------------------------------------------------------------------------

    @staticmethod
    def flash(request: Request, kind: str, text: str) -> None:
        request.session["flash"] = {"kind": kind, "text": text}

    @staticmethod
    def set_current(request: Request, org: str, project: str | None = None) -> None:
        """`org` is where the selector opens; `project` is what every data read uses. They can differ for a
        moment (a new org with no project yet), and nothing reads a role or a name from them."""
        request.session["org"] = org
        if project is not None:
            request.session["project"] = project

    def render(self, request: Request, template: str, ctx: dict | None = None, status: int = 200):
        s = request.session
        return self.templates.TemplateResponse(request, template, {
            "email": s.get("email", ""), "csrf": s.get("csrf", ""), "flash": s.pop("flash", None), **(ctx or {})},
            status_code=status)

    def unreachable(self, request: Request):
        return self.render(request, "proyectos.html", {"orgs": [], "engine_error": UNREACHABLE}, 502)

    @staticmethod
    def back(location: str = "/proyectos") -> RedirectResponse:
        return RedirectResponse(location, status_code=303)

    # -- routes -------------------------------------------------------------------------------------------

    def router(self) -> APIRouter:
        router, me = APIRouter(dependencies=[Depends(self.session_guard)]), self

        @router.get("/proyectos", response_class=HTMLResponse)
        async def selector(request: Request, org: str | None = None):
            orgs = await me.load_orgs(request)
            if orgs is None:
                return me.unreachable(request)
            if not orgs:
                return me.back("/sin-proyectos")
            wanted = org or request.session.get("org")
            current = next((o for o in orgs if o["id"] == wanted), orgs[0])
            projects = await me.load_org_projects(request, current)
            role = current["role"]
            can_create = role in ("owner", "admin")
            return me.render(request, "proyectos.html", {
                "orgs": [{**o, "count": project_count(len(o["projects"])), "role_label": ROLE_LABELS.get(o["role"], o["role"]),
                          "current": o["id"] == current["id"]} for o in orgs],
                "org": current, "role_label": ROLE_LABELS.get(role, role), "is_viewer": role == "viewer",
                "projects": [{**p, "mode": MODES.get(p["enforcement"]), "current": p["id"] == request.session.get("project")}
                             for p in projects],
                "can_create_project": can_create, "can_delete": role == "owner" and not current["projects"],
                "engine_error": None})

        @router.post("/proyectos/abrir")
        async def open_project(request: Request, project_id: str = Form("")):
            orgs = await me.load_orgs(request)
            if orgs is None:
                me.flash(request, "err", UNREACHABLE)
                return me.back()
            owner = next((o for o in orgs if any(p["id"] == project_id for p in o["projects"])), None)
            if owner is None:
                me.flash(request, "err", NO_ACCESS_PROJECT)
                return me.back()
            me.set_current(request, owner["id"], project_id)
            return me.back("/")

        def company_step(request: Request, name: str = "", error: str | None = None, status: int = 200,
                         pilot: bool = False):
            return me.render(request, "sin_proyectos.html", {
                "name": name, "error": error, "pilot": pilot, "pilot_text": PILOT_LIMIT if pilot else ""}, status)

        def has_org_redirect(request: Request) -> RedirectResponse:
            return me.back("/" if request.session.get("project") else "/proyectos")

        @router.get("/sin-proyectos", response_class=HTMLResponse)
        async def no_projects(request: Request):
            """First run: a person with no organization is asked only for the company name. The engine, not the
            session, says whether they have one."""
            if await me.load_orgs(request):
                return has_org_redirect(request)
            return company_step(request)

        @router.post("/sin-proyectos", response_class=HTMLResponse)
        async def create_company(request: Request, name: str = Form("")):
            """The org (person as owner) and its first project, named after the company, in one step."""
            clean = clean_name(name)
            if clean is None:
                return company_step(request, name, NAME_COMPANY_ERROR, 400)
            orgs = await me.load_orgs(request)
            if orgs is None:
                return company_step(request, name, UNREACHABLE, 502)
            if orgs:
                return has_org_redirect(request)  # a stale page or a double submit never makes a second org
            status, org = await me.write(request, "POST", "/v1/orgs", {"name": clean})
            if status == 403:
                return company_step(request, name, status=403, pilot=True)
            if status is None:
                return company_step(request, name, UNREACHABLE, 502)
            if status != 201 or not isinstance(org, dict) or not isinstance(org.get("id"), str):
                return company_step(request, name, engine_error(status), 502)
            org_id = quote(org["id"], safe="")
            status, project = await me.write(request, "POST", f"/v1/orgs/{org_id}/projects", {"name": clean})
            if status != 201 or not isinstance(project, dict) or not isinstance(project.get("id"), str):
                me.set_current(request, org["id"])
                me.flash(request, "warn", FIRST_PROJECT_FAILED)
                return me.back(f"/proyectos/nuevo?org={org_id}")
            me.set_current(request, org["id"], project["id"])
            me.flash(request, "ok", WELCOME.format(name=clean))
            return me.back("/claves")

        @router.get("/organizaciones/nueva", response_class=HTMLResponse)
        async def new_org_form(request: Request):
            return me.render(request, "organizacion_nueva.html", {"name": "", "error": None, "pilot": False})

        @router.post("/organizaciones/nueva", response_class=HTMLResponse)
        async def create_org(request: Request, name: str = Form("")):
            clean = clean_name(name)
            if clean is None:
                return me.render(request, "organizacion_nueva.html", {"name": name, "error": NAME_ORG_ERROR, "pilot": False}, 400)
            status, body = await me.write(request, "POST", "/v1/orgs", {"name": clean})
            if status == 201 and isinstance(body, dict) and isinstance(body.get("id"), str):
                me.set_current(request, body["id"])
                return me.back(f"/proyectos/nuevo?org={quote(body['id'], safe='')}")
            if status == 403:
                return me.render(request, "organizacion_nueva.html", {"name": name, "error": None, "pilot": True, "pilot_text": PILOT_LIMIT}, 403)
            if status is None:
                return me.render(request, "organizacion_nueva.html", {"name": name, "error": UNREACHABLE, "pilot": False}, 502)
            return me.render(request, "organizacion_nueva.html", {"name": name, "error": engine_error(status), "pilot": False}, 502)

        async def project_form_context(request: Request, org_id: str | None):
            """(orgs, org) when the person may create projects in `org_id`; else a redirect or an error page."""
            orgs = await me.load_orgs(request)
            if orgs is None:
                return me.unreachable(request), None
            org = next((o for o in orgs if o["id"] == org_id), None)
            if org is None:
                me.flash(request, "err", NO_ACCESS_ORG)
                return me.back(), None
            if org["role"] not in ("owner", "admin"):
                me.flash(request, "err", CREATE_PROJECT_FORBIDDEN)
                return me.back(), None
            return None, org

        def project_form(request: Request, org: dict, values: dict, error: str | None = None, status: int = 200):
            return me.render(request, "proyecto_nuevo.html", {"org": org, "error": error, **values}, status)

        @router.get("/proyectos/nuevo", response_class=HTMLResponse)
        async def new_project_form(request: Request, org: str | None = None):
            early, found = await project_form_context(request, org)
            if early is not None:
                return early
            return project_form(request, found, {"name": "", "enforcement": "observe", "ai_enabled": True})

        @router.post("/proyectos/nuevo", response_class=HTMLResponse)
        async def create_project(request: Request, org: str = Form(""), name: str = Form(""),
                                 enforcement: str = Form(""), ai_enabled: str | None = Form(None)):
            early, found = await project_form_context(request, org)
            if early is not None:
                return early
            values = {"name": name, "enforcement": enforcement or "observe", "ai_enabled": ai_enabled is not None}
            clean = clean_name(name)
            if clean is None:
                return project_form(request, found, values, NAME_PROJECT_ERROR, 400)
            if enforcement not in MODES:
                return project_form(request, found, values, MODE_ERROR, 400)
            status, body = await me.write(request, "POST", f"/v1/orgs/{quote(found['id'], safe='')}/projects", {"name": clean})
            if status is None:
                return project_form(request, found, values, UNREACHABLE, 502)
            if status == 403:
                return project_form(request, found, values, CREATE_PROJECT_FORBIDDEN, 403)
            if status != 201 or not isinstance(body, dict) or not isinstance(body.get("id"), str):
                return project_form(request, found, values, engine_error(status), 502)
            pid = body["id"]
            # The contract creates a project with just a name; mode and AI use are set on the project itself.
            saved, _ = await me.write(request, "PATCH", f"/v1/projects/{quote(pid, safe='')}",
                                      {"enforcement": enforcement, "ai_enabled": ai_enabled is not None})
            if saved != 200:
                me.flash(request, "warn", MODE_NOT_SAVED)
            me.set_current(request, found["id"], pid)
            return me.back("/")

        @router.post("/proyectos/{project_id}/modo")
        async def set_mode(request: Request, project_id: str, enforcement: str = Form("")):
            """Switch a project between enforce and observe (PATCH /v1/projects/{id}); the engine checks the role."""
            orgs = await me.load_orgs(request)
            if orgs is None:
                me.flash(request, "err", UNREACHABLE)
                return me.back()
            org = next((o for o in orgs if any(p["id"] == project_id for p in o["projects"])), None)
            if org is None:
                me.flash(request, "err", NO_ACCESS_PROJECT)
                return me.back()
            back = f"/proyectos?org={quote(org['id'], safe='')}"
            if enforcement not in MODES:
                me.flash(request, "err", MODE_ERROR)
                return me.back(back)
            status, _ = await me.write(request, "PATCH", f"/v1/projects/{quote(project_id, safe='')}",
                                       {"enforcement": enforcement})
            if status == 200:
                name = next(p["name"] for p in org["projects"] if p["id"] == project_id)
                me.flash(request, "ok", MODE_SAVED[enforcement].format(name=name))
            else:
                me.flash(request, "err", UNREACHABLE if status is None else MODE_FORBIDDEN if status == 403
                         else engine_error(status))
            return me.back(back)

        @router.post("/organizaciones/{org_id}/eliminar")
        async def delete_org(request: Request, org_id: str, confirm: str = Form("")):
            orgs = await me.load_orgs(request)
            if orgs is None:
                me.flash(request, "err", UNREACHABLE)
                return me.back()
            org = next((o for o in orgs if o["id"] == org_id), None)
            if org is None:
                me.flash(request, "err", NO_ACCESS_ORG)
                return me.back()
            if confirm.strip() != org["name"]:
                me.flash(request, "err", NAME_MISMATCH)
                return me.back(f"/proyectos?org={quote(org_id, safe='')}")
            status, _ = await me.write(request, "DELETE", f"/v1/orgs/{quote(org_id, safe='')}")
            if status == 204:
                await me.forget_gone(request, f"Organización «{org['name']}» eliminada.")
                return me.back()
            message = (UNREACHABLE if status is None else ORG_NOT_EMPTY if status == 409
                       else DELETE_FORBIDDEN if status == 403 else engine_error(status))
            me.flash(request, "err", message)
            return me.back(f"/proyectos?org={quote(org_id, safe='')}")

        return router

    async def forget_gone(self, request: Request, message: str) -> None:
        """After a deletion: drop the session's org and project when the engine no longer lists them."""
        self.flash(request, "ok", message)
        orgs = await self.load_orgs(request) or []
        if request.session.get("org") not in {o["id"] for o in orgs}:
            request.session.pop("org", None)
            if orgs:
                request.session["org"] = orgs[0]["id"]  # the selector opens on an org the person still has
        if request.session.get("project") not in {p["id"] for o in orgs for p in o["projects"]}:
            request.session.pop("project", None)
