"""Panel: live activity feed and the selected decision compared against the user's profile; refreshed every 2 s.

Env: VERIDIAN_URL (engine base URL), VERIDIAN_TOKEN (Bearer token).
"""

import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
REFRESH_SECONDS = 2
# Cache-busting query for /static assets: bump when tokens.css, panel.css, panel.js or htmx.min.js change.
ASSET_VERSION = "4"

# decision -> (nav word, detail word, color class)
VERDICTS = {
    "block": ("Bloqueado", "Bloqueado", "block"),
    "alert": ("Alerta", "Alerta", "alert"),
    "allow": ("Permitido", "Permitido", "allow"),
    "step_up": ("Segundo factor", "Segundo factor", "alert"),
}
ACTIVITY_LIMIT = 50
# activity verdict -> (word, color class). Normal rows are quiet (muted), never green.
ACTIVITY_VERDICTS = {"normal": ("Normal", "normal"), "review": ("Raro", "alert"), "block": ("Bloqueado", "block")}
LIFTED = ("Levantado", "Bloqueo levantado", "lifted")
ROUTE_CLASSES = {"critical": "crítico", "sensitive": "sensible"}
MARK_ERROR = "No se pudo marcar la decisión como normal. Inténtalo de nuevo."
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
    d["blocks_ip"] = access.get("ip") if d.get("decision") == "block" else None
    return d


def _decorate_activity(a: dict, decisions: dict[str, dict]) -> dict:
    # An unknown verdict is shown as "Raro" rather than hidden as normal.
    word, kind = ACTIVITY_VERDICTS.get(a.get("verdict"), ACTIVITY_VERDICTS["review"])
    method, route = a.get("method"), a.get("route") or ""
    decision = decisions.get(a.get("decision_id"))
    a["verdict_word"], a["verdict_kind"] = word, kind
    a["time"] = str(a.get("ts", ""))[11:19]
    a["action"] = f"{method} {route}" if method and route else route
    # The contract has no status on activity rows: a block row that is not the
    # access which triggered the decision is a request the middleware rejected.
    a["rejected"] = bool(kind == "block" and decision is not None
                         and a.get("event_id") not in (decision.get("event_ids") or []))
    a["href"] = (f"/users/{quote(str(a.get('user_id') or ''), safe='')}/decisions/{quote(str(a['decision_id']), safe='')}"
                 if a.get("decision_id") and a.get("user_id") else None)
    return a


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


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
    if secs < 10 and round(secs, 1) < 10:
        text = f"{secs:.1f}".replace(".", ",")
        return text.removesuffix(",0") + " s"
    whole = round(secs)
    if whole < 60:
        return f"{whole} s"
    minutes, rest = divmod(whole, 60)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


def _hours(hours: list) -> str:
    if len(hours) == 2:
        return f"{int(hours[0]):02d}:00 – {int(hours[1]):02d}:00 UTC"
    return ", ".join(f"{int(h):02d}:00" for h in hours) + " UTC"


def _days(n) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        return ""
    return f"{n} día de historial" if n == 1 else f"{n} días de historial"


