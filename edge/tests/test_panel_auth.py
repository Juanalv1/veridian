"""Panel login: Google OAuth, signed session, CSRF, no open mode. See docs/DISENO-PRODUCTO.md."""

import json
import os
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from panel.config import Settings, load_dotenv
from panel.main import ASSET_VERSION, create_app
from tests.panel_support import (EMAIL, PANEL_TOKEN, PERSON_ID, PROJECT_ID, PanelClient, google_transport,
                                 login_answer, make_panel, session_of, sign_in, stub_client, test_settings)

def api_routes(routes):
    """Every APIRoute, looking inside the routers the app includes."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from api_routes(route.original_router.routes)


PUBLIC_ROUTES = {"/login", "/auth/google", "/auth/callback", "/auth/github", "/auth/github/callback",
                 "/desbloquear/{token}"}  # public: the token is the proof (see test_panel_unlock.py)


def canned(request):
    if request.url.path == "/v1/decisions":
        return httpx.Response(200, json={"decisions": []})
    if request.url.path == "/v1/activity":
        return httpx.Response(200, json={"activity": []})
    return httpx.Response(404, json={"error": "not_found"})


class Engine:
    """Canned engine that remembers every request except the login (kept in `logins`)."""

    def __init__(self, projects=((PROJECT_ID, "Demo", "owner"),), handler=canned):
        self.requests, self.logins = [], []

        def record(request):
            self.requests.append(request)
            return handler(request)
        self.client = stub_client(record, projects=projects, logins=self.logins)

    def panel(self, **kw):
        return make_panel(self.client, **kw)


def error_of(response):
    return parse_qs(urlparse(response.headers["location"]).query).get("error", [None])[0]


# --- no open mode -----------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["google_client_id", "google_client_secret", "session_secret", "panel_token"])
def test_without_auth_configuration_the_panel_refuses_to_serve(missing):
    settings = test_settings(**{missing: ""})
    engine = Engine()
    app = create_app("http://engine", settings.panel_token, http_client=engine.client, settings=settings)
    panel = PanelClient(app, follow_redirects=False)
    for path in ("/", "/actividad", "/usuarios", "/rutas", "/users/u_1", "/login", "/auth/google", "/sin-proyectos"):
        r = panel.get(path)
        assert r.status_code == 503, path
        assert "Panel sin configurar" in r.text
    assert panel.post("/decisions/d_1/normal", csrf=False).status_code == 503
    assert panel.post("/logout", csrf=False).status_code == 503
    assert engine.requests == [] and engine.logins == []
    assert panel.get("/static/panel.css").status_code == 200  # assets stay public


def test_the_unconfigured_message_names_nothing_secret():
    settings = test_settings(session_secret="", google_client_secret="topsecret")
    app = create_app("http://engine", "tok", settings=settings)
    r = TestClient(app).get("/")
    assert "topsecret" not in r.text and "gsecret" not in r.text


def test_every_route_but_the_login_ones_needs_a_session():
    engine = Engine()
    panel = engine.panel(login=False)
    routes = list(api_routes(panel.app.routes))
    assert {r.path for r in routes} >= PUBLIC_ROUTES | {"/", "/actividad", "/decisions/{decision_id}/normal", "/logout"}
    for route in routes:
        if route.path in PUBLIC_ROUTES:
            continue
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = panel.request(method, path, headers={"Origin": "http://testserver"})
            assert r.status_code in (303, 401), (method, path, r.status_code)
            if method == "GET":
                assert r.headers["location"] == "/login", path
    assert engine.requests == []


def test_an_htmx_poll_without_a_session_is_told_to_go_to_login():
    panel = Engine().panel(login=False)
    r = panel.get("/actividad", headers={"HX-Request": "true"})
    assert r.status_code == 401 and r.headers["HX-Redirect"] == "/login"


def test_a_forged_session_cookie_is_not_a_session():
    engine = Engine()
    panel = engine.panel(login=False, cookies={"session": "eyJwZXJzb24iOiAicGVyX3N0dWIifQ==.abc.def"})
    r = panel.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert engine.requests == []


# --- /login ---------------------------------------------------------------------------------------


def test_login_page_offers_google_in_spanish():
    r = Engine().panel(login=False).get("/login")
    assert r.status_code == 200 and "Continuar con Google" in r.text and 'href="/auth/google"' in r.text
    assert "Veridian" in r.text and 'lang="es"' in r.text


@pytest.mark.parametrize("code,text", [
    ("denied", "Cancelaste"), ("state", "expiró"), ("email", "correo verificado"),
    ("engine", "Veridian"), ("failed", "No pudimos")])
def test_login_page_explains_each_error(code, text):
    r = Engine().panel(login=False).get("/login", params={"error": code})
    assert text in r.text and 'role="alert"' in r.text


def test_login_page_ignores_unknown_error_codes_without_echoing_them():
    r = Engine().panel(login=False).get("/login", params={"error": "<script>x</script>"})
    assert "<script>x</script>" not in r.text and "No pudimos" in r.text


def test_a_signed_in_person_visiting_login_goes_to_the_panel():
    r = Engine().panel().get("/login")
    assert r.status_code == 303 and r.headers["location"] == "/"


# --- /auth/google ---------------------------------------------------------------------------------


def test_auth_google_redirects_to_google_with_a_state_kept_in_the_session():
    panel = Engine().panel(login=False)
    r = panel.get("/auth/google")
    url = urlparse(r.headers["location"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert r.status_code == 303 and url.netloc == "accounts.google.com"
    assert query["client_id"] == "gid" and query["response_type"] == "code"
    assert query["redirect_uri"] == "http://testserver/auth/callback" and "email" in query["scope"]
    assert session_of(panel)["oauth_state"] == query["state"] and len(query["state"]) >= 32
    assert parse_qs(urlparse(panel.get("/auth/google").headers["location"]).query)["state"][0] != query["state"]


def test_the_redirect_uri_comes_from_public_url_when_set():
    settings = test_settings(public_url="https://panel.example.com")
    panel = Engine().panel(login=False, settings=settings)
    r = panel.get("/auth/google")
    assert parse_qs(urlparse(r.headers["location"]).query)["redirect_uri"] == ["https://panel.example.com/auth/callback"]


# --- /auth/callback -------------------------------------------------------------------------------


def test_callback_signs_in_and_asks_the_engine_for_the_persons_projects():
    engine = Engine()
    panel = engine.panel(login=False, transport=google_transport(sub="g-9", email="Ana@Example.com", name="Ana T"))
    r = sign_in(panel)
    assert r.status_code == 303 and r.headers["location"] == "/"
    (login,) = engine.logins
    assert login.headers["authorization"] == f"Bearer {PANEL_TOKEN}"
    assert json.loads(login.content) == {
        "google_sub": "g-9", "email": "ana@example.com", "name": "Ana T"}
    session = session_of(panel)
    assert session["person"] == PERSON_ID and session["email"] == EMAIL and session["project"] == PROJECT_ID
    assert "oauth_state" not in session and len(session["csrf"]) >= 32
    assert "role" not in session


def test_callback_state_is_single_use():
    panel = Engine().panel(login=False)
    r = panel.get("/auth/google")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    assert panel.get("/auth/callback", params={"code": "c", "state": state}).headers["location"] == "/"
    replay = panel.get("/auth/callback", params={"code": "c", "state": state})
    assert error_of(replay) == "state"


@pytest.mark.parametrize("params", [{"state": "wrong"}, {"code": None}, {"state": None}])
def test_callback_rejects_a_missing_or_wrong_state_or_code(params):
    engine = Engine()
    panel = engine.panel(login=False)
    panel.get("/auth/google")
    query = {"code": "c", "state": session_of(panel)["oauth_state"], **params}
    r = panel.get("/auth/callback", params={k: v for k, v in query.items() if v is not None})
    assert r.status_code == 303 and error_of(r) == "state" and engine.logins == []
    assert "person" not in session_of(panel)


def test_callback_without_a_started_login_is_rejected():
    r = Engine().panel(login=False).get("/auth/callback", params={"code": "c", "state": "s"})
    assert error_of(r) == "state"


def test_callback_google_denied():
    panel = Engine().panel(login=False)
    panel.get("/auth/google")
    r = panel.get("/auth/callback", params={"error": "access_denied", "state": session_of(panel)["oauth_state"]})
    assert error_of(r) == "denied"


def test_callback_requires_a_verified_email_and_never_calls_the_engine_otherwise():
    engine = Engine()
    panel = engine.panel(login=False, transport=google_transport(verified=False))
    r = sign_in(panel)
    assert error_of(r) == "email" and engine.logins == [] and "person" not in session_of(panel)


def test_callback_google_failure_is_a_login_error_not_a_500():
    panel = Engine().panel(login=False, transport=google_transport(token_status=500))
    assert error_of(sign_in(panel)) == "failed"


@pytest.mark.parametrize("answer", [
    httpx.Response(401, json={"error": "unauthorized"}), httpx.Response(403, json={"error": "forbidden"}),
    httpx.Response(200, text="not json"), httpx.ConnectError("down")])
def test_callback_engine_failure_is_a_login_error_and_no_session(answer):
    def wrapper(request):
        if isinstance(answer, Exception):
            raise answer
        return answer
    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapper))
    panel = make_panel(client, login=False)
    r = sign_in(panel)
    assert r.status_code == 303 and error_of(r) == "engine" and "person" not in session_of(panel)


def test_callback_replaces_whatever_the_session_held():
    panel = Engine().panel(login=False)
    panel.get("/auth/google")
    before = session_of(panel)
    sign_in(panel)
    after = session_of(panel)
    assert "oauth_state" in before and "oauth_state" not in after


def test_a_person_without_projects_gets_the_no_projects_page():
    engine = Engine(projects=())
    panel = engine.panel(login=False)
    assert sign_in(panel).headers["location"] == "/sin-proyectos"
    for path in ("/", "/actividad", "/usuarios", "/rutas"):
        r = panel.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/sin-proyectos", path
    page = panel.get("/sin-proyectos")
    assert page.status_code == 200 and "¿Cómo se llama tu empresa?" in page.text and EMAIL in page.text
    assert 'action="/logout"' in page.text and 'name="csrf"' in page.text
    assert engine.requests == []  # the stub refuses the org lookup (no project to name): the step still shows


def test_the_no_projects_page_needs_a_session():
    r = Engine(projects=()).panel(login=False).get("/sin-proyectos")
    assert r.status_code == 303 and r.headers["location"] == "/login"


# --- what the panel sends to the engine ---------------------------------------------------------


def multi_engine(handler, people):
    """Canned engine whose login answers per google_sub: people = {sub: (person_id, [(project_id, role), ...])},
    all the projects in one org."""
    def wrapper(request):
        if request.url.path == "/v1/people/login":
            sub = json.loads(request.content)["google_sub"]
            person, projects = people[sub]
            orgs = [("org_1", projects[0][1], [(i, i) for i, _ in projects])] if projects else []
            return httpx.Response(200, json=login_answer(person=person, orgs=orgs))
        return handler(request)
    return httpx.AsyncClient(transport=httpx.MockTransport(wrapper))


def test_data_reads_carry_the_service_credential_actor_and_the_first_project(monkeypatch):
    monkeypatch.setenv("VERIDIAN_TOKEN", "legacy-token")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"decisions": [], "activity": []})

    client = multi_engine(handler, {"g-1": ("per_1", [("p_first", "owner"), ("p_second", "admin")])})
    assert make_panel(client).get("/actividad").status_code == 200
    assert seen
    for request in seen:
        assert request.headers["authorization"] == f"Bearer {PANEL_TOKEN}"
        assert request.headers["x-veridian-actor"] == "per_1"
        assert request.headers["x-veridian-project"] == "p_first"


def org_engine(handler, orgs):
    """Canned engine whose login answers with `orgs` (see `login_answer`)."""
    def wrapper(request):
        if request.url.path == "/v1/people/login":
            return httpx.Response(200, json=login_answer(person="per_1", orgs=orgs))
        return handler(request)
    return httpx.AsyncClient(transport=httpx.MockTransport(wrapper))


def test_the_panel_uses_the_first_project_of_the_first_org():
    seen = []

    def handler(request):
        seen.append(request.headers["x-veridian-project"])
        return httpx.Response(200, json={"decisions": [], "activity": []})

    client = org_engine(handler, [("org_a", "admin", [("p_a1", "A1"), ("p_a2", "A2")]),
                                  ("org_b", "owner", [("p_b1", "B1")])])
    assert make_panel(client).get("/actividad").status_code == 200
    assert seen and set(seen) == {"p_a1"}


def test_an_org_without_projects_is_skipped_an_org_alone_sends_to_the_selector_and_no_org_to_the_no_org_page():
    seen = []

    def handler(request):
        seen.append(request.headers["x-veridian-project"])
        return httpx.Response(200, json={"decisions": [], "activity": []})

    client = org_engine(handler, [("org_a", "viewer", []), ("org_b", "owner", [("p_b1", "B1")])])
    assert make_panel(client).get("/actividad").status_code == 200
    assert set(seen) == {"p_b1"}
    empty = org_engine(handler, [("org_a", "owner", [])])
    r = make_panel(empty).get("/actividad", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/proyectos"
    nothing = org_engine(handler, [])
    r = make_panel(nothing).get("/actividad", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/sin-proyectos"


def test_the_engine_denying_membership_is_shown_not_hidden():
    client = stub_client(lambda r: httpx.Response(403, json={"error": "forbidden"}))
    panel = make_panel(client)
    html = panel.get("/actividad").text
    assert "No tienes acceso" in html


def test_the_engine_rejecting_the_service_credential_is_shown():
    client = stub_client(lambda r: httpx.Response(401, json={"error": "unauthorized"}))
    html = make_panel(client).get("/actividad").text
    assert "token" in html.lower() and "VERIDIAN_PANEL_TOKEN" in html


def test_two_people_never_share_cached_engine_reads():
    calls = []

    def handler(request):
        calls.append((request.headers["x-veridian-actor"], request.headers["x-veridian-project"]))
        return httpx.Response(200, json={"decisions": [], "activity": []})

    client = multi_engine(handler, {"a": ("per_a", [("p_a", "owner")]), "b": ("per_b", [("p_b", "viewer")])})
    first = make_panel(client, login=False)
    sign_in(first, code="sub:a")
    second = PanelClient(first.app, follow_redirects=False)
    sign_in(second, code="sub:b")
    first.get("/actividad")
    second.get("/actividad")
    assert set(calls) == {("per_a", "p_a"), ("per_b", "p_b")}  # B's page was fetched for B, not served from A's cache


# --- logout ------------------------------------------------------------------------------------------


def test_logout_clears_the_session():
    panel = Engine().panel()
    r = panel.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert panel.get("/").headers["location"] == "/login"


def test_opening_logout_as_an_address_signs_out_instead_of_a_405():
    panel = Engine().panel()
    r = panel.get("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert panel.get("/").headers["location"] == "/login"


# --- CSRF --------------------------------------------------------------------------------------------


def engine_with_block():
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(200, json={})
        return canned(request)
    return Engine(handler=handler), posts


def test_a_session_post_without_the_csrf_defences_is_rejected_and_never_reaches_the_engine():
    engine, posts = engine_with_block()
    panel = engine.panel()
    token = session_of(panel)["csrf"]
    bad = [
        {},  # nothing
        {"Origin": "http://testserver"},  # Origin only
        {"X-CSRF-Token": token},  # token only: no Origin
        {"Origin": "http://evil.example", "X-CSRF-Token": token},  # foreign Origin
        {"Origin": "http://testserver", "X-CSRF-Token": "wrong"},  # wrong token
        {"Origin": "null", "X-CSRF-Token": token},
    ]
    for headers in bad:
        r = panel.post("/decisions/d_1/normal", data={"user": "u"}, headers=headers, csrf=False)
        assert r.status_code == 403, headers
    assert posts == []
    assert panel.post("/logout", csrf=False, headers={}).status_code == 403
    assert panel.get("/actividad").status_code == 200  # still signed in


def test_origin_and_header_token_let_the_post_through():
    engine, posts = engine_with_block()
    panel = engine.panel()
    r = panel.post("/decisions/d_1/normal", data={"user": "u"})
    assert r.status_code == 303 and len(posts) == 1


def test_a_form_field_can_carry_the_token_for_pages_without_js():
    engine, posts = engine_with_block()
    panel = engine.panel()
    token = session_of(panel)["csrf"]
    r = panel.post("/decisions/d_1/normal", data={"user": "u", "csrf": token},
                   headers={"Origin": "http://testserver"}, csrf=False)
    assert r.status_code == 303 and len(posts) == 1
    r = panel.post("/decisions/d_1/normal", data={"user": "u", "csrf": "wrong"},
                   headers={"Origin": "http://testserver"}, csrf=False)
    assert r.status_code == 403


def test_with_public_url_the_origin_must_be_exactly_that():
    settings = test_settings(public_url="https://panel.example.com")
    engine, posts = engine_with_block()
    panel = engine.panel(settings=settings)
    token = session_of(panel, settings.session_secret)["csrf"]
    host_origin = panel.post("/decisions/d_1/normal", data={}, csrf=False,
                             headers={"Origin": "http://testserver", "X-CSRF-Token": token})
    assert host_origin.status_code == 403 and posts == []
    ok = panel.post("/decisions/d_1/normal", data={}, csrf=False,
                    headers={"Origin": "https://panel.example.com/", "X-CSRF-Token": token})
    assert ok.status_code == 303


def test_the_token_is_per_session_and_changes_on_every_login():
    panel = Engine().panel()
    first = session_of(panel)["csrf"]
    panel.post("/logout")
    sign_in(panel)
    assert session_of(panel)["csrf"] != first


def test_pages_hand_the_token_to_htmx_and_to_forms():
    def handler(request):
        if request.url.path == "/v1/decisions":
            return httpx.Response(200, json={"decisions": [{
                "id": "d_1", "ts": "2026-10-26T10:14:05Z", "user_id": "u_1", "decision": "block", "status": "open",
                "signals": {}, "rationale": "r", "event_ids": []}]})
        return canned(request)
    panel = Engine(handler=handler).panel()
    token = session_of(panel)["csrf"]
    html = panel.get("/actividad").text
    assert f'<meta name="csrf-token" content="{token}">' in html
    form = re.search(r'<form method="post" action="/decisions/d_1/normal">(.*?)</form>', html, re.S).group(1)
    assert f'name="csrf" value="{token}"' in form
    js = panel.get("/static/panel.js").text
    assert "htmx:configRequest" in js and "X-CSRF-Token" in js and "csrf-token" in js
    assert f"panel.js?v={ASSET_VERSION}" in html and ASSET_VERSION != "21"


# --- cookie ------------------------------------------------------------------------------------------


def session_cookie(response):
    return next(v for v in response.headers.get_list("set-cookie") if v.startswith("session="))


def test_the_session_cookie_is_httponly_samesite_lax_for_8_hours_and_not_secure_on_http():
    panel = Engine().panel(login=False)
    cookie = session_cookie(panel.get("/auth/google")).lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=28800" in cookie
    assert "secure" not in cookie


def test_the_session_cookie_is_secure_when_public_url_is_https():
    settings = test_settings(public_url="https://panel.example.com")
    panel = Engine().panel(login=False, settings=settings)
    assert "secure" in session_cookie(panel.get("/auth/google")).lower()


# --- settings ------------------------------------------------------------------------------------------


def test_load_dotenv_fills_missing_variables_without_overriding_real_ones(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nFOO_A=one\nFOO_B="two"\nFOO_C=\'three\'\nFOO_KEEP=file\nnot a pair\n')
    for name in ("FOO_A", "FOO_B", "FOO_C"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FOO_KEEP", "real")
    load_dotenv(env)
    assert (os.environ["FOO_A"], os.environ["FOO_B"], os.environ["FOO_C"], os.environ["FOO_KEEP"]) == (
        "one", "two", "three", "real")
    for name in ("FOO_A", "FOO_B", "FOO_C"):
        monkeypatch.delenv(name)
    load_dotenv(tmp_path / "missing.env")  # no file: nothing happens


def test_settings_from_env(monkeypatch):
    for name, value in dict(GOOGLE_CLIENT_ID="i", GOOGLE_CLIENT_SECRET="s", SESSION_SECRET="x" * 32,
                            PUBLIC_URL="https://p.example.com/", VERIDIAN_URL="http://e:1/",
                            VERIDIAN_PANEL_TOKEN="pt").items():
        monkeypatch.setenv(name, value)
    s = Settings.from_env()
    assert (s.google_client_id, s.google_client_secret, s.public_url, s.engine_url, s.panel_token) == (
        "i", "s", "https://p.example.com", "http://e:1", "pt")
    assert s.configured
    monkeypatch.delenv("SESSION_SECRET")
    assert not Settings.from_env().configured
