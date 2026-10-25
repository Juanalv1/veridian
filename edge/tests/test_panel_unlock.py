"""Panel: the public unlock page /desbloquear/{token} (docs/DESBLOQUEO-POR-CORREO.md).

No login. GET only shows (mail scanners open links): it never calls the engine's POST. The button POSTs with a
per-token form nonce. Over the real fake engine, plus canned answers for hostile brand values."""

import re

import httpx
from fastapi.testclient import TestClient

from panel.main import UnlockPathFilter
from tests.panel_support import PANEL_TOKEN, make_panel
from tests.test_panel_accounts import link, new_engine, panel_of, project_a, text
from tests.test_panel_audit import SDK_KEY

SEED = [{"user_id": "u_42", "ips": ["190.202.14.7"], "routes": ["GET /invoices"]}]
ATTACK = {"id": "e_atk", "ts": "2026-10-26T10:14:03Z", "user_id": "u_42", "session_id": "s_atk", "ip": "203.0.113.9",
          "user_agent": "Chrome 141 / Windows", "method": "POST", "route": "/payouts/bank-account", "status": 200,
          "bytes": 1}
BRAND = {"brand_name": "Acme", "brand_logo_url": "https://cdn.acme.com/logo.png", "brand_color": "#1A2B3C",
         "brand_support_url": "https://acme.com/help", "brand_support_email": "help@acme.com",
         "self_unlock_enabled": "on"}


class Recorder(httpx.AsyncBaseTransport):
    """Passes calls to the fake engine and remembers (method, path, headers)."""

    def __init__(self, engine, calls):
        self.inner, self.calls = httpx.ASGITransport(app=engine), calls

    async def handle_async_request(self, request):
        self.calls.append((request.method, request.url.path, dict(request.headers), str(request.url)))
        return await self.inner.handle_async_request(request)


def setup(brand=BRAND):
    """(engine, public client, calls, token): a block with an unlock email, and a panel with no session."""
    engine = new_engine(seed=SEED)
    owner = panel_of(engine)
    owner.post("/marca", data=brand)
    sdk = TestClient(engine)
    r = sdk.post("/v1/check", json={"event": {**ATTACK, "user_email": "ana@example.com"}}, headers=SDK_KEY).json()
    assert r["verdict"] == "block"
    calls = []
    public = make_panel(httpx.AsyncClient(transport=Recorder(engine, calls), base_url="http://engine"), login=False)
    return engine, public, calls, engine.state.outbox[-1]["token"]


def state_of(engine):
    return engine.state.projects[project_a(engine)].state


def nonce_of(html):
    return re.search(r'name="nonce" value="([^"]+)"', html).group(1)


def unlock(public, token, **kw):
    html = public.get(f"/desbloquear/{token}").text
    return public.post(f"/desbloquear/{token}", data={"nonce": nonce_of(html)}, csrf=False,
                       headers={"Origin": public.origin}, **kw)


# --- GET: shows, never unlocks -------------------------------------------------------------------------------------


def test_the_page_opens_without_login_and_shows_the_brand_and_the_access():
    engine, public, calls, token = setup()
    r = public.get(f"/desbloquear/{token}")
    assert r.status_code == 200
    page = text(r.text)
    assert "Acme" in page and "Fui yo, desbloquear" in page
    assert "26 de octubre de 2026, 10:14 UTC" in page and "Chrome en Windows" in page
    assert re.search(r'<img[^>]*src="https://cdn\.acme\.com/logo\.png"[^>]*alt="Acme"', r.text)
    assert "--unlock-accent:#1A2B3C" in r.text
    assert "help@acme.com" in page and 'href="https://acme.com/help"' in r.text
    assert "Veridian" not in page


def test_the_page_never_shows_the_full_ip_user_or_email():
    engine, public, calls, token = setup()
    html = public.get(f"/desbloquear/{token}").text
    for secret in ("203.0.113.9", "u_42", "ana@example.com", "s_atk", token):
        assert secret not in html, secret


def test_get_never_unlocks_and_only_reads_from_the_engine():
    engine, public, calls, token = setup()
    version = state_of(engine).version
    for _ in range(3):
        assert public.get(f"/desbloquear/{token}").status_code == 200
    unlock_calls = [c for c in calls if c[1] == "/v1/self-unlock"]
    assert unlock_calls and all(c[0] == "GET" for c in unlock_calls)
    assert state_of(engine).version == version and state_of(engine).sessions == ["s_atk"]
    assert state_of(engine).decisions[0]["status"] == "open"


