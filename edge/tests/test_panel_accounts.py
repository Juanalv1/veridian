"""Panel accounts: organization and project selector, creation, deletion, sidebar switcher (T4).

Most tests run the panel over the real fake engine (contract 10), so what the panel sends is what the
engine accepts. A few use canned engines for failures the fake engine does not produce.
"""

import re

import httpx
import pytest
from fastapi.testclient import TestClient

from fake_engine import create_app as create_engine
from tests.panel_support import (EMAIL, PANEL_TOKEN, google_transport, login_answer, make_panel, session_of, sign_in,
                                 stub_client)

TOKEN_A, TOKEN_B = "tok-a", "tok-b"  # two demo organizations, "Demo 1" and "Demo 2", one project each
FUTURE = "2999-01-01T00:00:00Z"
OTHER = "other@example.com"


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def new_engine(owners=(EMAIL,), seed=None):
    return create_engine(tokens={TOKEN_A, TOKEN_B}, panel_tokens={PANEL_TOKEN}, bootstrap_owners=set(owners),
                         learn_delay=0, seed=seed if seed is not None else [])


def link(engine):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=engine), base_url="http://engine")


def project_a(engine):
    return engine.state.project_id_for(TOKEN_A)


def project_b(engine):
    return engine.state.project_id_for(TOKEN_B)


def org_a(engine):
    return engine.state.org_id_for(TOKEN_A)


def org_b(engine):
    return engine.state.org_id_for(TOKEN_B)


def panel_of(engine, email=EMAIL, sub="g-1", **kw):
    return make_panel(link(engine), transport=google_transport(sub=sub, email=email), **kw)


def invited(engine, org_id, email, role):
    engine.state.orgs[org_id].invite(email, role, FUTURE)


def member_panel(engine, org_id, role, email=OTHER, sub="g-2", **kw):
    invited(engine, org_id, email, role)
    return panel_of(engine, email=email, sub=sub, **kw)


def open_project(panel, project_id):
    return panel.post("/proyectos/abrir", data={"project_id": project_id})


# --- login and "sin organización" pages (mockups Main, SinProyectos) -------------------------------


def test_login_page_follows_the_mockup_copy():
    html = panel_of(new_engine(), login=False).get("/login").text
    assert "Entra a tu panel" in html and "Continuar con Google" in html and 'href="/auth/google"' in html
    assert "primera vez" in html and "solo el nombre de tu empresa" in html and "GitHub" not in html
    assert 'role="alert"' not in html


@pytest.mark.parametrize("code,message", [
    ("denied", "Cancelaste el inicio de sesión con Google."),
    ("state", "El inicio de sesión expiró o no es válido. Inténtalo de nuevo."),
    ("email", "Tu cuenta de Google no tiene el correo verificado."),
    ("failed", "No pudimos completar el inicio de sesión con Google. Inténtalo de nuevo.")])
def test_login_errors_use_the_mockup_messages_with_a_lead_in(code, message):
    html = panel_of(new_engine(), login=False).get("/login", params={"error": code}).text
    assert message in html and "No pudiste entrar." in html and 'role="alert"' in html


def test_no_organization_page_asks_only_for_the_company_name():
    engine = new_engine(owners=())
    panel = panel_of(engine, email="nobody@example.com", login=False)
    assert sign_in(panel).headers["location"] == "/sin-proyectos"
    html = panel.get("/sin-proyectos").text
    assert "¿Cómo se llama tu empresa?" in html and "Nombre de la empresa" in html
    assert "nobody@example.com" in html and "Cerrar sesión" in html and 'action="/logout"' in html
    assert 'action="/sin-proyectos"' in html and 'name="csrf"' in html


def test_the_no_organization_page_lets_a_person_without_orgs_reach_the_selector_redirect():
    engine = new_engine(owners=())
    panel = panel_of(engine, email="nobody@example.com")
    r = panel.get("/proyectos")
    assert r.status_code == 303 and r.headers["location"] == "/sin-proyectos"


