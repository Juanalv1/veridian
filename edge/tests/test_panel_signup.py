"""Self-signup: a person with no organization names their company once, gets an org and a first project,
lands on the API keys page and sees the snippet with their new key; people with an org never see the step again."""

import re

import httpx
from fastapi.testclient import TestClient

from fake_engine import create_app as create_engine
from tests.panel_support import (EMAIL, PANEL_TOKEN, github_transport, google_transport, login_answer, make_panel,
                                 session_of, sign_in, sign_in_github, test_settings)

GITHUB = dict(github_client_id="ghid", github_client_secret="ghsecret")
OTHER = "other@example.com"


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def new_engine(owners=()):
    return create_engine(tokens={"tok-a"}, panel_tokens={PANEL_TOKEN}, bootstrap_owners=set(owners), learn_delay=0,
                         seed=[])


def link(engine):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=engine), base_url="http://engine")


def panel_of(engine, email=EMAIL, sub="g-1", **kw):
    return make_panel(link(engine), transport=google_transport(sub=sub, email=email), **kw)


def no_org_engine(handler):
    """A canned engine for a person with no org: the login is answered here, everything else by `handler`."""
    def wrapper(request):
        if request.url.path == "/v1/people/login":
            return httpx.Response(200, json=login_answer(projects=()))
        return handler(request)
    return httpx.AsyncClient(transport=httpx.MockTransport(wrapper))


def orgs_named(engine, name):
    return [o for o in engine.state.orgs.values() if o.name == name]


# --- the step -------------------------------------------------------------------------------------


def test_a_new_person_lands_on_a_step_that_asks_only_the_company_name():
    engine = new_engine()
    panel = panel_of(engine, login=False)
    assert sign_in(panel).headers["location"] == "/sin-proyectos"
    r = panel.get("/sin-proyectos")
    assert r.status_code == 200
    fields = re.findall(r'<input[^>]*name="([^"]+)"', r.text)
    assert set(fields) == {"csrf", "name"} and fields.count("name") == 1
    t = text(r.text)
    assert "empresa" in t and EMAIL in t and 'action="/logout"' in r.text
    assert "Pide al administrador" not in t


def test_the_company_name_creates_the_org_with_the_person_as_owner_and_its_first_project():
    engine = new_engine()
    panel = panel_of(engine)
    r = panel.post("/sin-proyectos", data={"name": "  Acme SA  "})
    assert r.status_code == 303 and r.headers["location"] == "/claves"
    (org,) = orgs_named(engine, "Acme SA")
    assert list(org.members.values()) == ["owner"]
    (project,) = [p for p in engine.state.projects.values() if p.org_id == org.id]
    assert project.name == "Acme SA"
    session = session_of(panel)
    assert session["org"] == org.id and session["project"] == project.id


def test_a_new_person_signs_up_with_google_and_creates_org_and_key_without_an_invitation():
    engine = new_engine()
    panel = panel_of(engine, login=False)
    sign_in(panel)
    panel.post("/sin-proyectos", data={"name": "Acme"})
    page = panel.get("/claves")
    assert page.status_code == 200 and "Crear una clave" in page.text
    made = panel.post("/claves/nueva", data={"name": "Producción"})
    assert made.status_code == 200
    key = re.search(r'id="snippet-env">VERIDIAN_API_KEY=(vrd_[^<]+)<', made.text).group(1)
    assert "veridian-sdk" in made.text
    events = TestClient(engine).post("/v1/events", json={"events": []}, headers={"Authorization": f"Bearer {key}"})
    assert events.status_code == 202  # the key shown is a working key of the new project


def test_a_new_person_signs_up_with_github_and_creates_org_and_key_without_an_invitation():
    engine = new_engine()
    panel = make_panel(link(engine), settings=test_settings(**GITHUB), login=False,
                       github_transport=github_transport(gh_id=5, email=OTHER))
    assert sign_in_github(panel).headers["location"] == "/sin-proyectos"
    assert panel.post("/sin-proyectos", data={"name": "Globex"}).headers["location"] == "/claves"
    made = panel.post("/claves/nueva", data={"name": "Producción"})
    assert made.status_code == 200 and "VERIDIAN_API_KEY=vrd_" in made.text


def test_the_landing_greets_the_new_org_once():
    panel = panel_of(new_engine())
    panel.post("/sin-proyectos", data={"name": "Acme"})
    first = text(panel.get("/claves").text)
    assert "Acme" in first and "primera clave" in first
    assert "primera clave" not in text(panel.get("/claves").text)  # a flash shows once


# --- never again ----------------------------------------------------------------------------------