def test_the_token_goes_to_the_engine_in_a_header_with_the_panel_credential_and_no_actor():
    engine, public, calls, token = setup()
    public.get(f"/desbloquear/{token}")
    method, path, headers, url = next(c for c in calls if c[1] == "/v1/self-unlock")
    assert headers["x-veridian-unlock-token"] == token and headers["authorization"] == f"Bearer {PANEL_TOKEN}"
    assert "x-veridian-actor" not in headers and "x-veridian-project" not in headers
    assert token not in url


def test_the_page_is_private_and_not_indexed():
    engine, public, calls, token = setup()
    for r in (public.get(f"/desbloquear/{token}"), public.get("/desbloquear/unknown")):
        assert "no-store" in r.headers["cache-control"]
        assert "noindex" in r.headers["x-robots-tag"]
        assert r.headers["referrer-policy"] == "no-referrer"
        assert re.search(r'<meta name="robots" content="[^"]*noindex', r.text)


def test_the_support_line_is_omitted_when_nothing_is_configured():
    engine, public, calls, token = setup({"brand_name": "Acme", "self_unlock_enabled": "on"})
    page = text(public.get(f"/desbloquear/{token}").text)
    assert "Necesitas ayuda" not in page and "<img" not in public.get(f"/desbloquear/{token}").text


# --- POST: unlocks -------------------------------------------------------------------------------------------------


def test_the_button_unlocks_and_says_so():
    engine, public, calls, token = setup()
    before = state_of(engine).version
    r = unlock(public, token)
    assert r.status_code == 200
    assert "Listo, ya puedes volver a la app" in text(r.text) and "Acme" in text(r.text)
    assert "no-store" in r.headers["cache-control"] and r.headers["referrer-policy"] == "no-referrer"
    st = state_of(engine)
    assert st.version == before + 1 and st.sessions == [] and st.decisions[0]["status"] == "marked_normal"
    assert token not in r.text


def test_a_second_click_says_the_link_was_used_and_does_not_unlock_again():
    engine, public, calls, token = setup()
    unlock(public, token)
    version = state_of(engine).version
    r = unlock(public, token)
    assert r.status_code == 409 and "Este enlace ya se usó" in text(r.text) and "Fui yo, desbloquear" not in text(r.text)
    assert state_of(engine).version == version


def test_post_without_the_nonce_or_with_another_tokens_nonce_does_nothing():
    engine, public, calls, token = setup()
    other = TestClient(engine).post("/v1/check", json={"event": {**ATTACK, "id": "e2", "session_id": "s2",
                                                                  "user_email": "ana@example.com"}},
                                    headers=SDK_KEY)
    for data in ({}, {"nonce": "x"}, {"nonce": nonce_of(public.get(f"/desbloquear/{token}").text) + "0"}):
        r = public.post(f"/desbloquear/{token}", data=data, csrf=False, headers={"Origin": public.origin})
        assert r.status_code == 403, data
    assert state_of(engine).sessions == ["s_atk"]
    assert not any(c[0] == "POST" and c[1] == "/v1/self-unlock" for c in calls)


def test_a_form_rendered_for_one_token_cannot_unlock_another():
    engine, public, calls, token = setup()
    nonce = nonce_of(public.get(f"/desbloquear/{token}").text)
    r = public.post("/desbloquear/other-token", data={"nonce": nonce}, csrf=False, headers={"Origin": public.origin})
    assert r.status_code == 403 and state_of(engine).sessions == ["s_atk"]


