import httpx
import pytest

from demo_app.main import create_app, session_public_id
from fake_engine import create_app as create_engine

TOKEN = "tok-demo"
PASSWORD = "s3cret-demo"
EMAIL = "ana@facturas.demo"


def make_demo(engine_client=None, trusted_proxies=0):
    engine = create_engine(tokens={TOKEN}, seed=[{
        "user_id": "u_42", "ips": ["190.202.14.7"],
        "routes": ["GET /invoices", "GET /invoices/{id}", "GET /payouts/bank-account"],
    }])
    ec = engine_client or httpx.AsyncClient(transport=httpx.ASGITransport(app=engine), base_url="http://engine")
    return create_app(
        engine_url="http://engine", token=TOKEN, password=PASSWORD,
        trusted_proxies=trusted_proxies, http_client=ec, background=False,
    )


def browser(app, peer="190.202.14.7", headers=None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 4000)),
        base_url="http://demo", headers=headers or {}, follow_redirects=False,
    )


async def login(c, password=PASSWORD):
    return await c.post("/login", data={"email": EMAIL, "password": password})


async def test_protected_pages_redirect_to_login():
    async with browser(make_demo()) as c:
        for path in ("/invoices", "/invoices/1", "/invoices/export", "/payouts/bank-account"):
            r = await c.get(path)
            assert r.status_code == 303 and r.headers["location"] == "/login"


async def test_login_page_and_bad_password():
    async with browser(make_demo()) as c:
        assert (await c.get("/login")).status_code == 200
        r = await login(c, "wrong")
        assert r.status_code == 401
        assert "sid" not in r.cookies
        assert (await c.get("/invoices")).status_code == 303


async def test_login_then_browse_invoices():
    async with browser(make_demo()) as c:
        r = await login(c)
        assert r.status_code == 303 and r.headers["location"] == "/invoices"
        r = await c.get("/invoices")
        assert r.status_code == 200 and "Facturas" in r.text
        r = await c.get("/invoices/1")
        assert r.status_code == 200
        assert (await c.get("/invoices/9999")).status_code == 404


async def test_export_is_csv():
    async with browser(make_demo()) as c:
        await login(c)
        r = await c.get("/invoices/export")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert r.text.splitlines()[0].startswith("id,")


async def test_bank_account_view_and_change():
    async with browser(make_demo()) as c:
        await login(c)
        assert (await c.get("/payouts/bank-account")).status_code == 200
        r = await c.post("/payouts/bank-account", data={"bank": "Banco Nuevo", "account_number": "01340000009999999999"})
        assert r.status_code == 200
        r = await c.get("/payouts/bank-account")
        assert "Banco Nuevo" in r.text


async def test_logout_ends_session():
    async with browser(make_demo()) as c:
        await login(c)
        r = await c.post("/logout")
        assert r.status_code == 303
        assert (await c.get("/invoices")).status_code == 303


async def test_opening_logout_as_an_address_ends_the_session_too():
    async with browser(make_demo()) as c:
        await login(c)
        r = await c.get("/logout")
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert (await c.get("/invoices")).status_code == 303


async def test_session_id_reported_is_not_the_cookie_secret():
    app = make_demo()
    async with browser(app) as c:
        await login(c)
        secret = c.cookies["sid"]
        await c.get("/invoices")
    events = list(app.queue)
    assert events and all(secret not in str(e) for e in events)
    assert {e["session_id"] for e in events} == {session_public_id(secret)}
    assert {e["route"] for e in events} >= {"/login", "/invoices"}


async def test_invoice_route_is_reported_as_template():
    app = make_demo()
    async with browser(app) as c:
        await login(c)
        await c.get("/invoices/3")
    assert "/invoices/{id}" in {e["route"] for e in app.queue}


async def test_end_to_end_attacker_blocked_owner_fine():
    app = make_demo(trusted_proxies=1)
    owner_h = {"X-Forwarded-For": "1.1.1.1, 190.202.14.7"}
    atk_h = {"X-Forwarded-For": "190.202.14.7, 203.0.113.9"}  # forged first value
    async with browser(app, headers=owner_h) as owner, browser(app, headers=atk_h) as atk:
        await login(owner)
        await login(atk)  # attacker has the owner's password
        r = await atk.post("/payouts/bank-account", data={"bank": "Evil", "account_number": "1"})
        assert r.status_code == 200
        await app.flush()
        await app.refresh_blocklist()
        assert (await atk.get("/invoices")).status_code == 403
        assert (await owner.get("/invoices")).status_code == 200


async def test_stylesheet_is_served_and_never_reported_as_an_event():
    app = make_demo()
    async with browser(app) as c:
        r = await c.get("/static/app.css")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
        assert "set-cookie" not in r.headers
        assert 'href="/static/app.css?v=' in (await c.get("/login")).text
    assert list(app.queue) == []


