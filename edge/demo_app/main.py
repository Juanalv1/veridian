"""Demo app: a small server-rendered invoicing startup protected by Veridian.

Routes match the table in docs/REPARTO.md. Data lives in memory.

Env (see edge/README.md):
  VERIDIAN_URL, VERIDIAN_TOKEN  where the engine is and how to authenticate
                                (VERIDIAN_API_KEY also works for the key)
  TRUSTED_PROXIES               number of our own proxies in front (default 0)
  DEMO_PASSWORD                 owner password; the default is for local use only
"""

import csv
import hashlib
import hmac
import io
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from veridian_sdk import VeridianMiddleware

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
ASSET_VERSION = "4"

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


def create_app(
    engine_url: str | None = None,
    token: str | None = None,
    password: str | None = None,
    trusted_proxies: int | None = None,
    http_client=None,
    background: bool = True,
) -> VeridianMiddleware:
    """Returns the app wrapped by VeridianMiddleware (an ASGI app)."""
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

    def user_id_of(request: Request):
        uid = getattr(request.state, "user_id", None)
        if uid:
            return uid
        return sessions.get(request.cookies.get(COOKIE, ""))

    def session_id_of(request: Request):
        sid = getattr(request.state, "session_id", None)
        if sid:
            return sid
        cookie_value = request.cookies.get(COOKIE)
        return session_public_id(cookie_value) if cookie_value else None

    return VeridianMiddleware(
        app,
        # VERIDIAN_TOKEN is the name the deployed demo already uses; the SDK reads VERIDIAN_API_KEY.
        api_key=token or os.environ.get("VERIDIAN_TOKEN") or None,
        base_url=engine_url or os.environ.get("VERIDIAN_URL", "http://localhost:8000"),
        user=user_id_of,
        session=session_id_of,
        trusted_proxies=trusted_proxies if trusted_proxies is not None
        else int(os.environ.get("TRUSTED_PROXIES", "0")),
        http_client=http_client,
        background=background,
    )


app = create_app()