# --- session ---------------------------------------------------------------------------------------


def test_login_keeps_the_current_org_and_project_in_the_session():
    engine = new_engine()
    panel = panel_of(engine)
    s = session_of(panel)
    assert s["project"] == project_a(engine) and s["org"] == org_a(engine)
    assert "role" not in s


def test_a_person_in_an_org_without_projects_lands_on_the_selector():
    engine = new_engine()
    engine.state.projects[project_a(engine)].deleted_at = "2026-01-01T00:00:00Z"
    engine.state.projects[project_b(engine)].deleted_at = "2026-01-01T00:00:00Z"
    panel = make_panel(link(engine), login=False, transport=google_transport(email=EMAIL))
    r = sign_in(panel)
    assert r.headers["location"] == "/proyectos"
    s = session_of(panel)
    assert "project" not in s and s["org"] == org_a(engine)
    for path in ("/", "/actividad", "/usuarios", "/rutas"):
        r = panel.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/proyectos", path
    assert panel.get("/proyectos").status_code == 200


# --- /proyectos ------------------------------------------------------------------------------------


def test_the_selector_needs_a_session():
    panel = panel_of(new_engine(), login=False)
    for method, path in (("get", "/proyectos"), ("get", "/proyectos/nuevo"), ("get", "/organizaciones/nueva")):
        r = getattr(panel, method)(path)
        assert r.status_code == 303 and r.headers["location"] == "/login", path
    for path in ("/proyectos/abrir", "/proyectos/nuevo", "/organizaciones/nueva", "/organizaciones/org_x/eliminar"):
        r = panel.post(path, csrf=False)
        assert r.status_code in (303, 401), path


def test_the_selector_lists_the_orgs_with_role_and_the_projects_of_the_current_one():
    engine = new_engine()
    html = panel_of(engine).get("/proyectos").text
    assert "Tus organizaciones" in html.title() or "TUS ORGANIZACIONES" in html
    t = text(html)
    assert "Demo 1" in t and "Demo 2" in t and "Owner · 1 proyecto" in t
    assert "Proyectos de Demo 1" in t and "Tu rol en Demo 1 es Owner y vale para todos sus proyectos." in t
    assert project_a(engine) in html
    assert "Abierto ahora" in t or "ABIERTO AHORA" in t
    assert "Bloquea" in t  # the seeded demo project enforces
    assert re.search(r'<a[^>]*aria-current="page"[^>]*>.*?Demo 1', html, re.S)


def test_the_org_query_shows_another_orgs_projects_without_switching():
    engine = new_engine()
    panel = panel_of(engine)
    html = panel.get("/proyectos", params={"org": org_b(engine)}).text
    assert "Proyectos de Demo 2" in text(html) and project_b(engine) in html
    assert project_a(engine) not in html.split("Proyectos de Demo 2")[1]
    assert session_of(panel)["project"] == project_a(engine)


def test_an_unknown_org_query_falls_back_to_the_current_org():
    engine = new_engine()
    html = panel_of(engine).get("/proyectos", params={"org": "org_nope"}).text
    assert "Proyectos de Demo 1" in text(html)


def test_the_selector_says_the_engine_is_unreachable():
    def down(request):
        if request.url.path == "/v1/people/login":
            return httpx.Response(200, json=login_answer())
        raise httpx.ConnectError("down")
    panel = make_panel(httpx.AsyncClient(transport=httpx.MockTransport(down)))
    r = panel.get("/proyectos")
    assert r.status_code == 502 and "No se pudo conectar con el motor" in r.text


def test_owner_and_admin_see_create_project_and_create_org_links():
    engine = new_engine()
    html = panel_of(engine).get("/proyectos").text
    assert f'href="/proyectos/nuevo?org={org_a(engine)}"' in html and "Crear proyecto" in html
    assert 'href="/organizaciones/nueva"' in html and "Crear organización" in html
    admin = member_panel(engine, org_b(engine), "admin")
    html = admin.get("/proyectos", params={"org": org_b(engine)}).text
    assert f'href="/proyectos/nuevo?org={org_b(engine)}"' in html