async def test_login_form_hints_for_password_managers_and_submit_feedback():
    async with browser(make_demo()) as c:
        html = (await c.get("/login")).text
        assert 'name="email" type="email" autocomplete="username"' in html
        assert 'name="password" type="password" autocomplete="current-password"' in html
        assert 'data-busy="Entrando…"' in html


async def test_bank_account_number_is_numeric_with_pattern_and_hint():
    async with browser(make_demo()) as c:
        await login(c)
        html = (await c.get("/payouts/bank-account")).text
        assert 'inputmode="numeric"' in html and 'pattern="[0-9]{10,20}"' in html
        assert 'autocomplete="off"' in html and 'title="' in html
        assert 'data-busy="Guardando…"' in html


async def test_demo_serves_busy_script_and_css_has_focus_and_press_states():
    async with browser(make_demo()) as c:
        css = (await c.get("/static/app.css")).text
        assert ":focus-visible" in css and "scale(0.97)" in css and "prefers-reduced-motion" in css
        assert (await c.get("/static/busy.js")).status_code == 200


BLOCKED_TEXT = "Detectamos un acceso inusual y bloqueamos esta acción."


async def test_bank_form_is_sent_by_fetch_and_has_a_hidden_blocked_notice():
    async with browser(make_demo()) as c:
        await login(c)
        html = (await c.get("/payouts/bank-account")).text
        assert 'data-fetch data-blocked-notice="blocked-notice"' in html
        assert f'<div class="msg error" id="blocked-notice" role="alert" hidden>{BLOCKED_TEXT}</div>' in html
        js = (await c.get("/static/busy.js")).text
        # the page reads the SDK's unchanged 403 body and shows the notice instead of raw JSON
        assert 'r.status === 403' in js and 'body.error === "blocked"' in js
        # the SDK's unlock message, when present, replaces the generic notice
        assert "body.message" in js and "textContent" in js


def recording_engine(sent: list):
    def handler(request):
        if request.url.path == "/v1/events":
            import json
            sent.extend(json.loads(request.content)["events"])
            return httpx.Response(202, json={"accepted": 1})
        if request.url.path == "/v1/blocklist":
            return httpx.Response(200, json={"version": 1, "users": [], "sessions": [], "ips": []})
        if request.url.path == "/v1/routes":
            return httpx.Response(200, json={"routes": []})
        return httpx.Response(200, json={})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://engine")


async def test_demo_users_report_the_unlock_inbox_only_when_configured(monkeypatch):
    for inbox, expected in ((None, None), ("dueno@ejemplo.com", "dueno@ejemplo.com")):
        if inbox:
            monkeypatch.setenv("DEMO_UNLOCK_EMAIL", inbox)
        else:
            monkeypatch.delenv("DEMO_UNLOCK_EMAIL", raising=False)
        sent: list = []
        demo = make_demo(engine_client=recording_engine(sent))
        async with browser(demo) as c:
            await login(c)
            await c.get("/invoices")
        await demo.flush()
        mine = [e for e in sent if e.get("user_id") == "u_42"]
        assert mine and all(e.get("user_email") == expected for e in mine)


async def test_blocked_bank_change_still_answers_the_sdk_json_the_page_reads():
    app = make_demo(trusted_proxies=1)
    atk_h = {"X-Forwarded-For": "190.202.14.7, 203.0.113.9"}
    async with browser(app, headers=atk_h) as atk:
        await login(atk)
        await atk.post("/payouts/bank-account", data={"bank": "Evil", "account_number": "1"})
        await app.flush()
        await app.refresh_blocklist()
        r = await atk.post("/payouts/bank-account", data={"bank": "Evil", "account_number": "1"},
                           headers={"Accept": "text/html"})
        assert r.status_code == 403 and r.json()["error"] == "blocked"


# --- one-tap demo preparation (/demo/preparar) -------------------------------

PREPARE_KEY = "k-prepare"
PANEL_TOKEN = "panel-tok"
PHONE_IP = "190.202.14.7"


