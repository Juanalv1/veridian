"""Google and GitHub login, the signed session and its guards (session, project, CSRF).

Hand-rolled OAuth with httpx, like Centinela's web/auth.py: `state` lives in the session and is
single use, a verified email is required (GitHub: the primary one, from /user/emails), and the Google and
GitHub transports are injectable for tests. A person is their verified email: the engine joins the providers. The
session cookie never holds a role: the engine reads the role from its database on every call.
"""

import hmac
import logging
import secrets
from contextvars import ContextVar
from typing import NamedTuple
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .config import Settings

log = logging.getLogger("veridian.panel")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
SESSION_MAX_AGE_S = 8 * 3600
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

LOGIN_ERRORS = {
    "denied": "Cancelaste el inicio de sesión con Google.",
    "state": "El inicio de sesión expiró o no es válido. Inténtalo de nuevo.",
    "email": "Tu cuenta de Google no tiene el correo verificado.",
    "engine": "Iniciaste sesión, pero Veridian no pudo confirmar tu cuenta. Inténtalo de nuevo en unos minutos.",
    "failed": "No pudimos completar el inicio de sesión con Google. Inténtalo de nuevo.",
    "github_denied": "Cancelaste el inicio de sesión con GitHub.",
    "github_email": ("Tu cuenta de GitHub no tiene un correo principal verificado. Verifica tu correo principal en "
                     "GitHub o entra con Google."),
    "github_failed": "No pudimos completar el inicio de sesión con GitHub. Inténtalo de nuevo.",
}
UNCONFIGURED = ("Panel sin configurar. Faltan variables de entorno del acceso (Google, sesión o credencial "
                "del motor). Hasta que estén, el panel no muestra nada.")


class NotConfigured(Exception):
    pass


class LoginRequired(Exception):
    pass


class NoProjects(Exception):
    pass


class CsrfRejected(Exception):
    pass


class Who(NamedTuple):
    """The caller of the current request, as the engine must be told: person and project."""

    person: str
    project: str


# Set by `Auth.project_guard` (an async dependency, so it runs in the request's own task) and read by the
# panel's engine calls, including the ones that run concurrently: tasks copy the context when created.
current: ContextVar[Who | None] = ContextVar("veridian_panel_who", default=None)


def install(app, settings: Settings, templates: Jinja2Templates, client: httpx.AsyncClient,
            google_transport: httpx.AsyncBaseTransport | None,
            github_transport: httpx.AsyncBaseTransport | None = None) -> "Auth":
    """Wire the exception handlers; return the guards and the public router."""
    auth = Auth(settings, templates, client, google_transport, github_transport)

    @app.exception_handler(NotConfigured)
    async def _not_configured(_: Request, __: NotConfigured):
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Veridian</title><p>{UNCONFIGURED}</p>",
                            status_code=503)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, __: LoginRequired):
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=401, headers={"HX-Redirect": "/login"})  # a poll must not render the login page inside the panel
        if request.method == "GET":
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    @app.exception_handler(NoProjects)
    async def _no_projects(request: Request, __: NoProjects):
        # With an org (but no project yet) the selector is where a project gets created; with none, the
        # "sin organización" page. The session holds the org id from login, never a role.
        return RedirectResponse("/proyectos" if request.session.get("org") else "/sin-proyectos", status_code=303)

    @app.exception_handler(CsrfRejected)
    async def _csrf_rejected(_: Request, __: CsrfRejected):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    return auth