def test_a_viewer_has_no_create_project_button_and_gets_the_explanation():
    engine = new_engine(owners=())
    viewer = member_panel(engine, org_a(engine), "viewer")
    html = viewer.get("/proyectos").text
    assert "Crear proyecto" not in html and "/proyectos/nuevo" not in html
    assert "Como viewer ves todos los proyectos de Demo 1; crearlos o configurarlos lo hace un admin u owner." in text(html)
    assert "Tu rol en Demo 1 es Viewer" in text(html)


def test_the_selector_page_is_a_page_of_its_own_with_email_and_logout():
    html = panel_of(new_engine()).get("/proyectos").text
    assert EMAIL in html and 'action="/logout"' in html and "Cerrar sesión" in html
    assert 'id="side"' not in html  # the mockup is a standalone page, not the sidebar shell


# --- switching project -------------------------------------------------------------------------------


def test_opening_a_project_of_another_org_switches_org_and_project():
    engine = new_engine()
    panel = panel_of(engine)
    r = open_project(panel, project_b(engine))
    assert r.status_code == 303 and r.headers["location"] == "/"
    s = session_of(panel)
    assert s["project"] == project_b(engine) and s["org"] == org_b(engine)


def test_each_project_row_is_a_csrf_protected_form_button():
    engine = new_engine()
    html = panel_of(engine).get("/proyectos", params={"org": org_b(engine)}).text
    form = re.search(r'<form[^>]*action="/proyectos/abrir"[^>]*>(.*?)</form>', html, re.S).group(1)
    assert 'name="csrf"' in form and f'name="project_id" value="{project_b(engine)}"' in form
    assert "<button" in form


def test_a_project_of_an_org_the_person_does_not_belong_to_is_refused():
    engine = new_engine(owners=())
    viewer = member_panel(engine, org_a(engine), "viewer")
    before = session_of(viewer)
    r = open_project(viewer, project_b(engine))
    assert r.status_code == 303 and r.headers["location"] == "/proyectos"
    after = session_of(viewer)
    assert after["project"] == before["project"] == project_a(engine)
    assert "No tienes acceso a ese proyecto." in viewer.get("/proyectos").text


@pytest.mark.parametrize("value", ["p_nope", "", "../x"])
def test_an_unknown_project_id_is_refused(value):
    engine = new_engine()
    panel = panel_of(engine)
    r = open_project(panel, value)
    assert r.status_code in (303, 422) and session_of(panel)["project"] == project_a(engine)


def test_opening_a_project_needs_the_csrf_token():
    engine = new_engine()
    panel = panel_of(engine)
    r = panel.post("/proyectos/abrir", data={"project_id": project_b(engine)}, csrf=False,
                   headers={"Origin": "http://testserver"})
    assert r.status_code == 403 and session_of(panel)["project"] == project_a(engine)


def test_a_project_that_was_deleted_cannot_be_opened():
    engine = new_engine()
    panel = panel_of(engine)
    engine.state.projects[project_b(engine)].deleted_at = "2026-01-01T00:00:00Z"
    open_project(panel, project_b(engine))
    assert session_of(panel)["project"] == project_a(engine)


