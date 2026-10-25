"""Panel login with GitHub next to Google: scope user:email, primary verified email, one account per email."""

import json
from urllib.parse import parse_qs, urlparse

import httpx

from fake_engine import create_app as create_engine
from tests.panel_support import (EMAIL, PANEL_TOKEN, PERSON_ID, github_transport, google_transport, make_panel,
                                 session_of, sign_in, sign_in_github, stub_client, test_settings)

GITHUB = dict(github_client_id="ghid", github_client_secret="ghsecret")


def canned(request):
    return httpx.Response(404, json={"error": "not_found"})


def github_panel(logins=None, **kw):
    kw.setdefault("settings", test_settings(**GITHUB))
    return make_panel(stub_client(canned, logins=logins), login=False, **kw)


def error_of(response):
    return parse_qs(urlparse(response.headers["location"]).query).get("error", [None])[0]


def real_engine():
    return create_engine(tokens={"tok-a"}, panel_tokens={PANEL_TOKEN}, learn_delay=0, seed=[])


def engine_client(engine):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=engine), base_url="http://engine")


# --- hidden unless configured: never an open mode ----------------------------------------------


def test_github_is_hidden_on_the_login_page_when_unconfigured():
    html = make_panel(stub_client(canned), login=False).get("/login").text
    assert "Continuar con Google" in html and "GitHub" not in html and "/auth/github" not in html


def test_github_routes_do_not_exist_when_unconfigured():
    panel = make_panel(stub_client(canned), login=False)
    assert panel.get("/auth/github").status_code == 404
    assert panel.get("/auth/github/callback", params={"code": "c", "state": "s"}).status_code == 404


def test_github_is_offered_next_to_google_when_configured():
    html = github_panel().get("/login").text
    assert "Continuar con Google" in html and "Continuar con GitHub" in html and 'href="/auth/github"' in html


def test_a_half_configured_github_stays_hidden():
    panel = github_panel(settings=test_settings(github_client_id="ghid"))
    assert "/auth/github" not in panel.get("/login").text and panel.get("/auth/github").status_code == 404


# --- /auth/github -------------------------------------------------------------------------------


