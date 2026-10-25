"""Panel: live activity feed and the selected decision compared against the user's profile; refreshed every 2 s.

Everything sits behind a Google login. The panel talks to the engine with its own service credential plus
the signed-in person and project (X-Veridian-Actor, X-Veridian-Project); the engine checks membership and role.

Env: VERIDIAN_URL (engine base URL), VERIDIAN_PANEL_TOKEN (service credential), GOOGLE_CLIENT_ID,
GOOGLE_CLIENT_SECRET, SESSION_SECRET, PUBLIC_URL. Without them the panel refuses to serve (no open mode).
GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET add GitHub sign-in; without both it stays off.
"""

import asyncio
import dataclasses
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import accounts as accounts_module
from . import auth as auth_module
from .cache import TTLCache
from .config import Settings
from .fmt import count as _count
from .fmt import seconds_text
from .overview import build_overview, iso_z, local_day_window, user_rows
from .team import Team

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
REFRESH_SECONDS = 2
# Cache-busting query for /static assets: bump when tokens.css, panel.css, panel.js or htmx.min.js change.
ASSET_VERSION = "29"

# decision -> (nav word, detail word, color class)
VERDICTS = {
    "block": ("Bloqueado", "Bloqueado", "block"),
    "alert": ("Alerta", "Alerta", "alert"),
    "allow": ("Permitido", "Permitido", "allow"),
    "step_up": ("Segundo factor", "Segundo factor", "alert"),
}
ACTIVITY_LIMIT = 50
# Engine GETs are cached in-process: short for what the 2 s poll reads (bursts of clicks reuse it,
# the poll still sees fresh data), longer for profiles. Marking a decision normal drops everything.
LIVE_TTL = 1.0
PROFILE_TTL = 15.0
# activity verdict -> (word, color class). Normal rows are quiet (muted), never green.
ACTIVITY_VERDICTS = {"normal": ("Normal", "normal"), "review": ("Raro", "alert"), "block": ("Bloqueado", "block")}
LIFTED = ("Levantado", "Bloqueo levantado", "lifted")
ROUTE_CLASSES = {"critical": "crítico", "sensitive": "sensible"}
FORBIDDEN = "No tienes acceso a este proyecto."
MARK_ERROR = "No se pudo marcar la decisión como normal. Inténtalo de nuevo."
MARK_FORBIDDEN = "Tu rol no permite marcar decisiones como normales. Solo un admin u owner puede hacerlo."
MAX_PROFILE_ROUTES = 4


def _decorate(d: dict) -> dict:
    signals = d.get("signals") or {}
    access = d.get("access") if isinstance(d.get("access"), dict) else {}
    lifted = d.get("decision") == "block" and d.get("status") == "marked_normal"
    short, long, kind = LIFTED if lifted else VERDICTS.get(
        d.get("decision"), (str(d.get("decision", "")).capitalize(), str(d.get("decision", "")).capitalize(), "alert"))
    method, route = access.get("method"), access.get("route") or signals.get("route") or ""
    d["access"] = access
    d["verdict_short"], d["verdict_long"], d["verdict_kind"], d["lifted"] = short, long, kind, lifted
    d["time"] = str(d.get("ts", ""))[11:19]
    d["access_ts"] = access.get("ts") or d.get("ts", "")
    d["access_time"] = str(d["access_ts"])[11:19]
    d["action"] = f"{method} {route}" if method and route else route
    d["marked_normal"] = d.get("status") == "marked_normal"
    # Compared against the profile: flags the engine derived from the access.
    route_class = ROUTE_CLASSES.get(signals.get("route_class"))
    never = bool(signals.get("route_never_used"))
    d["route_flag"] = (f"{route_class}, nunca lo hizo" if route_class else "nunca lo hizo") if never else (route_class or "")
    d["new_network"] = bool(signals.get("new_network")) and bool(access.get("ip"))
    d["new_device"] = bool(signals.get("new_device")) and bool(access.get("device_id"))
    path = signals.get("path") or []
    d["path"] = " → ".join(str(p) for p in path) if path else ""
    took = _detected_in(d.get("ts"), access.get("ts"))
    d["detected"] = (f"Detectado y bloqueado en {took}" if d.get("decision") == "block" else f"Detectado en {took}") if took else ""
    # Contract 3 signals added later; anything unknown is ignored, wrong types are dropped.
    rate = signals.get("rate_per_min")
    d["rate"] = rate if isinstance(rate, (int, float)) and not isinstance(rate, bool) else None
    d["rate_spike"] = signals.get("rate_spike") is True
    d["unusual_hour"] = signals.get("unusual_hour") is True
    d["new_transition"] = signals.get("new_transition") is True
    return d