def _block_in_project_a(engine):
    """A critical route used from a new network by a user with history, in project A only."""
    ev = {"id": "e_1", "ts": "2026-10-26T10:14:03Z", "user_id": "u_in_a", "session_id": "s_x", "ip": "203.0.113.9",
          "user_agent": "t", "device_id": "d_1", "method": "POST", "route": "/payouts/bank-account",
          "status": 200, "bytes": 1}
    r = TestClient(engine).post("/v1/events", json={"events": [ev]}, headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert r.status_code in (200, 202), r.text


def test_switching_project_never_shows_the_previous_projects_data():
    engine = new_engine(seed=[{"user_id": "u_in_a", "ips": ["190.202.14.7"], "routes": ["GET /invoices"]}])
    _block_in_project_a(engine)
    panel = panel_of(engine)
    first = panel.get("/actividad").text
    assert "u_in_a" in first
    assert open_project(panel, project_b(engine)).status_code == 303
    second = panel.get("/actividad").text
    assert "u_in_a" not in second
    assert open_project(panel, project_a(engine)).status_code == 303
    assert "u_in_a" in panel.get("/actividad").text


def test_the_engine_calls_after_switching_carry_the_new_project():
    engine = new_engine()
    seen = []

    class Spy(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.ASGITransport(app=engine)

        async def handle_async_request(self, request):
            seen.append((request.url.path, request.headers.get("x-veridian-project")))
            return await self.inner.handle_async_request(request)

    panel = make_panel(httpx.AsyncClient(transport=Spy(), base_url="http://engine"),
                       transport=google_transport(email=EMAIL))
    open_project(panel, project_b(engine))
    seen.clear()
    panel.get("/actividad")
    data = [(p, h) for p, h in seen if p in ("/v1/decisions", "/v1/activity")]
    assert data and {h for _, h in data} == {project_b(engine)}


# --- create organization -----------------------------------------------------------------------------


def test_the_create_organization_form_follows_the_mockup():
    html = panel_of(new_engine()).get("/organizaciones/nueva").text
    t = text(html)
    assert "Crear organización" in t and "Quedas como owner." in t and "Cómo funcionan los roles" in t
    assert re.search(r'<label[^>]*for="org-name"[^>]*>\s*Nombre', html)
    assert re.search(r'<input[^>]*id="org-name"[^>]*name="name"', html) or re.search(r'<input[^>]*name="name"[^>]*id="org-name"', html)
    assert 'name="csrf"' in html and 'href="/proyectos"' in html and "Cancelar" in t
    assert 'id="org-slug"' not in html  # the contract has no identifier field


def test_creating_an_organization_makes_the_person_owner_and_goes_to_create_its_first_project():
    engine = new_engine()
    panel = panel_of(engine)
    r = panel.post("/organizaciones/nueva", data={"name": "  Acme  "})
    assert r.status_code == 303
    created = [o for o in engine.state.orgs.values() if o.name == "Acme"]
    assert len(created) == 1
    (org,) = created
    assert r.headers["location"] == f"/proyectos/nuevo?org={org.id}"
    assert list(org.members.values()) == ["owner"]
    assert session_of(panel)["org"] == org.id
    assert "Acme" in panel.get("/proyectos", params={"org": org.id}).text  # the cached org list was dropped


def test_when_the_engine_says_forbidden_the_pilot_note_is_shown_and_nothing_is_created():
    seen = []

    def handler(request):
        seen.append(request.method)
        return httpx.Response(403, json={"error": "forbidden"})

    panel = make_panel(stub_client(handler))
    r = panel.post("/organizaciones/nueva", data={"name": "Acme"})
    assert r.status_code == 403 and seen == ["POST"]
    assert "limitado durante el piloto" in text(r.text) and 'role="alert"' in r.text
    assert "[correo de soporte]" in r.text
    assert 'value="Acme"' in r.text


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
def test_an_invalid_organization_name_is_an_inline_error_and_never_reaches_the_engine(name):
    engine = new_engine()
    panel = panel_of(engine)
    before = len(engine.state.orgs)
    r = panel.post("/organizaciones/nueva", data={"name": name})
    assert r.status_code == 400 and 'role="alert"' in r.text and "nombre" in r.text.lower()
    assert len(engine.state.orgs) == before


def test_creating_an_organization_needs_the_csrf_token():
    engine = new_engine()
    panel = panel_of(engine)
    r = panel.post("/organizaciones/nueva", data={"name": "Acme"}, csrf=False, headers={"Origin": "http://testserver"})
    assert r.status_code == 403 and not [o for o in engine.state.orgs.values() if o.name == "Acme"]


def test_the_create_organization_form_survives_an_engine_that_is_down():
    def handler(request):
        if request.url.path == "/v1/people/login":
            return httpx.Response(200, json=login_answer())
        raise httpx.ConnectError("down")
    panel = make_panel(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = panel.post("/organizaciones/nueva", data={"name": "Acme"})
    assert r.status_code == 502 and "No se pudo conectar con el motor" in r.text and 'value="Acme"' in r.text


# --- create project ----------------------------------------------------------------------------------


def test_the_create_project_form_follows_the_mockup():
    engine = new_engine()
    html = panel_of(engine).get("/proyectos/nuevo", params={"org": org_a(engine)}).text
    t = text(html)
    assert "Crear proyecto en Demo 1" in t and "← Proyectos de Demo 1" in t
    assert re.search(r'<input[^>]*name="name"', html) and 'for="name"' in html
    assert re.search(r'<input[^>]*type="radio"[^>]*name="enforcement"[^>]*value="observe"[^>]*checked', html)
    assert re.search(r'<input[^>]*type="radio"[^>]*name="enforcement"[^>]*value="enforce"(?![^>]*checked)', html)
    assert "Solo observa (recomendado)" in t and "Bloquea desde el inicio" in t
    assert re.search(r'<input[^>]*type="checkbox"[^>]*name="ai_enabled"[^>]*checked', html)
    assert "Usar IA para explicar y decidir" in t and 'name="csrf"' in html
    assert f'name="org" value="{org_a(engine)}"' in html
    assert 'id="slug"' not in html


def test_a_viewer_is_sent_back_from_the_create_project_form():
    engine = new_engine(owners=())
    viewer = member_panel(engine, org_a(engine), "viewer")
    r = viewer.get("/proyectos/nuevo", params={"org": org_a(engine)})
    assert r.status_code == 303 and r.headers["location"] == "/proyectos"


def test_the_create_project_form_of_an_org_the_person_is_not_in_is_refused():
    engine = new_engine(owners=())
    viewer = member_panel(engine, org_a(engine), "admin")
    r = viewer.get("/proyectos/nuevo", params={"org": org_b(engine)})
    assert r.status_code == 303 and r.headers["location"] == "/proyectos"


def created_projects(engine, org_id, exclude=()):
    return [p for p in engine.state.projects.values() if p.org_id == org_id and p.id not in exclude]


def test_creating_a_project_defaults_to_observe_with_ai_and_switches_to_it():
    engine = new_engine()
    panel = panel_of(engine)
    old = {project_a(engine), project_b(engine)}
    r = panel.post("/proyectos/nuevo", data={"org": org_a(engine), "name": " Portal clientes ",
                                              "enforcement": "observe", "ai_enabled": "on"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    (p,) = created_projects(engine, org_a(engine), exclude=old)
    assert (p.name, p.enforcement, p.ai_enabled) == ("Portal clientes", "observe", True)
    s = session_of(panel)
    assert s["project"] == p.id and s["org"] == org_a(engine)


def test_enforce_and_no_ai_are_mapped_to_the_contract_fields():
    engine = new_engine()
    panel = panel_of(engine)
    old = {project_a(engine), project_b(engine)}
    panel.post("/proyectos/nuevo", data={"org": org_a(engine), "name": "Backoffice", "enforcement": "enforce"})
    (p,) = created_projects(engine, org_a(engine), exclude=old)
    assert (p.enforcement, p.ai_enabled) == ("enforce", False)


def test_the_new_project_is_the_one_the_next_page_reads():
    engine = new_engine()
    seen = []

    class Spy(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.ASGITransport(app=engine)

        async def handle_async_request(self, request):
            seen.append((request.url.path, request.headers.get("x-veridian-project")))
            return await self.inner.handle_async_request(request)

    panel = make_panel(httpx.AsyncClient(transport=Spy(), base_url="http://engine"),
                       transport=google_transport(email=EMAIL))
    panel.post("/proyectos/nuevo", data={"org": org_a(engine), "name": "Nuevo", "enforcement": "observe"})
    pid = session_of(panel)["project"]
    seen.clear()
    assert panel.get("/actividad").status_code == 200
    assert {h for p, h in seen if p == "/v1/decisions"} == {pid}


@pytest.mark.parametrize("data", [
    {"name": ""}, {"name": "   "}, {"name": "x" * 81}, {"name": "Ok", "enforcement": "yolo"}])
def test_invalid_project_input_is_an_inline_error_and_creates_nothing(data):
    engine = new_engine()
    panel = panel_of(engine)
    before = len(engine.state.projects)
    r = panel.post("/proyectos/nuevo", data={"org": org_a(engine), "enforcement": "observe", **data})
    assert r.status_code == 400 and 'role="alert"' in r.text
    assert len(engine.state.projects) == before
    assert session_of(panel)["project"] == project_a(engine)


def test_a_viewer_forging_the_create_project_post_creates_nothing():
    engine = new_engine(owners=())
    viewer = member_panel(engine, org_a(engine), "viewer")
    before = len(engine.state.projects)
    r = viewer.post("/proyectos/nuevo", data={"org": org_a(engine), "name": "Sneaky", "enforcement": "observe"})
    assert r.status_code in (303, 403) and len(engine.state.projects) == before
    assert session_of(viewer)["project"] == project_a(engine)


def test_creating_a_project_in_a_foreign_org_is_refused():
    engine = new_engine(owners=())
    admin = member_panel(engine, org_a(engine), "admin")
    before = len(engine.state.projects)
    r = admin.post("/proyectos/nuevo", data={"org": org_b(engine), "name": "Sneaky", "enforcement": "observe"})
    assert r.status_code in (303, 403) and len(engine.state.projects) == before


def test_creating_a_project_needs_the_csrf_token():
    engine = new_engine()
    panel = panel_of(engine)
    before = len(engine.state.projects)
    r = panel.post("/proyectos/nuevo", data={"org": org_a(engine), "name": "X", "enforcement": "observe"},
                   csrf=False, headers={"Origin": "http://testserver"})
    assert r.status_code == 403 and len(engine.state.projects) == before


def test_when_saving_the_mode_fails_the_project_is_still_opened_and_the_person_is_told():
    engine = new_engine()

    class FailPatch(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.ASGITransport(app=engine)

        async def handle_async_request(self, request):
            if request.method == "PATCH":
                return httpx.Response(500, json={"error": "boom"})
            return await self.inner.handle_async_request(request)

    panel = make_panel(httpx.AsyncClient(transport=FailPatch(), base_url="http://engine"),
                       transport=google_transport(email=EMAIL))
    old = {project_a(engine), project_b(engine)}
    r = panel.post("/proyectos/nuevo", data={"org": org_a(engine), "name": "Nuevo", "enforcement": "enforce"})
    assert r.status_code == 303
    (p,) = created_projects(engine, org_a(engine), exclude=old)
    assert session_of(panel)["project"] == p.id
    assert "no se pudo guardar el modo" in panel.get("/proyectos").text


def test_the_first_project_of_a_new_org_can_be_created_without_any_current_project():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/organizaciones/nueva", data={"name": "Acme"})
    org_id = session_of(panel)["org"]
    r = panel.post("/proyectos/nuevo", data={"org": org_id, "name": "App", "enforcement": "observe"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    (p,) = created_projects(engine, org_id)
    assert session_of(panel)["project"] == p.id


# --- delete organization -------------------------------------------------------------------------------


def make_empty_org(engine, panel):
    engine_org = next(o for o in engine.state.orgs.values() if o.id == org_b(engine))
    engine.state.projects[project_b(engine)].deleted_at = "2026-01-01T00:00:00Z"
    return engine_org


def test_the_owner_of_an_empty_org_sees_delete_with_a_name_confirmation():
    engine = new_engine()
    panel = panel_of(engine)
    org = make_empty_org(engine, panel)
    html = panel.get("/proyectos", params={"org": org.id}).text
    assert "Eliminar organización" in html
    form = re.search(r'<form[^>]*action="/organizaciones/%s/eliminar"[^>]*>(.*?)</form>' % org.id, html, re.S).group(1)
    assert 'name="csrf"' in form and re.search(r'<input[^>]*name="confirm"', form) and "Demo 2" in html


def test_delete_is_not_offered_when_the_org_has_projects_or_the_role_is_not_owner():
    engine = new_engine()
    panel = panel_of(engine)
    assert "Eliminar organización" not in panel.get("/proyectos").text
    admin_engine = new_engine(owners=())
    admin = member_panel(admin_engine, org_b(admin_engine), "admin")
    admin_engine.state.projects[project_b(admin_engine)].deleted_at = "2026-01-01T00:00:00Z"
    assert "Eliminar organización" not in admin.get("/proyectos", params={"org": org_b(admin_engine)}).text


def test_deleting_with_the_exact_name_removes_the_org_and_says_so():
    engine = new_engine()
    panel = panel_of(engine)
    org = make_empty_org(engine, panel)
    r = panel.post(f"/organizaciones/{org.id}/eliminar", data={"confirm": "Demo 2"})
    assert r.status_code == 303 and r.headers["location"] == "/proyectos"
    assert org.deleted_at is not None
    html = panel.get("/proyectos").text
    assert "Organización «Demo 2» eliminada." in html and org.id not in html.replace("Organización «Demo 2»", "")


def test_a_wrong_confirmation_deletes_nothing():
    engine = new_engine()
    panel = panel_of(engine)
    org = make_empty_org(engine, panel)
    r = panel.post(f"/organizaciones/{org.id}/eliminar", data={"confirm": "demo 2"})
    assert r.status_code == 303 and org.deleted_at is None
    assert "El nombre no coincide" in panel.get("/proyectos").text


def test_a_conflict_from_the_engine_is_explained():
    engine = new_engine()
    panel = panel_of(engine)
    r = panel.post(f"/organizaciones/{org_a(engine)}/eliminar", data={"confirm": "Demo 1"})
    assert r.status_code == 303 and engine.state.orgs[org_a(engine)].deleted_at is None
    assert "todavía tiene proyectos" in panel.get("/proyectos").text


def test_an_admin_forging_the_delete_is_refused_by_the_engine():
    engine = new_engine(owners=())
    admin = member_panel(engine, org_b(engine), "admin")
    engine.state.projects[project_b(engine)].deleted_at = "2026-01-01T00:00:00Z"
    admin.post(f"/organizaciones/{org_b(engine)}/eliminar", data={"confirm": "Demo 2"})
    assert engine.state.orgs[org_b(engine)].deleted_at is None
    assert "Solo un owner puede eliminar la organización." in admin.get("/proyectos").text


def test_deleting_an_org_the_person_is_not_in_does_nothing():
    engine = new_engine(owners=())
    viewer = member_panel(engine, org_a(engine), "viewer")
    r = viewer.post(f"/organizaciones/{org_b(engine)}/eliminar", data={"confirm": "Demo 2"})
    assert r.status_code == 303 and engine.state.orgs[org_b(engine)].deleted_at is None


def test_deleting_needs_the_csrf_token():
    engine = new_engine()
    panel = panel_of(engine)
    org = make_empty_org(engine, panel)
    r = panel.post(f"/organizaciones/{org.id}/eliminar", data={"confirm": "Demo 2"}, csrf=False,
                   headers={"Origin": "http://testserver"})
    assert r.status_code == 403 and org.deleted_at is None


def test_deleting_the_current_org_clears_the_session_project():
    engine = new_engine()
    panel = panel_of(engine)
    open_project(panel, project_b(engine))
    engine.state.projects[project_b(engine)].deleted_at = "2026-01-01T00:00:00Z"
    panel.post(f"/organizaciones/{org_b(engine)}/eliminar", data={"confirm": "Demo 2"})
    s = session_of(panel)
    assert "project" not in s and s["org"] == org_a(engine)  # the selector opens on an org they still have
    assert panel.get("/actividad").headers["location"] == "/proyectos"
    assert "Demo 1" in panel.get("/proyectos").text


# --- sidebar ---------------------------------------------------------------------------------------


def side(html):
    return re.search(r'<aside id="side".*?</aside>', html, re.S).group(0)


def test_the_sidebar_top_is_the_switcher_card_with_org_role_project_and_mode():
    engine = new_engine()
    s = side(panel_of(engine).get("/").text)
    card = re.search(r'<a[^>]*class="switcher"[^>]*>(.*?)</a>', s, re.S)
    assert card and 'href="/proyectos"' in card.group(0)
    assert 'aria-label="Cambiar de organización o de proyecto"' in card.group(0)
    t = text(card.group(1))
    assert "Demo 1 · Owner" in t and "Demo 1" in t and "Bloquea" in t
    assert "Solo observa" not in t


def test_an_observing_project_shows_solo_observa_in_the_review_color():
    engine = new_engine()
    engine.state.projects[project_a(engine)].enforcement = "observe"
    s = side(panel_of(engine).get("/").text)
    assert re.search(r'class="[^"]*mode-observe[^"]*"[^>]*>\s*Solo observa', s)


def test_the_switcher_role_follows_the_role_in_the_current_org():
    engine = new_engine(owners=())
    viewer = member_panel(engine, org_a(engine), "viewer")
    assert "Demo 1 · Viewer" in text(side(viewer.get("/").text))


def test_the_switcher_follows_the_project_after_switching():
    engine = new_engine()
    panel = panel_of(engine)
    assert "Demo 1 · Owner" in text(side(panel.get("/").text))
    open_project(panel, project_b(engine))
    t = text(side(panel.get("/").text))
    assert "Demo 2 · Owner" in t and "Demo 1 · Owner" not in t


def test_nav_groups_link_only_pages_that_exist():
    s = side(panel_of(new_engine()).get("/").text)
    nav = re.search(r'<nav[^>]*aria-label="Secciones"[^>]*>(.*?)</nav>', s, re.S).group(1)
    assert "ACCESOS" in nav and "ORGANIZACIÓN" in nav and "PROYECTO" in nav
    hrefs = re.findall(r'<a [^>]*href="([^"]+)"', nav)
    assert hrefs == ["/", "/actividad", "/usuarios", "/rutas", "/claves", "/proyectos", "/miembros", "/auditoria"]
    for dead in ("Ajustes",):
        assert dead not in nav
    proyectos = re.search(r'<a [^>]*href="/proyectos"[^>]*>', nav).group(0)
    assert 'hx-boost="false"' in proyectos and "data-full" in proyectos


def test_a_poll_does_not_reload_the_switcher_data():
    engine = new_engine()
    seen = []

    class Spy(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.ASGITransport(app=engine)

        async def handle_async_request(self, request):
            seen.append(request.url.path)
            return await self.inner.handle_async_request(request)

    panel = make_panel(httpx.AsyncClient(transport=Spy(), base_url="http://engine"),
                       transport=google_transport(email=EMAIL))
    seen.clear()
    assert panel.get("/", headers={"HX-Request": "true"}).status_code == 200
    assert "/v1/me/orgs" not in seen and not any(p.startswith("/v1/projects/") for p in seen)
    seen.clear()
    assert panel.get("/").status_code == 200
    assert "/v1/me/orgs" in seen


def test_the_sidebar_still_renders_when_the_engine_cannot_describe_the_project():
    client = stub_client(lambda r: httpx.Response(200, json={"decisions": [], "activity": []}))
    s = side(make_panel(client).get("/").text)
    assert 'href="/proyectos"' in s and "Cambiar de organización o de proyecto" in s


def test_viewer_targets_and_focus_are_44px_in_css():
    css = TestClient(panel_of(new_engine()).app).get("/static/panel.css").text
    for selector in (".switcher", ".proj", ".org", ".field input[type=text]", ".choice"):
        assert selector in css, selector
    assert "min-height:44px" in css.replace(" ", "")
