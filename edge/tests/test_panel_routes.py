"""Panel: /rutas edits the route classification of the session project.

Over the real fake engine, so what the panel sends is what the engine accepts and audits.
"""

import re

from tests.test_panel_accounts import member_panel, new_engine, org_a, panel_of, project_a, project_b, text


def routes_of(engine, project_id):
    return engine.state.projects[project_id].state.routes


def main_of(panel):
    return re.search(r'<main id="live".*?</main>', panel.get("/rutas").text, re.S).group(0)


def test_the_read_only_note_is_gone():
    t = text(main_of(panel_of(new_engine())))
    assert "solo lectura" not in t and "se configura en el motor" not in t


def test_an_owner_gets_the_add_form_and_per_route_controls():
    main = main_of(panel_of(new_engine()))
    assert 'action="/rutas/guardar"' in main and 'action="/rutas/quitar"' in main
    assert re.search(r'<select[^>]*name="method"', main) and re.search(r'<select[^>]*name="class"', main)
    assert 'name="route"' in main and "Agregar ruta" in text(main)


def test_an_owner_adds_a_route_to_the_session_project_only():
    engine = new_engine()
    panel = panel_of(engine)
    r = panel.post("/rutas/guardar", data={"method": "POST", "route": "/transfers", "class": "critical"})
    assert r.status_code == 303 and r.headers["location"] == "/rutas"
    assert routes_of(engine, project_a(engine))["POST /transfers"] == "critical"
    assert "POST /transfers" not in routes_of(engine, project_b(engine))
    page = panel.get("/rutas").text
    assert "Ruta guardada: POST /transfers es crítica." in text(page)
    assert "routes.rule_set" in [e["action"] for e in engine.state.orgs[org_a(engine)].audit]


def test_an_owner_changes_a_class_and_removes_a_route():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/rutas/guardar", data={"method": "GET", "route": "/invoices/export", "class": "critical"})
    assert routes_of(engine, project_a(engine))["GET /invoices/export"] == "critical"
    r = panel.post("/rutas/quitar", data={"method": "GET", "route": "/invoices/export"})
    assert r.status_code == 303
    assert "GET /invoices/export" not in routes_of(engine, project_a(engine))
    assert "Ruta quitada: GET /invoices/export ahora es normal." in text(panel.get("/rutas").text)


def test_invalid_input_is_refused_by_the_panel_without_calling_the_engine():
    engine = new_engine()
    panel = panel_of(engine)
    before = dict(routes_of(engine, project_a(engine)))
    for data in ({"method": "BREW", "route": "/x", "class": "critical"},
                 {"method": "POST", "route": "x y", "class": "critical"},
                 {"method": "POST", "route": "/x", "class": "deadly"}):
        assert panel.post("/rutas/guardar", data=data).status_code == 303
    assert routes_of(engine, project_a(engine)) == before
    assert not engine.state.orgs[org_a(engine)].audit
    assert "La ruta tiene que empezar con /" in text(panel.get("/rutas").text)


def test_a_viewer_sees_the_list_read_only_and_a_forged_post_is_refused():
    engine = new_engine()
    panel_of(engine)
    viewer = member_panel(engine, org_a(engine), "viewer")
    main = main_of(viewer)
    assert "POST /payouts/bank-account" in text(main)
    assert 'action="/rutas/guardar"' not in main and 'action="/rutas/quitar"' not in main
    assert "Solo un admin u owner puede cambiar la clasificación." in text(main)
    viewer.post("/rutas/guardar", data={"method": "POST", "route": "/transfers", "class": "critical"})
    assert "POST /transfers" not in routes_of(engine, project_a(engine))
    assert "Tu rol no permite cambiar la clasificación" in text(viewer.get("/rutas").text)


def test_a_new_project_does_not_show_the_demo_routes():
    engine = new_engine()
    panel = panel_of(engine)
    r = panel.post("/proyectos/nuevo", data={"org": org_a(engine), "name": "elve-lorio", "enforcement": "observe"})
    assert r.status_code == 303
    t = text(main_of(panel))
    assert "/payouts/bank-account" not in t and "Ninguna ruta está clasificada" in t
