"""Panel: users, decisions and profile from the engine, refreshed every 2 s.

Env: VERIDIAN_URL (engine base URL), VERIDIAN_TOKEN (Bearer token).
"""

import asyncio
import os
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
REFRESH_SECONDS = 2

# decision -> (label, css modifier)
BADGES = {
    "block": ("BLOQUEADO", "block"),
    "alert": ("ALERTA", "alert"),
    "allow": ("PERMITIDO", "allow"),
    "step_up": ("SEGUNDO FACTOR", "alert"),
}
LIFTED_BADGE = ("BLOQUEO LEVANTADO", "lifted")
MARK_ERROR = "No se pudo marcar la decisión como normal. Inténtalo de nuevo."


def _chips(signals: dict) -> list[dict]:
    chips = []
    if signals.get("new_network"):
        chips.append({"text": "red nueva", "critical": False})
    if signals.get("new_device"):
        chips.append({"text": "dispositivo nuevo", "critical": False})
    route_class = signals.get("route_class")
    if route_class:
        used = "nunca usada" if signals.get("route_never_used") else "ya usada"
        chips.append({"text": f"{route_class} · {used}", "critical": route_class == "critical"})
    elif signals.get("route_never_used"):
        chips.append({"text": "ruta nunca usada", "critical": False})
    return chips


def _decorate(d: dict) -> dict:
    signals = d.get("signals") or {}
    lifted = d.get("decision") == "block" and d.get("status") == "marked_normal"
    label, kind = LIFTED_BADGE if lifted else BADGES.get(d.get("decision"), (str(d.get("decision", "")).upper(), "alert"))
    path = signals.get("path") or []
    d["badge_label"], d["badge_kind"], d["lifted"] = label, kind, lifted
    d["time"] = str(d.get("ts", ""))[11:19]
    d["route"] = signals.get("route") or ""
    d["chips"] = _chips(signals)
    d["path"] = " → ".join(str(p) for p in path) if path else ""
    d["can_mark_normal"] = d.get("status") != "marked_normal"
    return d


def _hours(hours: list) -> str:
    if len(hours) == 2:
        return f"{int(hours[0]):02d}:00 – {int(hours[1]):02d}:00 UTC"
    return ", ".join(f"{int(h):02d}:00" for h in hours) + " UTC"


def create_app(engine_url: str | None = None, token: str | None = None,
               http_client: httpx.AsyncClient | None = None) -> FastAPI:
    base = (engine_url or os.environ.get("VERIDIAN_URL", "http://localhost:8000")).rstrip("/")
    bearer = token if token is not None else os.environ.get("VERIDIAN_TOKEN", "")
    client = http_client or httpx.AsyncClient(timeout=3.0)
    headers = {"Authorization": f"Bearer {bearer}"}
    app = FastAPI(title="Veridian panel", docs_url=None, redoc_url=None, openapi_url=None)

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

    @app.get("/", response_class=HTMLResponse)
    @app.get("/users/{user:path}", response_class=HTMLResponse)
    async def index(request: Request, user: str | None = None, error: str | None = None):
        decisions, engine_error = await load_decisions()
        user_ids = list(dict.fromkeys(d.get("user_id", "") for d in decisions))
        profiles = dict(zip(user_ids, await asyncio.gather(*(load_profile(u) for u in user_ids))))
        users = [{"id": u, "href": user_url(u), "state": (profiles[u] or {}).get("state", "normal"),
                  "blocked": (profiles[u] or {}).get("state") == "blocked"} for u in user_ids]
        selected = user if user in profiles else (user_ids[0] if user_ids else None)
        profile = profiles.get(selected) if selected else None
        if profile is not None:
            hours = profile.get("hours") or []
            profile = {**profile, "hours_text": _hours(hours) if hours else "",
                       "hours_utc": ",".join(str(int(h)) for h in hours)}
        return templates.TemplateResponse(request, "index.html", {
            "decisions": decisions, "engine_error": engine_error, "refresh": REFRESH_SECONDS,
            "users": users, "selected": selected, "profile": profile,
            "notice": MARK_ERROR if error else None,
            "refresh_url": user_url(selected),
        })

    @app.post("/decisions/{decision_id}/normal")
    async def mark_normal(decision_id: str, user: str | None = Form(None)):
        ok = False
        try:
            r = await client.post(f"{base}/v1/decisions/{quote(decision_id, safe='')}/normal", headers=headers)
            ok = r.status_code == 200
        except httpx.HTTPError:
            pass
        url = user_url(user)
        if not ok:
            url += "?" + urlencode({"error": "mark"})
        return RedirectResponse(url, status_code=303)

    return app


app = create_app()
