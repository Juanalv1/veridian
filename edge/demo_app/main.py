"""Demo app: a small server-rendered invoicing startup protected by Veridian.

Routes match the table in docs/REPARTO.md. Data lives in memory.

Env (see edge/README.md):
  VERIDIAN_URL, VERIDIAN_TOKEN  where the engine is and how to authenticate
                                (VERIDIAN_API_KEY also works for the key)
  TRUSTED_PROXIES               number of our own proxies in front (default 0)
  DEMO_PASSWORD                 owner password; the default is for local use only
  DEMO_UNLOCK_EMAIL             real inbox reported as every demo user's email, so the engine
                                can send the self-unlock email (the demo addresses are fake)
  DEMO_PREPARE_PIN              turns on /preparar (one-tap rehearsal reset, asks this PIN once);
                                unset, the page answers 404
  VERIDIAN_PANEL_TOKEN          panel credential that page uses to erase the project's data
  DEMO_PROJECT_ID, DEMO_USER_ID, DEMO_ACTOR_EMAIL   as in engine/scripts/prepare_demo.py
"""

import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from veridian_sdk import VeridianMiddleware, resolve_client_ip

from .prepare import PrepareError, Settings, prepare

PREPARE_PATH = "/preparar"
PREPARE_COOKIE = "demo_prep"
DEVICE_COOKIE = "vrd_did"  # the SDK's device cookie (veridian_sdk.middleware.DEVICE_COOKIE)
MAX_PIN_FAILURES = 10  # wrong PINs in a row before the PIN is refused for PIN_LOCK_SECONDS
PIN_LOCK_SECONDS = 300
LOCAL_DEFAULT_PASSWORD = "demo1234"  # local demo only, never a real secret
COOKIE = "sid"

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def date_label(iso: str) -> str:
    """'2026-10-03' -> '3 oct 2026'."""
    y, m, d = iso.split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


templates.env.filters["money"] = money
templates.env.filters["date_label"] = date_label
# Cache-busting query for /static assets: bump when app.css or busy.js change.
ASSET_VERSION = "7"

USERS = {
    "ana@facturas.demo": {"id": "u_42", "name": "Ana Torres", "company": "Taller Torres C.A."},
    "luis@facturas.demo": {"id": "u_7", "name": "Luis Pérez", "company": "Pérez Logística"},
}

CLIENTS = ["Inversiones Andes", "Comercial Caribe", "Panadería La Esquina", "Grupo Orinoco",
           "Ferretería Central", "Estudio Mérida"]


def _make_invoices(seed: int) -> list[dict]:
    items = []
    for n in range(1, 9):
        items.append({
            "id": n,
            "number": f"F-{n:04d}",
            "client": CLIENTS[(n + seed) % len(CLIENTS)],
            "date": f"2026-10-{n + 2:02d}",
            "amount": 120.0 * n + seed,
            "status": "Pagada" if n % 3 else "Pendiente",
        })
    return items


def session_public_id(cookie_value: str) -> str:
    """What the engine sees as session_id: a hash, never the cookie secret."""
    return "s_" + hashlib.sha256(cookie_value.encode()).hexdigest()[:12]


class BlockedPage:
    """Shows a page instead of the SDK's 403 JSON when a browser navigates while blocked.

    Only real navigations (Sec-Fetch-Mode: navigate) are rewritten: fetch calls such as the
    bank form keep the SDK's unchanged body, which busy.js reads. Attributes fall through to
    the wrapped VeridianMiddleware (flush, blocked, refresh_blocklist...).
    """

    def __init__(self, app: VeridianMiddleware):
        self.app = app

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or dict(scope["headers"]).get(b"sec-fetch-mode") != b"navigate":
            return await self.app(scope, receive, send)
        held: dict = {}

        async def capture(message):
            if message["type"] == "http.response.start" and message["status"] == 403:
                held["start"], held["body"] = message, b""
                return
            if "start" not in held:
                return await send(message)
            held["body"] += message.get("body", b"")
            if message.get("more_body"):
                return
            try:
                answer = json.loads(held["body"])
            except ValueError:
                answer = None
            if not (isinstance(answer, dict) and answer.get("error") == "blocked"):
                await send(held["start"])
                return await send({"type": "http.response.body", "body": held["body"]})
            html = templates.get_template("blocked.html").render(
                user=None, asset_version=ASSET_VERSION, unlock_message=answer.get("message")).encode()
            await send({"type": "http.response.start", "status": 403, "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(html)).encode()),
                (b"cache-control", b"no-store"),
            ]})
            await send({"type": "http.response.body", "body": html})

        await self.app(scope, receive, capture)


