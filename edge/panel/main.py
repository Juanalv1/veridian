"""Panel: lists the engine's decisions and refreshes itself.

Env: VERIDIAN_URL (engine base URL), VERIDIAN_TOKEN (Bearer token).
"""

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
REFRESH_SECONDS = 2


def _signals_text(signals: dict) -> list[str]:
    labels = []
    if signals.get("new_network"):
        labels.append("red nueva")
    if signals.get("new_device"):
        labels.append("dispositivo nuevo")
    if signals.get("route"):
        cls = signals.get("route_class")
        labels.append(f"ruta {signals['route']}" + (f" ({cls})" if cls else ""))
    if signals.get("route_never_used"):
        labels.append("ruta nunca usada")
    return labels


def create_app(engine_url: str | None = None, token: str | None = None,
               http_client: httpx.AsyncClient | None = None) -> FastAPI:
    base = (engine_url or os.environ.get("VERIDIAN_URL", "http://localhost:8000")).rstrip("/")
    bearer = token if token is not None else os.environ.get("VERIDIAN_TOKEN", "")
    client = http_client or httpx.AsyncClient(timeout=3.0)
    app = FastAPI(title="Veridian panel", docs_url=None, redoc_url=None, openapi_url=None)

    async def load() -> tuple[list[dict], str | None]:
        try:
            r = await client.get(f"{base}/v1/decisions", params={"limit": 50},
                                 headers={"Authorization": f"Bearer {bearer}"})
        except httpx.HTTPError:
            return [], "No se pudo conectar con el motor. Reintentando…"
        if r.status_code == 401:
            return [], "El motor rechazó el token (revisa VERIDIAN_TOKEN)."
        if r.status_code != 200:
            return [], f"El motor respondió con error {r.status_code}. Reintentando…"
        try:
            decisions = r.json()["decisions"]
        except (ValueError, KeyError, TypeError):
            return [], "Respuesta inesperada del motor. Reintentando…"
        for d in decisions:
            d["signal_labels"] = _signals_text(d.get("signals") or {})
        return decisions, None

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        decisions, error = await load()
        return templates.TemplateResponse(
            request, "index.html",
            {"decisions": decisions, "error": error, "refresh": REFRESH_SECONDS},
        )

    return app


app = create_app()