def _block_effect(access_ip: str | None, listed_ips: set[str] | None) -> str:
    """What an open block does, from the engine's blocklist. The IP is named only
    when it is listed; with no blocklist (fetch failed) nothing is claimed about it."""
    if listed_ips is None:
        return "La sesión está bloqueada."
    if access_ip and access_ip in listed_ips:
        return f"La sesión y la IP {access_ip} no pueden entrar."
    if access_ip:
        return "La sesión no puede seguir. La IP es la del dueño y no se bloquea."
    return "La sesión no puede seguir."


def _status_note(status, with_code: bool = False) -> str:
    """What happened to a blocked request. Empty if unknown. The list says it in words;
    the detail adds the HTTP code."""
    if not isinstance(status, int) or isinstance(status, bool):
        return ""
    if status == 403:
        return "detenido (403)" if with_code else "detenido"
    if 200 <= status < 400:
        return f"pasó antes del bloqueo ({status})" if with_code else "pasó antes del bloqueo"
    return str(status)


def _decorate_activity(a: dict) -> dict:
    # An unknown verdict is shown as "Raro" rather than hidden as normal.
    word, kind = ACTIVITY_VERDICTS.get(a.get("verdict"), ACTIVITY_VERDICTS["review"])
    method, route = a.get("method"), a.get("route") or ""
    a["verdict_word"], a["verdict_kind"] = word, kind
    a["time"] = str(a.get("ts", ""))[11:19]
    a["action"] = f"{method} {route}" if method and route else route
    # The request's HTTP status says what the block did to it: stopped (403) or it passed first.
    a["status_note"] = _status_note(a.get("status")) if kind == "block" else ""
    # Every row has its own URL, keyed by event: the row you click is the row that stays selected.
    a["href"] = f"/actividad/{quote(str(a['event_id']), safe='')}" if a.get("event_id") else None
    return a


def _watching(activity: list[dict]) -> str:
    """Empty-state sentence with real counts. Only the window is known, so a
    full window is worded as "los últimos N"."""
    n, users = len(activity), len({a.get("user_id") for a in activity})
    if n == 0:
        return "Todavía no hay accesos. Veridian está listo para vigilar."
    window = f"los últimos {n} accesos" if n >= ACTIVITY_LIMIT else _count(n, "acceso", "accesos")
    return f"Todo normal. Veridian está vigilando {window} de {_count(users, 'usuario', 'usuarios')}."


def _detected_in(decision_ts, access_ts) -> str:
    """Seconds from the triggering access to the decision, in Spanish ("" if unknown).

    This measures detection only: the block itself is applied on the user's next
    request, after the middleware's blocklist poll (~2 s).
    """
    try:
        secs = (datetime.fromisoformat(decision_ts) - datetime.fromisoformat(access_ts)).total_seconds()
    except (TypeError, ValueError):
        return ""
    if secs < 0:
        return ""
    return seconds_text(secs)


def _hours(hours: list) -> str:
    if len(hours) == 2:
        return f"{int(hours[0]):02d}:00 – {int(hours[1]):02d}:00 UTC"
    return ", ".join(f"{int(h):02d}:00" for h in hours) + " UTC"


def _days(n) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        return ""
    return f"{n} día de historial" if n == 1 else f"{n} días de historial"