class StubEngine:
    """The engine endpoints the preparation calls, enough to drive it in tests."""

    def __init__(self, owner=True, blocked_session="s_attacker"):
        self.owner = owner
        self.calls: list[tuple[str, str]] = []
        self.events: list[dict] = []
        self.blocklist = {"version": 3, "users": [], "sessions": [blocked_session], "ips": []}

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json as _json
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        auth = request.headers.get("authorization", "")
        panel = auth == f"Bearer {PANEL_TOKEN}"
        if method == "GET" and path == "/v1/blocklist":
            return httpx.Response(200, json=self.blocklist)
        if method == "POST" and path == "/v1/events":
            if auth != f"Bearer {TOKEN}":
                return httpx.Response(401)
            self.events += _json.loads(request.content)["events"]
            return httpx.Response(202, json={"accepted": len(self.events)})
        if not panel:
            return httpx.Response(401)
        if method == "POST" and path == "/v1/people/login":
            return httpx.Response(200, json={"person": {"id": "p_1"}})
        if method == "GET" and path == "/v1/projects/demo":
            return httpx.Response(200, json={"role": "owner" if self.owner else "viewer"})
        if method == "DELETE" and path == "/v1/projects/demo/data":
            self.events.clear()
            self.blocklist = {"version": 4, "users": [], "sessions": [], "ips": []}
            return httpx.Response(200, json={"deleted": True})
        if method == "GET" and path == "/v1/users/u_42/profile":
            mine = [e for e in self.events if e["user_id"] == "u_42"]
            days = {e["ts"][:10] for e in mine}
            return httpx.Response(200, json={
                "state": "normal", "history_days": len(days),
                "networks": sorted({".".join(e["ip"].split(".")[:3]) + ".0/24" for e in mine}),
                "devices": sorted({e["device_id"] for e in mine if e["device_id"]}),
            })
        return httpx.Response(404)


def make_prepared_demo(stub, prepare_key=PREPARE_KEY):
    ec = httpx.AsyncClient(transport=httpx.MockTransport(stub.handler), base_url="http://engine")
    return create_app(
        engine_url="http://engine", token=TOKEN, password=PASSWORD, http_client=ec, background=False,
        prepare_key=prepare_key, panel_token=PANEL_TOKEN,
    )


async def test_prepare_page_is_off_without_the_key_env_and_404_with_a_wrong_key():
    stub = StubEngine()
    async with browser(make_prepared_demo(stub, prepare_key=None)) as c:
        assert (await c.get("/demo/preparar", params={"clave": "anything"})).status_code == 404
        assert (await c.post("/demo/preparar", params={"clave": "anything"})).status_code == 404
    async with browser(make_prepared_demo(stub)) as c:
        assert (await c.get("/demo/preparar")).status_code == 404
        assert (await c.get("/demo/preparar", params={"clave": "wrong"})).status_code == 404
        assert (await c.post("/demo/preparar", params={"clave": "wrong"})).status_code == 404
    assert ("DELETE", "/v1/projects/demo/data") not in stub.calls


async def test_prepare_page_shows_one_button_issues_the_device_cookie_and_hides_secrets():
    async with browser(make_prepared_demo(StubEngine())) as c:
        r = await c.get("/demo/preparar", params={"clave": PREPARE_KEY})
        assert r.status_code == 200
        assert "Preparar demo" in r.text and '<form method="post"' in r.text
        assert PREPARE_KEY not in r.text and PANEL_TOKEN not in r.text
        assert "vrd_did=" in r.headers.get("set-cookie", "")


async def test_prepare_without_the_device_cookie_explains_and_touches_nothing():
    stub = StubEngine()
    async with browser(make_prepared_demo(stub)) as c:
        r = await c.post("/demo/preparar", params={"clave": PREPARE_KEY})
        assert r.status_code == 400 and "dispositivo" in r.text
    assert ("DELETE", "/v1/projects/demo/data") not in stub.calls and stub.events == []


async def test_prepare_erases_seeds_the_phone_and_clears_the_blocklist_in_memory():
    stub = StubEngine()
    app = make_prepared_demo(stub)
    await app.refresh_blocklist()
    assert app.blocked["sessions"] == {"s_attacker"}
    async with browser(app, peer=PHONE_IP) as c:
        await c.get("/demo/preparar", params={"clave": PREPARE_KEY})  # the phone gets its device cookie
        device = c.cookies["vrd_did"]
        r = await c.post("/demo/preparar", params={"clave": PREPARE_KEY})
        assert r.status_code == 200, r.text
        assert "Listo: 1 red, 1 dispositivo, 14 días" in r.text
        assert PREPARE_KEY not in r.text and PANEL_TOKEN not in r.text
    assert stub.calls.index(("DELETE", "/v1/projects/demo/data")) < stub.calls.index(("POST", "/v1/events"))
    assert ("POST", "/v1/admin/reset") not in stub.calls
    seeded = stub.events
    assert seeded and {e["ip"] for e in seeded} == {PHONE_IP} and {e["device_id"] for e in seeded} == {device}
    assert {e["user_id"] for e in seeded} == {"u_42"}
    assert app.blocked == {"users": set(), "sessions": set(), "ips": set()}
    # the preparation pages are never reported as the presenter's activity
    assert list(app.queue) == []


async def test_prepare_reports_an_engine_problem_without_leaking_the_token():
    stub = StubEngine(owner=False)
    async with browser(make_prepared_demo(stub)) as c:
        await c.get("/demo/preparar", params={"clave": PREPARE_KEY})
        r = await c.post("/demo/preparar", params={"clave": PREPARE_KEY})
        assert r.status_code == 502 and "owner" in r.text and PANEL_TOKEN not in r.text
    assert ("DELETE", "/v1/projects/demo/data") not in stub.calls