def create_app(
    engine_url: str | None = None,
    token: str | None = None,
    password: str | None = None,
    trusted_proxies: int | None = None,
    http_client=None,
    background: bool = True,
    prepare_pin: str | None = None,
    panel_token: str | None = None,
) -> BlockedPage:
    """Returns the app wrapped by VeridianMiddleware (an ASGI app)."""
    prepare_pin = prepare_pin or os.environ.get("DEMO_PREPARE_PIN") or None
    panel_token = panel_token or os.environ.get("VERIDIAN_PANEL_TOKEN") or ""
    password = password or os.environ.get("DEMO_PASSWORD") or LOCAL_DEFAULT_PASSWORD
    sessions: dict[str, str] = {}  # cookie value -> user_id
    invoices = {u["id"]: _make_invoices(i) for i, u in enumerate(USERS.values())}
    bank = {u["id"]: {"bank": "Banco de Venezuela", "account_number": "01020000001234567890"}
            for u in USERS.values()}
    users_by_id = {u["id"]: u for u in USERS.values()}

    app = FastAPI(title="Facturas Demo", docs_url=None, redoc_url=None, openapi_url=None)
    # /static is excluded from VeridianMiddleware (see its exclude_paths): assets
    # are not user actions and need no blocklist check.
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def current_user(request: Request) -> dict | None:
        uid = sessions.get(request.cookies.get(COOKIE, ""))
        return users_by_id.get(uid) if uid else None

    def render(request: Request, name: str, status_code: int = 200, **ctx):
        return templates.TemplateResponse(request, name, {"user": current_user(request), "asset_version": ASSET_VERSION, **ctx},
                                          status_code=status_code)

    login_redirect = RedirectResponse  # alias for readability below

    @app.get("/")
    async def home(request: Request):
        return login_redirect("/invoices" if current_user(request) else "/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if current_user(request):
            return login_redirect("/invoices", status_code=303)
        return render(request, "login.html")

    @app.post("/login")
    async def login(request: Request, email: str = Form(""), password_in: str = Form("", alias="password")):
        user = USERS.get(email.strip().lower())
        ok = hmac.compare_digest(password_in.encode(), password.encode())
        if not user or not ok:
            return render(request, "login.html", status_code=401, error="Correo o contraseña incorrectos.")
        cookie_value = secrets.token_urlsafe(24)
        sessions[cookie_value] = user["id"]
        # The session cookie is not on this request yet; tell the middleware who this is.
        request.state.user_id = user["id"]
        request.state.session_id = session_public_id(cookie_value)
        resp = login_redirect("/invoices", status_code=303)
        resp.set_cookie(COOKIE, cookie_value, httponly=True, samesite="lax")
        return resp

    @app.get("/logout")  # opening /logout as an address signs out too, instead of a 405
    @app.post("/logout")
    async def logout(request: Request):
        sessions.pop(request.cookies.get(COOKIE, ""), None)
        resp = login_redirect("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    def guard(request: Request):
        return None if current_user(request) else login_redirect("/login", status_code=303)

    @app.get("/invoices", response_class=HTMLResponse)
    async def invoice_list(request: Request):
        if (r := guard(request)) is not None:
            return r
        mine = invoices[current_user(request)["id"]]
        pending = sum(i["amount"] for i in mine if i["status"] == "Pendiente")
        return render(request, "invoices.html", invoices=mine, active="invoices",
                      total=sum(i["amount"] for i in mine), pending=pending)

    # Declared before /invoices/{id} so "export" is not read as an id.
    @app.get("/invoices/export")
    async def invoice_export(request: Request):
        if (r := guard(request)) is not None:
            return r
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["id", "number", "client", "date", "amount", "status"])
        for i in invoices[current_user(request)["id"]]:
            w.writerow([i["id"], i["number"], i["client"], i["date"], i["amount"], i["status"]])
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="facturas.csv"'})

    @app.get("/invoices/{id}", response_class=HTMLResponse)
    async def invoice_detail(request: Request, id: int):
        if (r := guard(request)) is not None:
            return r
        found = next((i for i in invoices[current_user(request)["id"]] if i["id"] == id), None)
        if found is None:
            return render(request, "message.html", status_code=404, title="Factura no encontrada",
                          message="Esta factura no existe o no pertenece a tu empresa.")
        return render(request, "invoice.html", invoice=found, active="invoices")

    def masked(number: str) -> str:
        return "•••• " + number[-4:]

    @app.get("/payouts/bank-account", response_class=HTMLResponse)
    async def bank_view(request: Request):
        if (r := guard(request)) is not None:
            return r
        acct = bank[current_user(request)["id"]]
        return render(request, "bank_account.html", bank=acct["bank"], masked=masked(acct["account_number"]),
                      active="bank")

    @app.post("/payouts/bank-account", response_class=HTMLResponse)
    async def bank_change(request: Request, bank_name: str = Form("", alias="bank"),
                          account_number: str = Form("")):
        if (r := guard(request)) is not None:
            return r
        acct = bank[current_user(request)["id"]]
        acct["bank"] = bank_name.strip()[:60] or acct["bank"]
        acct["account_number"] = account_number.strip()[:34] or acct["account_number"]
        return render(request, "bank_account.html", bank=acct["bank"],
                      masked=masked(acct["account_number"]), saved=True, active="bank")

    # --- one-tap demo preparation (see prepare.py) ---------------------------
    # Not excluded from the middleware: the GET must issue the device cookie the POST seeds.
    # user/session return None on this path, so nothing here is reported as activity.
    pin_failures = {"count": 0, "until": 0.0}

    def pin_pass() -> str:
        # What the cookie holds after a correct PIN: derived from it, never the PIN itself.
        return hmac.new(prepare_pin.encode(), b"veridian-demo-prepare", hashlib.sha256).hexdigest()

    def prepare_enabled() -> None:
        if not prepare_pin:
            raise HTTPException(status_code=404)

    def has_pass(request: Request) -> bool:
        return hmac.compare_digest(request.cookies.get(PREPARE_COOKIE, "").encode(), pin_pass().encode())

    @app.get(PREPARE_PATH, response_class=HTMLResponse)
    async def prepare_page(request: Request):
        prepare_enabled()
        return render(request, "demo_prepare.html", ask_pin=not has_pass(request))

    @app.post(PREPARE_PATH, response_class=HTMLResponse)
    async def prepare_run(request: Request, pin: str = Form("")):
        prepare_enabled()
        authorized = has_pass(request)
        if not authorized:
            if time.monotonic() < pin_failures["until"]:
                return render(request, "demo_prepare.html", status_code=429, ask_pin=True,
                              error="Demasiados intentos. Espera unos minutos.")
            if not hmac.compare_digest(pin.strip().encode(), prepare_pin.encode()):
                pin_failures["count"] += 1
                if pin_failures["count"] >= MAX_PIN_FAILURES:
                    pin_failures.update(count=0, until=time.monotonic() + PIN_LOCK_SECONDS)
                return render(request, "demo_prepare.html", status_code=403, ask_pin=True,
                              error="PIN incorrecto.")
            pin_failures["count"] = 0
        response = await run_preparation(request)
        if not authorized:
            response.set_cookie(PREPARE_COOKIE, pin_pass(), httponly=True, samesite="lax",
                                secure=request.url.scheme == "https", max_age=60 * 60 * 24 * 30)
        return response

    async def run_preparation(request: Request):
        device_id = request.cookies.get(DEVICE_COOKIE)
        if not device_id:
            return render(request, "demo_prepare.html", status_code=400,
                          error="Este navegador no tiene la cookie de dispositivo de Veridian. Recarga esta "
                                "página (con cookies activadas) y vuelve a pulsar el botón.")
        if not panel_token:
            return render(request, "demo_prepare.html", status_code=500,
                          error="Falta VERIDIAN_PANEL_TOKEN en el entorno de la app.")
        settings = Settings(
            panel_token=panel_token,
            project_id=os.environ.get("DEMO_PROJECT_ID", "demo"),
            user_id=os.environ.get("DEMO_USER_ID", "u_42"),
            actor_email=os.environ.get("DEMO_ACTOR_EMAIL", "ensayo@veridian.demo"),
        )
        ip = resolve_client_ip(request.scope, middleware.trusted_proxies)
        try:
            done = await prepare(middleware, settings, ip, device_id)
        except PrepareError as error:
            return render(request, "demo_prepare.html", status_code=502, error=str(error))
        return render(request, "demo_prepare.html", done=done)

    def is_prepare_path(request: Request) -> bool:
        return request.url.path == PREPARE_PATH

    def user_id_of(request: Request):
        if is_prepare_path(request):
            return None
        uid = getattr(request.state, "user_id", None)
        if uid:
            return uid
        return sessions.get(request.cookies.get(COOKIE, ""))

    def user_email_of(request: Request):
        # The demo addresses (@facturas.demo) receive no mail, so one real inbox stands in for all.
        return os.environ.get("DEMO_UNLOCK_EMAIL") or None if user_id_of(request) else None

    def session_id_of(request: Request):
        if is_prepare_path(request):
            return None
        sid = getattr(request.state, "session_id", None)
        if sid:
            return sid
        cookie_value = request.cookies.get(COOKIE)
        return session_public_id(cookie_value) if cookie_value else None

    middleware = VeridianMiddleware(
        app,
        # VERIDIAN_TOKEN is the name the deployed demo already uses; the SDK reads VERIDIAN_API_KEY.
        api_key=token or os.environ.get("VERIDIAN_TOKEN") or None,
        base_url=engine_url or os.environ.get("VERIDIAN_URL", "http://localhost:8000"),
        user=user_id_of,
        user_email=user_email_of,
        session=session_id_of,
        trusted_proxies=trusted_proxies if trusted_proxies is not None
        else int(os.environ.get("TRUSTED_PROXIES", "0")),
        http_client=http_client,
        background=background,
    )
    return BlockedPage(middleware)


app = create_app()