# Sidebar groups: (title, [(key, href, label, full_page)]). `full_page` links leave the live shell, so the
# boosted/instant navigation skips them. Ajustes joins when its page exists.
NAV_GROUPS = (
    ("ACCESOS", (("resumen", "/", "Resumen", False), ("actividad", "/actividad", "Actividad", False),
                 ("usuarios", "/usuarios", "Usuarios", False), ("rutas", "/rutas", "Rutas críticas", False))),
    ("PROYECTO", (("claves", "/claves", "Claves de API", True), ("webhook", "/webhook", "Webhook", True))),
    ("ORGANIZACIÓN", (("proyectos", "/proyectos", "Proyectos", True), ("miembros", "/miembros", "Miembros", True),
                      ("auditoria", "/auditoria", "Auditoría", True))),
)
# Links only an admin or owner is offered (the engine refuses the rest anyway). With the role unknown they stay hidden.
MANAGER_ONLY = frozenset({"claves", "webhook", "auditoria"})


def nav_for(role: str | None):
    """The sidebar groups for a role (`owner`, `admin`, `viewer`, or None when it could not be read)."""
    allowed = role in ("owner", "admin")
    groups = ((title, tuple(i for i in items if allowed or i[0] not in MANAGER_ONLY)) for title, items in NAV_GROUPS)
    return tuple((title, items) for title, items in groups if items)
DEFAULT_UTC_OFFSET_MIN = -240  # Caracas, until the browser tells its own (cookie `vtz`, minutes east of UTC)
NO_USERS = "El motor todavía no entrega la lista de usuarios."
NO_ROUTES_ENDPOINT = "Este motor no expone su clasificación de rutas."
ROUTE_GROUPS = (("critical", "Críticas"), ("sensitive", "Sensibles"))
ROUTE_CLASS_LABELS = {"critical": "crítica", "sensitive": "sensible", "normal": "normal"}
ROUTE_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
ROUTE_RE = re.compile(r"^/\S{0,299}$")
ROUTE_INVALID = "La ruta tiene que empezar con / y no llevar espacios (por ejemplo /payouts/{id})."
ROUTE_FORBIDDEN = "Tu rol no permite cambiar la clasificación. Solo un admin u owner puede hacerlo."
ROUTE_GONE = "Esa ruta ya no estaba clasificada."
ROUTE_FAILED = "No se pudo guardar el cambio. Inténtalo de nuevo."


def _utc_offset(request: Request) -> int:
    try:
        minutes = int(request.cookies.get("vtz", ""))
    except ValueError:
        return DEFAULT_UTC_OFFSET_MIN
    return minutes if -14 * 60 <= minutes <= 14 * 60 else DEFAULT_UTC_OFFSET_MIN


def _shell(decisions: list[dict], activity: list[dict] | None) -> dict:
    """What the sidebar shows on every page: open blocks and the newest access."""
    opened = sum(1 for d in decisions if d.get("decision") == "block" and not d.get("marked_normal"))
    return {"nav_blocks": opened,
            "nav_blocks_text": f"{_count(opened, 'bloqueo abierto', 'bloqueos abiertos')}" if opened else "",
            "last_access": activity[0].get("ts") if activity else None}