def create_app(engine_url: str | None = None, token: str | None = None,
               http_client: httpx.AsyncClient | None = None) -> FastAPI:
    base = (engine_url or os.environ.get("VERIDIAN_URL", "http://localhost:8000")).rstrip("/")
    bearer = token if token is not None else os.environ.get("VERIDIAN_TOKEN", "")
    client = http_client or httpx.AsyncClient(timeout=3.0)
    headers = {"Authorization": f"Bearer {bearer}"}
    app = FastAPI(title="Veridian panel", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    async def load_decisions() -> tuple[list[dict], str | None]:
        try:
            r = await client.get(f"{base}/v1/decisions", params={"limit": 50}, headers=headers)
        except httpx.HTTPError:
            return [], "No se pudo conectar con el motor. Reintentando…"
        if r.status_code == 401:
            return [], "El motor rechazó el token (revisa VERIDIAN_TOKEN)."
        if r.status_code != 200:
            return [], f"El motor respondió con error {r.status_code}. Reintentando…"
        try:
            decisions = r.json()["decisions"]
            return [_decorate(d) for d in decisions], None
        except (ValueError, KeyError, TypeError, AttributeError):
            return [], "Respuesta inesperada del motor. Reintentando…"

    async def load_activity(decisions: list[dict]) -> list[dict] | None:
        """None when the engine has no /v1/activity (or it fails): the panel then
        shows the decisions list, with no error."""
        try:
            r = await client.get(f"{base}/v1/activity", params={"limit": ACTIVITY_LIMIT}, headers=headers)
            if r.status_code != 200:
                return None
            items = [a for a in r.json()["activity"] if isinstance(a, dict)]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return None
        by_id = {d.get("id"): d for d in decisions}
        return [_decorate_activity(a, by_id) for a in items]

    async def load_profile(user_id: str) -> dict | None:
        try:
            r = await client.get(f"{base}/v1/users/{quote(user_id, safe='')}/profile", headers=headers)
            if r.status_code != 200:
                return None
            p = r.json()
            return p if isinstance(p, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    def user_url(user_id: str | None) -> str:
        # user_id is opaque and comes from the customer's app: escape it fully.
        return f"/users/{quote(user_id, safe='')}" if user_id else "/"

    def decision_url(d: dict) -> str:
        return f"{user_url(d.get('user_id'))}/decisions/{quote(str(d.get('id', '')), safe='')}"

    # Decorators register bottom-up: the more specific route must come first.
    async def render(request: Request, user: str | None, decision_id: str | None, error: bool):
        decisions, engine_error = await load_decisions()
        for d in decisions:
            d["href"] = decision_url(d)
        activity = await load_activity(decisions) if engine_error is None else None
        by_user = [d for d in decisions if d.get("user_id") == user]
        current = (next((d for d in by_user if d.get("id") == decision_id), None)
                   or (by_user[0] if by_user else None)
                   or (decisions[0] if decisions else None))
        profile = None
        if current is not None:
            raw = await load_profile(current.get("user_id", ""))
            if raw is not None:
                hours = raw.get("hours") or []
                routes = list(raw.get("routes") or [])
                profile = {**raw, "hours_text": _hours(hours) if hours else "",
                           "hours_utc": ",".join(str(int(h)) for h in hours),
                           "days_text": _days(raw.get("history_days")),
                           "routes_shown": routes[:MAX_PROFILE_ROUTES],
                           "routes_more": max(0, len(routes) - MAX_PROFILE_ROUTES)}
        return templates.TemplateResponse(request, "index.html", {
            "decisions": decisions, "activity": activity,
            "watching": _watching(activity) if activity is not None else "", "engine_error": engine_error, "refresh": REFRESH_SECONDS,
            "asset_version": ASSET_VERSION,
            "current": current, "profile": profile,
            "notice": MARK_ERROR if error else None,
            "refresh_url": decision_url(current) if current else "/",
        })

    @app.get("/", response_class=HTMLResponse)
    @app.get("/users/{user:path}", response_class=HTMLResponse)
    @app.get("/users/{user:path}/decisions/{decision_id}", response_class=HTMLResponse)
    async def index(request: Request, user: str | None = None, decision_id: str | None = None,
                    error: str | None = None):
        return await render(request, user, decision_id, bool(error))

    @app.post("/decisions/{decision_id}/normal")
    async def mark_normal(request: Request, decision_id: str, user: str | None = Form(None)):
        ok = False
        try:
            r = await client.post(f"{base}/v1/decisions/{quote(decision_id, safe='')}/normal", headers=headers)
            ok = r.status_code == 200
        except httpx.HTTPError:
            pass
        if request.headers.get("HX-Request") == "true":
            # htmx swaps the returned page in place: same selected decision, same error text.
            return await render(request, user, decision_id, not ok)
        url = user_url(user)
        if not ok:
            url += "?" + urlencode({"error": "mark"})
        return RedirectResponse(url, status_code=303)

    return app


app = create_app()