class Auth:
    def __init__(self, settings: Settings, templates: Jinja2Templates, client: httpx.AsyncClient,
                 google_transport: httpx.AsyncBaseTransport | None,
                 github_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings, self.templates, self.client, self.google_transport = settings, templates, client, google_transport
        self.github_transport = github_transport

    # -- guards (FastAPI dependencies) ----------------------------------------------------------

    def configured_guard(self) -> None:
        if not self.settings.configured:
            raise NotConfigured()

    @staticmethod
    def signed_in(request: Request) -> bool:
        s = request.session
        return bool(s.get("person") and s.get("email") and s.get("csrf"))

    async def session_guard(self, request: Request) -> None:
        """A signed-in session; every write also passes the CSRF check."""
        if not self.signed_in(request):
            raise LoginRequired()
        if request.method not in SAFE_METHODS and not await self.csrf_ok(request):
            raise CsrfRejected()

    async def project_guard(self, request: Request) -> None:
        project = request.session.get("project")
        if not project:
            raise NoProjects()
        current.set(Who(request.session["person"], project))

    async def csrf_ok(self, request: Request) -> bool:
        """Same-site Origin (PUBLIC_URL when set) and the session token, in X-CSRF-Token or the `csrf` field."""
        origin = request.headers.get("origin")
        if not origin:
            return False
        if self.settings.public_url:
            if origin.rstrip("/") != self.settings.public_url:
                return False
        elif urlparse(origin).netloc != request.headers.get("host", ""):
            return False
        sent = request.headers.get("x-csrf-token")
        if sent is None:
            sent = (await request.form()).get("csrf")
        expected = request.session.get("csrf", "")
        return isinstance(sent, str) and bool(expected) and hmac.compare_digest(sent.encode(), expected.encode())

    # -- helpers ----------------------------------------------------------------------------------

    def redirect_uri(self, request: Request, path: str = "/auth/callback") -> str:
        base = self.settings.public_url or str(request.base_url).rstrip("/")
        return f"{base}{path}"

    @staticmethod
    def login_error(code: str) -> RedirectResponse:
        return RedirectResponse("/login?" + urlencode({"error": code}), status_code=303)

    async def google_profile(self, request: Request, code: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.google_transport) as http:
                tok = await http.post(GOOGLE_TOKEN_URL, data={
                    "code": code, "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "redirect_uri": self.redirect_uri(request), "grant_type": "authorization_code"})
                tok.raise_for_status()
                access = tok.json().get("access_token")
                if not access:
                    return None
                info = await http.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
                info.raise_for_status()
                profile = info.json()
        except Exception as exc:  # network, Google error or bad JSON: never the details, never a 500
            log.warning("google login failed: %s", type(exc).__name__)
            return None
        return profile if isinstance(profile, dict) else None

    async def github_profile(self, request: Request, code: str) -> dict | None:
        """{sub, email, name} of the GitHub account, with its primary verified email; None if GitHub fails, and
        `{"sub": ..., "email": None}` when the account has no primary verified email."""
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.github_transport) as http:
                tok = await http.post(GITHUB_TOKEN_URL, headers={"Accept": "application/json"}, data={
                    "code": code, "client_id": self.settings.github_client_id,
                    "client_secret": self.settings.github_client_secret,
                    "redirect_uri": self.redirect_uri(request, "/auth/github/callback")})
                tok.raise_for_status()
                access = tok.json().get("access_token")  # a bad code is a 200 with {"error": ...}
                if not access:
                    return None
                headers = {"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json",
                           "User-Agent": "veridian-panel"}
                user = await http.get(GITHUB_USER_URL, headers=headers)
                user.raise_for_status()
                emails = await http.get(GITHUB_EMAILS_URL, headers=headers)
                emails.raise_for_status()
                account, listed = user.json(), emails.json()
            if not isinstance(account, dict) or not account.get("id") or not isinstance(listed, list):
                return None
        except Exception as exc:  # network, GitHub error or bad JSON: never the details, never a 500
            log.warning("github login failed: %s", type(exc).__name__)
            return None
        # The profile email can be hidden or unverified: only the primary verified address counts.
        primary = next((e.get("email") for e in listed if isinstance(e, dict) and e.get("primary") is True
                        and e.get("verified") is True and isinstance(e.get("email"), str) and e["email"]), None)
        return {"sub": f"github:{account['id']}", "email": primary, "name": account.get("name") or account.get("login")}

    async def engine_login(self, profile: dict) -> tuple[dict, list[dict], list[dict]] | None:
        """POST /v1/people/login with the service credential: (person, orgs, projects), or None if the engine
        says no. The engine answers person + orgs (role and projects each); `projects` is those of every org,
        in order, each with the id of its org."""
        body = {"google_sub": str(profile["sub"]), "email": str(profile["email"]).lower(),
                "name": str(profile.get("name") or profile["email"])}
        try:
            r = await self.client.post(f"{self.settings.engine_url}/v1/people/login", json=body,
                                       headers={"Authorization": f"Bearer {self.settings.panel_token}"})
            if r.status_code != 200:
                return None
            answer = r.json()
            person, orgs = answer["person"], answer["orgs"]
            if not isinstance(person["id"], str) or not isinstance(orgs, list):
                return None
            orgs = [o for o in orgs if isinstance(o, dict) and isinstance(o.get("id"), str)]
            projects = [{**p, "org_id": o["id"]} for o in orgs if isinstance(o.get("projects"), list)
                        for p in o["projects"] if isinstance(p, dict) and isinstance(p.get("id"), str)]
            return person, orgs, projects
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None

    async def start_session(self, request: Request, profile: dict) -> RedirectResponse:
        """Both providers end here with `sub` (their own id), a verified `email` and a `name`."""
        answer = await self.engine_login(profile)
        if answer is None:
            return self.login_error("engine")
        person, orgs, projects = answer
        request.session.clear()  # a fresh session (and CSRF token) for every login
        request.session.update({
            "person": person["id"], "email": str(profile["email"]).lower(),
            "name": str(profile.get("name") or profile["email"]), "csrf": secrets.token_urlsafe(32)})
        # Start on the first project (of the first org that has one); the selector changes it from there.
        if projects:
            request.session.update({"project": projects[0]["id"], "org": projects[0]["org_id"]})
        elif orgs:
            request.session["org"] = orgs[0]["id"]
        return RedirectResponse("/" if projects else "/proyectos" if orgs else "/sin-proyectos", status_code=303)

    # -- routes -------------------------------------------------------------------------------------

    def public_router(self) -> APIRouter:
        router, auth = APIRouter(), self

        @router.get("/login", response_class=HTMLResponse)
        async def login(request: Request, error: str | None = None):
            if auth.signed_in(request):
                return RedirectResponse("/", status_code=303)
            message = LOGIN_ERRORS.get(error, LOGIN_ERRORS["failed"]) if error else None
            return auth.templates.TemplateResponse(request, "login.html", {
                "error": message, "github": auth.settings.github_enabled})

        @router.get("/auth/google")
        async def auth_google(request: Request):
            state = secrets.token_urlsafe(32)
            request.session["oauth_state"] = state
            query = urlencode({
                "client_id": auth.settings.google_client_id, "redirect_uri": auth.redirect_uri(request),
                "response_type": "code", "scope": "openid email profile",
                "state": state, "prompt": "select_account"})
            return RedirectResponse(f"{GOOGLE_AUTH_URL}?{query}", status_code=303)

        @router.get("/auth/callback")
        async def auth_callback(request: Request, code: str | None = None, state: str | None = None,
                                error: str | None = None):
            expected = request.session.pop("oauth_state", None)  # single use
            if error:
                return auth.login_error("denied")
            if not (expected and state and code) or not hmac.compare_digest(str(state).encode(), str(expected).encode()):
                return auth.login_error("state")
            profile = await auth.google_profile(request, code)
            if profile is None or not profile.get("sub"):
                return auth.login_error("failed")
            if profile.get("email_verified") is not True or not profile.get("email"):
                return auth.login_error("email")
            return await auth.start_session(request, profile)

        def github_only():
            if not auth.settings.github_enabled:
                raise HTTPException(404)

        @router.get("/auth/github", dependencies=[Depends(github_only)])
        async def auth_github(request: Request):
            state = secrets.token_urlsafe(32)
            request.session["oauth_state"] = state
            query = urlencode({
                "client_id": auth.settings.github_client_id,
                "redirect_uri": auth.redirect_uri(request, "/auth/github/callback"),
                "scope": "user:email", "state": state})
            return RedirectResponse(f"{GITHUB_AUTH_URL}?{query}", status_code=303)

        @router.get("/auth/github/callback", dependencies=[Depends(github_only)])
        async def auth_github_callback(request: Request, code: str | None = None, state: str | None = None,
                                       error: str | None = None):
            expected = request.session.pop("oauth_state", None)  # single use
            if error:
                return auth.login_error("github_denied")
            if not (expected and state and code) or not hmac.compare_digest(str(state).encode(), str(expected).encode()):
                return auth.login_error("state")
            profile = await auth.github_profile(request, code)
            if profile is None:
                return auth.login_error("github_failed")
            if not profile["email"]:
                return auth.login_error("github_email")
            return await auth.start_session(request, profile)

        return router

    def session_router(self) -> APIRouter:
        """Pages for a signed-in person, with or without projects."""
        router, auth = APIRouter(dependencies=[Depends(self.session_guard)]), self

        @router.get("/sin-proyectos", response_class=HTMLResponse)
        async def no_projects(request: Request):
            return auth.templates.TemplateResponse(request, "sin_proyectos.html", {
                "email": request.session["email"], "csrf": request.session["csrf"]})

        @router.post("/logout")
        async def logout(request: Request):
            request.session.clear()
            return RedirectResponse("/login", status_code=303)

        return router