def create_app(engine_url: str | None = None, token: str | None = None,
               http_client: httpx.AsyncClient | None = None,
               clock: Callable[[], float] = time.monotonic,
               now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
               settings: Settings | None = None,
               google_transport: httpx.AsyncBaseTransport | None = None,
               github_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """`token` is the service credential for the engine; `google_transport` and `github_transport` replace the
    network to Google and GitHub (tests)."""
    settings = settings or Settings.from_env()
    settings = dataclasses.replace(
        settings, engine_url=(engine_url or settings.engine_url).rstrip("/"),
        panel_token=token if token is not None else settings.panel_token)
    base, bearer = settings.engine_url, settings.panel_token
    client = http_client or httpx.AsyncClient(timeout=3.0)
    templates.env.globals["asset_version"] = ASSET_VERSION
    app = FastAPI(title="Veridian panel", docs_url=None, redoc_url=None, openapi_url=None)
    auth = auth_module.install(app, settings, templates, client, google_transport, github_transport)
    # Registered on the routers below, in this order: configured -> session (+CSRF) -> project.
    app.router.dependencies.append(Depends(auth.configured_guard))
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    # Render's health check: a plain Starlette route, outside the configured guard, so a missing secret
    # closes the pages (503) without failing every deploy. Says nothing about the configuration.
    app.add_route("/healthz", lambda _: PlainTextResponse("ok", headers={"Cache-Control": "no-store"}))
    if settings.configured:
        from starlette.middleware.sessions import SessionMiddleware
        app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="session",
                           max_age=auth_module.SESSION_MAX_AGE_S, same_site="lax",
                           https_only=settings.public_url.startswith("https"))
    cache = TTLCache(clock)
    accounts = accounts_module.Accounts(base, bearer, client, templates, cache, auth.session_guard)
    app.include_router(auth.public_router())
    app.include_router(auth.session_router())
    app.include_router(accounts.router())
    panel = APIRouter(dependencies=[Depends(auth.session_guard), Depends(auth.project_guard),
                                    Depends(accounts.switcher_guard)])

    def engine_headers() -> dict[str, str]:
        who = auth_module.current.get()
        return {"Authorization": f"Bearer {bearer}", "X-Veridian-Actor": who.person, "X-Veridian-Project": who.project}

    async def engine_get(path: str, ttl: float, params: dict | None = None) -> httpx.Response:
        headers = engine_headers()
        # Keyed by person and project: the app serves everyone, so a cached read must never cross either.
        key = (headers["X-Veridian-Actor"], headers["X-Veridian-Project"], path, tuple(sorted((params or {}).items())))
        return await cache.get(key, ttl, lambda: client.get(f"{base}{path}", params=params, headers=headers))

    async def load_decisions() -> tuple[list[dict], str | None]:
        try:
            r = await engine_get("/v1/decisions", LIVE_TTL, {"limit": 50})
        except httpx.HTTPError:
            return [], "No se pudo conectar con el motor. Reintentando…"
        if r.status_code == 401:
            return [], "El motor rechazó el token del panel (revisa VERIDIAN_PANEL_TOKEN)."
        if r.status_code == 403:
            return [], FORBIDDEN
        if r.status_code != 200:
            return [], f"El motor respondió con error {r.status_code}. Reintentando…"
        try:
            decisions = r.json()["decisions"]
            return [_decorate(d) for d in decisions], None
        except (ValueError, KeyError, TypeError, AttributeError):
            return [], "Respuesta inesperada del motor. Reintentando…"

    async def load_activity() -> list[dict] | None:
        """None when the engine has no /v1/activity (or it fails): the panel then
        shows the decisions list, with no error."""
        try:
            r = await engine_get("/v1/activity", LIVE_TTL, {"limit": ACTIVITY_LIMIT})
            if r.status_code != 200:
                return None
            items = [a for a in r.json()["activity"] if isinstance(a, dict)]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return None
        return [_decorate_activity(a) for a in items]

    async def load_blocklist() -> dict | None:
        """The engine's blocklist ({sessions, ips, ...}), or None if it cannot be read."""
        try:
            r = await engine_get("/v1/blocklist", LIVE_TTL)
            if r.status_code != 200:
                return None
            body = r.json()
            return {"sessions": list(body.get("sessions") or []), "ips": [str(ip) for ip in body["ips"]]}
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return None

    async def load_summary(since: datetime, until: datetime) -> dict | None:
        """Contract 6 for the given range; None when the engine does not have it (yet) or it is malformed."""
        try:
            r = await engine_get("/v1/summary", LIVE_TTL, {"since": iso_z(since), "until": iso_z(until)})
            if r.status_code != 200:
                return None
            body = r.json()
            ints = ("accesses", "users", "reviewed_by_ai")
            ba = body["blocks_active"]
            if not all(isinstance(body[k], int) and not isinstance(body[k], bool) for k in ints):
                return None
            if not all(isinstance(ba[k], int) for k in ("decisions", "sessions", "ips")) or not isinstance(body["by_hour"], list):
                return None
            return body
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return None

    async def load_users() -> list[dict] | None:
        """Contract 7; None when the engine does not have it (yet)."""
        try:
            r = await engine_get("/v1/users", LIVE_TTL)
            if r.status_code != 200:
                return None
            return [u for u in r.json()["users"] if isinstance(u, dict)]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return None

    async def load_routes() -> list[dict] | None:
        """Contract 8; None when the engine does not have it (or it is malformed)."""
        try:
            r = await engine_get("/v1/routes", LIVE_TTL)
            if r.status_code != 200:
                return None
            items = r.json()["routes"]
            if not isinstance(items, list):
                return None
            return [x for x in items if isinstance(x, dict) and x.get("class") in dict(ROUTE_GROUPS)]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return None

    async def load_profile(user_id: str) -> dict | None:
        try:
            r = await engine_get(f"/v1/users/{quote(user_id, safe='')}/profile", PROFILE_TTL)
            if r.status_code != 200:
                return None
            p = r.json()
            return p if isinstance(p, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    def user_url(user_id: str | None) -> str:
        # user_id is opaque and comes from the customer's app: escape it fully.
        return f"/users/{quote(user_id, safe='')}" if user_id else "/actividad"

    def decision_url(d: dict) -> str:
        return f"{user_url(d.get('user_id'))}/decisions/{quote(str(d.get('id', '')), safe='')}"

    async def shell(request: Request, template: str, section: str, ctx: dict, status: int = 200):
        """A page inside the sidebar shell: the switcher card (looked up alongside the page's reads), the role-aware
        navigation, the flash message and the CSRF token."""
        lookup = getattr(request.state, "switcher", None)  # started by the guard
        switcher = await lookup if lookup is not None else None
        return templates.TemplateResponse(request, template, {
            "refresh": REFRESH_SECONDS, "nav_groups": nav_for(switcher["role_key"] if switcher else None),
            "section": section, "switcher": switcher, "flash": request.session.pop("flash", None),
            "csrf": request.session.get("csrf", ""), "email": request.session.get("email", ""), **ctx},
            status_code=status)

    async def page(request: Request, template: str, section: str, ctx: dict, engine_ms: float, t0_render: float):
        response = await shell(request, template, section, ctx)
        response.headers["Server-Timing"] = (
            f"engine;dur={engine_ms:.1f}, render;dur={(time.perf_counter() - t0_render) * 1000:.1f}")
        return response

    async def core(*extra):
        """Decisions, activity and blocklist, plus any extra awaitables, all at once."""
        (decisions, engine_error), activity, blocklist, *rest = await asyncio.gather(
            load_decisions(), load_activity(), load_blocklist(), *extra)
        for d in decisions:
            d["href"] = decision_url(d)
        if engine_error is not None:
            activity = None
        return decisions, engine_error, activity, blocklist, rest

    async def render_activity(request: Request, user: str | None, decision_id: str | None, event_id: str | None,
                              error: str | None):
        # Independent engine calls run together. The profile of the user in the URL starts with them
        # too; on `/actividad` the selected user is only known once the decisions are in.
        t0 = time.perf_counter()
        role_lookup = asyncio.ensure_future(accounts.role(request))  # to offer "Esto era normal" only to admin and owner
        guess = asyncio.ensure_future(load_profile(user)) if user else None
        decisions, engine_error, activity, blocklist, _ = await core()
        blocked_ips = set(blocklist["ips"]) if blocklist is not None else None
        row = next((a for a in activity or [] if a.get("event_id") == event_id), None) if event_id else None
        if row is not None and row.get("decision_id"):
            decision_id = row["decision_id"]
            by_user = [d for d in decisions if d.get("id") == decision_id]
            current = by_user[0] if by_user else None
        elif row is not None:
            current = None  # a normal access: shown as the access it is, no verdict
        else:
            row = None
            by_user = [d for d in decisions if d.get("user_id") == user]
            current = (next((d for d in by_user if d.get("id") == decision_id), None)
                       or (by_user[0] if by_user else None)
                       or (decisions[0] if decisions else None))
        if activity is not None and row is not None:
            row["selected"] = True  # exactly the row that was clicked
        elif activity is not None and current is not None:
            # By decision URL: the access that triggered it (else the newest row of it).
            mine = [a for a in activity if a.get("decision_id") == current.get("id")]
            trigger = [a for a in mine if a.get("event_id") in (current.get("event_ids") or [])] or mine[:1]
            for a in trigger:
                a["selected"] = True
        if current is not None and row is not None and row.get("event_id") not in (current.get("event_ids") or []):
            # The detail describes another request than the row that was clicked: say so.
            note = _status_note(row.get("status"), with_code=True) if row.get("verdict_kind") == "block" else ""
            what = "del bloqueo" if row.get("verdict_kind") == "block" else "de la decisión"
            lead = f"Este acceso: {note} · parte" if note else "Este acceso: parte"
            current["selected_note"] = {"text": f"{lead} {what} de las", "ts": current.get("ts")}
        profile = None
        if current is not None and current.get("decision") == "block" and not current["marked_normal"]:
            current["block_effect"] = _block_effect((current.get("access") or {}).get("ip"), blocked_ips)
        raw = None
        if current is not None:
            raw = await (guess if guess is not None and current.get("user_id") == user
                         else load_profile(current.get("user_id", "")))
        if guess is not None and not guess.done():
            guess.cancel()
        role = await role_lookup
        engine_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        if current is not None:
            if raw is not None:
                hours = raw.get("hours") or []
                routes = list(raw.get("routes") or [])
                usual = raw.get("usual_max_rate_per_min")
                profile = {**raw, "usual_rate": usual if isinstance(usual, int) and not isinstance(usual, bool) and usual > 0 else None,
                           "hours_text": _hours(hours) if hours else "",
                           "hours_utc": ",".join(str(int(h)) for h in hours),
                           "days_text": _days(raw.get("history_days")),
                           "routes_shown": routes[:MAX_PROFILE_ROUTES],
                           "routes_more": max(0, len(routes) - MAX_PROFILE_ROUTES)}
        if row is not None:
            refresh_url = row["href"]
        else:
            refresh_url = decision_url(current) if current else "/actividad"
        return await page(request, "actividad.html", "actividad", {
            "decisions": decisions, "activity": activity, "access": row if current is None else None,
            "watching": _watching(activity) if activity is not None else "", "engine_error": engine_error,
            "current": current, "profile": profile, "viewer_role": role == "viewer",
            "notice": MARK_FORBIDDEN if error == "forbidden" else MARK_ERROR if error else None,
            "refresh_url": refresh_url, **_shell(decisions, activity),
        }, engine_ms, t1)

    async def render_overview(request: Request):
        t0 = time.perf_counter()
        offset = _utc_offset(request)
        since, until = local_day_window(now(), offset)
        decisions, engine_error, activity, blocklist, (summary, users) = await core(
            load_summary(since, until), load_users())
        engine_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        overview = build_overview(summary=summary, users=users, activity=activity, decisions=decisions,
                                  blocklist=blocklist, since=since, until=until, offset_min=offset,
                                  engine_error=engine_error)
        return await page(request, "resumen.html", "resumen", {
            "overview": overview, "engine_error": engine_error, "refresh_url": "/",
            **_shell(decisions, activity)}, engine_ms, t1)

    async def render_users(request: Request):
        t0 = time.perf_counter()
        decisions, engine_error, activity, blocklist, (users,) = await core(load_users())
        engine_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        open_blocks = [d for d in decisions if d.get("decision") == "block" and not d.get("marked_normal")]
        rows = user_rows(users, decisions, activity, open_blocks) if users is not None else None
        return await page(request, "usuarios.html", "usuarios", {
            "rows": rows, "no_users": NO_USERS, "engine_error": engine_error, "refresh_url": "/usuarios",
            **_shell(decisions, activity)}, engine_ms, t1)

    async def render_routes(request: Request):
        t0 = time.perf_counter()
        role_lookup = asyncio.ensure_future(accounts.role(request))  # viewers get the list without the controls
        decisions, engine_error, activity, blocklist, (routes,) = await core(load_routes())
        role = await role_lookup
        engine_ms = (time.perf_counter() - t0) * 1000
        groups = None if routes is None else [
            (label, rows) for cls, label in ROUTE_GROUPS
            if (rows := [{"method": str(x.get("method")), "route": str(x.get("route")), "class": cls}
                         for x in routes if x["class"] == cls])]
        return await page(request, "rutas.html", "rutas", {
            "groups": groups, "no_endpoint": NO_ROUTES_ENDPOINT, "engine_error": engine_error, "refresh_url": "/rutas",
            "can_edit": role != "viewer", "methods": ROUTE_METHODS, "class_labels": ROUTE_CLASS_LABELS,
            **_shell(decisions, activity)}, engine_ms, time.perf_counter())

    def route_rule_path() -> str:
        return f"/v1/projects/{quote(auth_module.current.get().project, safe='')}/routes/rule"

    def route_refused(status: int | None) -> str:
        return ROUTE_FORBIDDEN if status == 403 else ROUTE_GONE if status == 404 else             ROUTE_INVALID if status == 422 else ROUTE_FAILED

    @panel.post("/rutas/guardar")
    async def save_route(request: Request, method: str = Form(""), route: str = Form(""), route_class: str = Form("", alias="class")):
        route = route.strip()
        if method not in ROUTE_METHODS or not ROUTE_RE.match(route) or route_class not in ROUTE_CLASS_LABELS:
            accounts.flash(request, "err", ROUTE_INVALID)
            return accounts.back("/rutas")
        status, _ = await accounts.write(request, "PUT", route_rule_path(),
                                         {"method": method, "route": route, "class": route_class})
        if status == 200:
            accounts.flash(request, "ok", f"Ruta guardada: {method} {route} es {ROUTE_CLASS_LABELS[route_class]}.")
        else:
            accounts.flash(request, "err", route_refused(status))
        return accounts.back("/rutas")

    @panel.post("/rutas/quitar")
    async def remove_route(request: Request, method: str = Form(""), route: str = Form("")):
        if method not in ROUTE_METHODS or not ROUTE_RE.match(route):
            accounts.flash(request, "err", ROUTE_INVALID)
            return accounts.back("/rutas")
        r = None
        try:
            r = await client.delete(f"{base}{route_rule_path()}", params={"method": method, "route": route},
                                    headers=engine_headers())
        except httpx.HTTPError:
            pass
        cache.invalidate()
        if r is not None and r.status_code == 200:
            accounts.flash(request, "ok", f"Ruta quitada: {method} {route} ahora es normal.")
        else:
            accounts.flash(request, "err", route_refused(None if r is None else r.status_code))
        return accounts.back("/rutas")

    @panel.get("/", response_class=HTMLResponse)
    async def resumen(request: Request):
        return await render_overview(request)

    @panel.get("/usuarios", response_class=HTMLResponse)
    async def usuarios(request: Request):
        return await render_users(request)

    @panel.get("/rutas", response_class=HTMLResponse)
    async def rutas(request: Request):
        return await render_routes(request)

    # Decorators register bottom-up: the more specific route must come first.
    @panel.get("/actividad", response_class=HTMLResponse)
    @panel.get("/actividad/{event_id:path}", response_class=HTMLResponse)
    async def actividad(request: Request, event_id: str | None = None, error: str | None = None):
        return await render_activity(request, None, None, event_id or None, error or None)

    @panel.get("/users/{user:path}", response_class=HTMLResponse)
    @panel.get("/users/{user:path}/decisions/{decision_id}", response_class=HTMLResponse)
    async def by_user(request: Request, user: str, decision_id: str | None = None, error: str | None = None):
        return await render_activity(request, user, decision_id, None, error or None)

    @panel.post("/decisions/{decision_id}/normal")
    async def mark_normal(request: Request, decision_id: str, user: str | None = Form(None)):
        failure = "mark"
        try:
            r = await client.post(f"{base}/v1/decisions/{quote(decision_id, safe='')}/normal", headers=engine_headers())
            failure = None if r.status_code == 200 else "forbidden" if r.status_code == 403 else "mark"
        except httpx.HTTPError:
            pass
        cache.invalidate()  # whatever the engine answered, what was cached may no longer be true
        if request.headers.get("HX-Request") == "true":
            # htmx swaps the returned page in place: same selected decision, same error text.
            return await render_activity(request, user, decision_id, None, failure)
        url = user_url(user)
        if failure:
            url += "?" + urlencode({"error": failure})
        return RedirectResponse(url, status_code=303)

    panel.include_router(Team(accounts, shell, now).router())
    app.include_router(panel)
    return app


app = create_app()