def test_auth_github_redirects_with_user_email_scope_and_a_single_use_state():
    panel = github_panel()
    r = panel.get("/auth/github")
    url = urlparse(r.headers["location"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert r.status_code == 303 and url.netloc == "github.com" and url.path == "/login/oauth/authorize"
    assert query["client_id"] == "ghid" and query["scope"] == "user:email"
    assert query["redirect_uri"] == "http://testserver/auth/github/callback"
    assert session_of(panel)["oauth_state"] == query["state"] and len(query["state"]) >= 32


def test_the_github_redirect_uri_comes_from_public_url_when_set():
    panel = github_panel(settings=test_settings(public_url="https://panel.example.com", **GITHUB))
    query = parse_qs(urlparse(panel.get("/auth/github").headers["location"]).query)
    assert query["redirect_uri"] == ["https://panel.example.com/auth/github/callback"]


# --- /auth/github/callback ----------------------------------------------------------------------


def test_callback_signs_in_with_the_primary_verified_email_and_a_namespaced_id():
    logins, gh = [], github_transport(gh_id=77, email="Ana@Example.com", name="Ana G")
    panel = github_panel(logins=logins, github_transport=gh)
    r = sign_in_github(panel)
    assert r.status_code == 303 and r.headers["location"] == "/"
    (login,) = logins
    assert json.loads(login.content) == {"google_sub": "github:77", "email": "ana@example.com", "name": "Ana G"}
    session = session_of(panel)
    assert session["person"] == PERSON_ID and session["email"] == EMAIL and "oauth_state" not in session
    token_call = next(q for q in gh.seen if q.url.host == "github.com")
    assert parse_qs(token_call.content.decode())["client_secret"] == ["ghsecret"]
    emails_call = next(q for q in gh.seen if q.url.path == "/user/emails")
    assert emails_call.headers["authorization"] == "Bearer gh-c"


def test_callback_takes_the_primary_verified_email_not_another_verified_one():
    listed = [{"email": "other@example.com", "primary": False, "verified": True},
              {"email": "main@example.com", "primary": True, "verified": True}]
    logins = []
    sign_in_github(github_panel(logins=logins, github_transport=github_transport(emails=listed)))
    assert json.loads(logins[0].content)["email"] == "main@example.com"


def test_github_without_a_primary_verified_email_is_refused_and_never_reaches_the_engine():
    for listed in ([{"email": "a@example.com", "primary": True, "verified": False}],
                   [{"email": "a@example.com", "primary": False, "verified": True}],
                   []):
        logins = []
        panel = github_panel(logins=logins, github_transport=github_transport(emails=listed))
        r = sign_in_github(panel)
        assert r.status_code == 303 and error_of(r) == "github_email"
        assert logins == [] and "person" not in session_of(panel)


def test_the_refusal_names_github_and_what_to_do():
    html = github_panel().get("/login", params={"error": "github_email"}).text
    assert "GitHub" in html and "correo principal verificado" in html and 'role="alert"' in html


def test_callback_state_is_single_use_and_must_match():
    panel = github_panel(github_transport=github_transport())
    panel.get("/auth/github")
    r = panel.get("/auth/github/callback", params={"code": "c", "state": "forged"})
    assert error_of(r) == "state"
    assert error_of(panel.get("/auth/github/callback", params={"code": "c", "state": "forged"})) == "state"


def test_a_google_state_cannot_be_replayed_in_the_github_callback_without_the_session():
    panel = github_panel(github_transport=github_transport())
    r = panel.get("/auth/github/callback", params={"code": "c", "state": "anything"})
    assert error_of(r) == "state"


def test_callback_github_denied():
    panel = github_panel()
    panel.get("/auth/github")
    r = panel.get("/auth/github/callback", params={"error": "access_denied", "state": session_of(panel)["oauth_state"]})
    assert error_of(r) == "github_denied"


def test_github_failures_are_login_errors_not_a_500():
    for gh in (github_transport(token_error="bad_verification_code"), github_transport(user_status=500),
               github_transport(emails_status=500)):
        panel = github_panel(github_transport=gh)
        r = sign_in_github(panel)
        assert r.status_code == 303 and error_of(r) == "github_failed" and "person" not in session_of(panel)


# --- one person, two providers --------------------------------------------------------------------


def test_the_same_verified_email_through_google_and_github_is_one_account():
    engine = real_engine()
    settings = test_settings(**GITHUB)
    via_google = make_panel(engine_client(engine), settings=settings, login=False,
                            transport=google_transport(sub="g-1", email=EMAIL))
    via_github = make_panel(engine_client(engine), settings=settings, login=False,
                            github_transport=github_transport(gh_id=9, email=EMAIL.upper()))
    assert sign_in(via_google).status_code == 303
    assert sign_in_github(via_github).status_code == 303
    assert len(engine.state.people) == 1
    assert session_of(via_google)["person"] == session_of(via_github)["person"]
    # and GitHub again, later, is still that person
    again = make_panel(engine_client(engine), settings=settings, login=False,
                       github_transport=github_transport(gh_id=9, email=EMAIL))
    sign_in_github(again)
    assert len(engine.state.people) == 1


def test_different_verified_emails_are_different_accounts():
    engine = real_engine()
    settings = test_settings(**GITHUB)
    a = make_panel(engine_client(engine), settings=settings, login=False,
                   github_transport=github_transport(gh_id=1, email="a@example.com"))
    b = make_panel(engine_client(engine), settings=settings, login=False,
                   github_transport=github_transport(gh_id=2, email="b@example.com"))
    sign_in_github(a), sign_in_github(b)
    assert session_of(a)["person"] != session_of(b)["person"]


def test_github_settings_come_from_the_environment_and_need_both_values(monkeypatch):
    from panel.config import Settings
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    assert not Settings.from_env().github_enabled
    monkeypatch.setenv("GITHUB_CLIENT_ID", "i")
    assert not Settings.from_env().github_enabled
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s")
    assert Settings.from_env().github_enabled