def test_a_person_with_an_org_never_sees_the_step_again():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/sin-proyectos", data={"name": "Acme"})
    again = panel_of(engine, login=False)  # a later session of the same person
    assert sign_in(again).headers["location"] == "/"
    r = again.get("/sin-proyectos")
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_posting_the_step_again_creates_nothing_more():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/sin-proyectos", data={"name": "Acme"})
    before = len(engine.state.orgs)
    r = panel.post("/sin-proyectos", data={"name": "Acme 2"})
    assert r.status_code == 303 and len(engine.state.orgs) == before and not orgs_named(engine, "Acme 2")


def test_an_invited_person_skips_the_step_and_a_stale_session_cannot_make_a_second_org():
    engine = new_engine()
    panel = panel_of(engine, email=OTHER, sub="g-2")  # no org yet: the step would show
    (org,) = [o for o in engine.state.orgs.values() if o.name == "Demo"]
    org.invite(OTHER, "viewer", "2999-01-01T00:00:00Z")
    engine_login = TestClient(engine).post(
        "/v1/people/login", json={"google_sub": "g-2", "email": OTHER}, headers={"Authorization": f"Bearer {PANEL_TOKEN}"})
    assert engine_login.status_code == 200
    before = len(engine.state.orgs)
    r = panel.post("/sin-proyectos", data={"name": "Sneaky"})
    assert r.status_code == 303 and len(engine.state.orgs) == before


def test_a_person_who_signs_in_with_an_org_goes_straight_to_the_panel():
    engine = new_engine(owners=(EMAIL,))
    panel = panel_of(engine, login=False)
    assert sign_in(panel).headers["location"] == "/"


# --- one person across providers ------------------------------------------------------------------


def test_the_company_created_through_one_provider_is_found_through_the_other():
    engine = new_engine()
    settings = test_settings(**GITHUB)
    via_google = make_panel(link(engine), settings=settings, transport=google_transport(sub="g-1", email=EMAIL))
    via_google.post("/sin-proyectos", data={"name": "Acme"})
    via_github = make_panel(link(engine), settings=settings, login=False,
                            github_transport=github_transport(gh_id=3, email=EMAIL))
    assert sign_in_github(via_github).headers["location"] == "/"
    assert via_github.get("/sin-proyectos").status_code == 303
    assert len(engine.state.people) == 1


# --- failures -------------------------------------------------------------------------------------


def test_an_invalid_company_name_is_an_inline_error_and_creates_nothing():
    engine = new_engine()
    panel = panel_of(engine)
    before = len(engine.state.orgs)
    for name in ("", "   ", "x" * 81):
        r = panel.post("/sin-proyectos", data={"name": name})
        assert r.status_code == 400 and 'role="alert"' in r.text and "empresa" in text(r.text).lower()
    assert len(engine.state.orgs) == before


def test_the_step_needs_the_csrf_token():
    engine = new_engine()
    panel = panel_of(engine)
    before = len(engine.state.orgs)
    r = panel.post("/sin-proyectos", data={"name": "Acme"}, csrf=False, headers={"Origin": "http://testserver"})
    assert r.status_code == 403 and len(engine.state.orgs) == before


def test_the_step_needs_a_session():
    panel = panel_of(new_engine(), login=False)
    r = panel.get("/sin-proyectos")
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_when_the_engine_refuses_the_org_the_pilot_note_shows_and_the_name_is_kept():
    def handler(request):
        if request.url.path == "/v1/me/orgs":
            return httpx.Response(200, json={"orgs": []})
        return httpx.Response(403, json={"error": "forbidden"})

    panel = make_panel(no_org_engine(handler))
    r = panel.post("/sin-proyectos", data={"name": "Acme"})
    assert r.status_code == 403 and "limitado durante el piloto" in text(r.text) and 'value="Acme"' in r.text


def test_when_the_engine_is_down_the_step_says_so_and_creates_nothing():
    def handler(request):
        raise httpx.ConnectError("down")

    panel = make_panel(no_org_engine(handler))
    r = panel.post("/sin-proyectos", data={"name": "Acme"})
    assert r.status_code == 502 and "No se pudo conectar" in text(r.text) and 'value="Acme"' in r.text


def test_if_the_first_project_fails_the_org_is_kept_and_the_person_finishes_in_the_project_form():
    posted = []

    def handler(request):
        posted.append((request.method, request.url.path))
        if request.url.path == "/v1/me/orgs":
            return httpx.Response(200, json={"orgs": []})
        if request.url.path == "/v1/orgs":
            return httpx.Response(201, json={"id": "org_new", "name": "Acme", "role": "owner", "projects": []})
        return httpx.Response(500, json={"error": "boom"})

    panel = make_panel(no_org_engine(handler))
    r = panel.post("/sin-proyectos", data={"name": "Acme"})
    assert r.status_code == 303 and r.headers["location"] == "/proyectos/nuevo?org=org_new"
    assert session_of(panel)["org"] == "org_new" and "project" not in session_of(panel)
    assert ("POST", "/v1/orgs/org_new/projects") in posted