def test_a_cross_site_post_is_refused_even_with_the_nonce():
    engine, public, calls, token = setup()
    nonce = nonce_of(public.get(f"/desbloquear/{token}").text)
    r = public.post(f"/desbloquear/{token}", data={"nonce": nonce}, csrf=False, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and state_of(engine).sessions == ["s_atk"]


def test_origin_null_is_accepted_because_no_referrer_makes_browsers_send_it():
    engine, public, calls, token = setup()
    nonce = nonce_of(public.get(f"/desbloquear/{token}").text)
    r = public.post(f"/desbloquear/{token}", data={"nonce": nonce}, csrf=False, headers={"Origin": "null"})
    assert r.status_code == 200 and state_of(engine).sessions == []


# --- the other states ----------------------------------------------------------------------------------------------


def test_an_expired_link_says_so_and_has_no_button():
    engine, public, calls, token = setup()
    import hashlib
    engine.state.unlock_tokens[hashlib.sha256(token.encode()).hexdigest()]["expires_at"] = "2000-01-01T00:00:00Z"
    r = public.get(f"/desbloquear/{token}")
    assert r.status_code == 200
    page = text(r.text)
    assert "El enlace venció" in page and "24 horas" in page and "Fui yo, desbloquear" not in page
    assert "<form" not in r.text
    r = public.post(f"/desbloquear/{token}", data={"nonce": "n"}, csrf=False, headers={"Origin": public.origin})
    assert r.status_code == 403  # no nonce was ever issued for a link that is not valid


def test_a_link_an_admin_already_resolved_is_closed():
    engine, public, calls, token = setup()
    panel_of(engine).post("/decisions/d_1/normal", data={"user": "u_42"})
    page = text(public.get(f"/desbloquear/{token}").text)
    assert "Este acceso ya no está bloqueado" in page and "Fui yo, desbloquear" not in page


def test_a_used_link_says_so_on_get():
    engine, public, calls, token = setup()
    unlock(public, token)
    page = text(public.get(f"/desbloquear/{token}").text)
    assert "Este enlace ya se usó" in page and "Fui yo, desbloquear" not in page


def test_an_unknown_link_is_a_neutral_404_without_any_brand():
    engine, public, calls, token = setup()
    r = public.get("/desbloquear/does-not-exist")
    assert r.status_code == 404
    assert "No encontramos este enlace" in text(r.text) and "Acme" not in r.text
    assert public.post("/desbloquear/does-not-exist", data={"nonce": "n"}, csrf=False,
                       headers={"Origin": public.origin}).status_code == 403


def test_a_malformed_token_never_reaches_the_engine():
    engine, public, calls, token = setup()
    calls.clear()
    for bad in ("x" * 300, "a b", "%3Cscript%3E"):
        assert public.get(f"/desbloquear/{bad}").status_code == 404
    assert not calls


def test_an_engine_that_cannot_answer_gives_a_retry_message_not_a_stack_trace():
    def boom(_):
        raise httpx.ConnectError("down")
    public = make_panel(httpx.AsyncClient(transport=httpx.MockTransport(boom)), login=False)
    r = public.get("/desbloquear/abc")
    assert r.status_code == 503 and "No pudimos verificar el enlace ahora" in text(r.text)
    assert "no-store" in r.headers["cache-control"]


# --- hostile brand values ------------------------------------------------------------------------------------------


def canned(brand):
    body = {"state": "valid", "brand": brand, "access": {"time": "1 de enero de 2026, 10:00 UTC", "device": "Chrome en Windows"},
            "expires_at": "2999-01-01T00:00:00Z"}
    return make_panel(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))),
                      login=False)


def test_hostile_brand_values_are_escaped_or_dropped():
    public = canned({"name": '<script>alert(1)</script>', "logo_url": "javascript:alert(1)",
                     "color": 'red;}</style><script>x()</script>', "support_url": "javascript:alert(2)",
                     "support_email": '"><img src=x onerror=alert(3)>'})
    html = public.get("/desbloquear/abc").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    for bad in ("javascript:", "onerror=alert(3)>", "x()", "</style><script>"):
        assert bad not in html, bad
    assert "--unlock-accent" not in html and "<img" not in html


def test_an_http_logo_is_not_rendered():
    html = canned({"name": "Acme", "logo_url": "http://cdn.acme.com/l.png", "color": None,
                   "support_url": None, "support_email": None}).get("/desbloquear/abc").text
    assert "<img" not in html


# --- the access log never keeps the token --------------------------------------------------------------------------


def test_the_access_log_redacts_the_token():
    import logging
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/desbloquear/SECRET-token_1", "1.1", 200), None)
    assert UnlockPathFilter().filter(record) is True
    assert "SECRET" not in record.getMessage() and "/desbloquear/<redacted>" in record.getMessage()
    plain = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                              ("1.2.3.4:5", "GET", "/resumen", "1.1", 200), None)
    UnlockPathFilter().filter(plain)
    assert "/resumen" in plain.getMessage()
