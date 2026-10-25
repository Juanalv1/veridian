"""Panel: audit page (contract 15), the sidebar per role, and who may mark a decision normal (decision 3).

Over the real fake engine, so what the panel sends is what the engine accepts and audits.
"""

import re

import httpx
from fastapi.testclient import TestClient

from tests.panel_support import make_panel, stub_client
from tests.test_panel_accounts import (OTHER, member_panel, new_engine, org_a, panel_of, project_a, project_b, side, text)

SEED = [{"user_id": "u_42", "ips": ["190.202.14.7"], "routes": ["GET /invoices"]}]
ATTACK = {"id": "e_atk", "ts": "2026-10-26T10:14:03Z", "user_id": "u_42", "session_id": "s_atk", "ip": "203.0.113.9",
          "method": "POST", "route": "/payouts/bank-account", "status": 200, "bytes": 1}
SDK_KEY = {"Authorization": "Bearer tok-a"}


def blocked_engine():
    """Engine with one open block (decision d_1) in project A."""
    engine = new_engine(seed=SEED)
    assert TestClient(engine).post("/v1/check", json={"event": ATTACK}, headers=SDK_KEY).json()["verdict"] == "block"
    return engine


def nav_links(html):
    nav = re.search(r'<nav[^>]*aria-label="Secciones"[^>]*>(.*?)</nav>', side(html), re.S).group(1)
    return re.findall(r'<a [^>]*href="([^"]+)"', nav)


def entries(html):
    return [text(m) for m in re.findall(r'<li[^>]*class="tm-entry"[^>]*>(.*?)</li>', html, re.S)]


# --- the page ---------------------------------------------------------------------------------------------------------


def test_the_audit_page_is_empty_at_first():
    html = panel_of(new_engine()).get("/auditoria").text
    assert "Auditoría" in html and "Quién cambió qué en Demo 1 y sus proyectos. Nadie puede editar ni borrar este registro." in text(html)
    assert "Todavía no hay cambios" in text(html)
    assert "Aquí aparecerán los bloqueos levantados y los cambios de claves, miembros y ajustes." in text(html)
    assert entries(html) == [] and "Ver anteriores" not in html


def test_entries_read_as_sentences_newest_first_with_where_and_detail():
    engine = blocked_engine()
    panel = panel_of(engine)
    member_panel(engine, org_a(engine), "viewer")
    panel.post("/miembros/invitar", data={"email": "marta@example.com", "role": "admin"})
    panel.post("/claves/nueva", data={"name": "Producción"})
    key = next(iter(engine.state.api_keys.values()))
    panel.post(f"/claves/{key.id}/revocar")
    panel.post("/decisions/d_1/normal", data={"user": "u_42"})
    other = next(p.id for p in engine.state.people.values() if p.email == OTHER)
    panel.post(f"/miembros/{other}/rol", data={"role": "admin"})
    got = entries(panel.get("/auditoria").text)
    assert len(got) == 5
    assert got[0].endswith("Ana cambió el rol de other@example.com Demo 1 · de Viewer a Admin")
    assert "Ana levantó el bloqueo de la decisión d_1 Demo 1 · El perfil aprende ese acceso." in got[1]
    assert f"Ana revocó la clave {key.prefix} Demo 1 · Producción" in got[2]
    assert f"Ana creó la clave {key.prefix} Demo 1 · Producción" in got[3]
    assert "Ana invitó a marta@example.com Demo 1 · como Admin" in got[4]
    assert all(re.search(r"\d{1,2} [a-z]{3} \d\d:\d\d UTC", e) for e in got)


def test_other_audit_actions_have_their_own_sentences():
    engine = new_engine()
    panel = panel_of(engine)
    member_panel(engine, org_a(engine), "viewer")
    other = next(p.id for p in engine.state.people.values() if p.email == OTHER)
    panel.post("/miembros/invitar", data={"email": "p@example.com", "role": "viewer"})
    panel.post("/miembros/invitar", data={"email": "p@example.com", "role": "viewer"})
    inv = engine.state.orgs[org_a(engine)].pending()[0]
    panel.post(f"/invitaciones/{inv['id']}/cancelar")
    panel.post(f"/miembros/{other}/quitar")
    got = " | ".join(entries(panel.get("/auditoria").text))
    assert "Ana quitó a other@example.com Demo 1 · era Viewer" in got
    assert "Ana canceló la invitación a p@example.com Demo 1 · como Viewer" in got
    assert "Ana reenvió la invitación a p@example.com" in got and "Ana invitó a p@example.com" in got


def test_names_and_targets_are_escaped():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/claves/nueva", data={"name": "<b>x</b>"})
    html = panel.get("/auditoria").text
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


def test_a_viewer_gets_the_no_access_state_from_the_engine_403():
    engine = new_engine()
    panel_of(engine)
    viewer = member_panel(engine, org_a(engine), "viewer")
    r = viewer.get("/auditoria")
    assert r.status_code == 403
    assert "No tienes acceso a la auditoría" in text(r.text)
    assert "El registro de cambios lo ven los admins y owners de Demo 1." in text(r.text)
    assert entries(r.text) == []


def test_an_admin_reads_the_audit():
    engine = new_engine()
    panel_of(engine)
    admin = member_panel(engine, org_a(engine), "admin")
    assert admin.get("/auditoria").status_code == 200


def test_there_is_no_way_to_edit_the_audit_from_the_panel():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/claves/nueva", data={"name": "k"})
    assert 'method="post"' not in re.search(r'<main id="live".*</main>', panel.get("/auditoria").text, re.S).group(0)
    assert panel.post("/auditoria", data={}).status_code in (403, 405)
    for method in ("put", "delete", "patch"):
        assert getattr(panel, method)("/auditoria").status_code in (401, 403, 405)


# --- filters and pages ------------------------------------------------------------------------------------------------


def seeded_audit(engine, panel):
    panel.post("/miembros/invitar", data={"email": "marta@example.com", "role": "viewer"})
    panel.post("/claves/nueva", data={"name": "En A"})
    panel.post("/proyectos/abrir", data={"project_id": project_b(engine)})
    panel.post("/claves/nueva", data={"name": "En B"})
    panel.post("/proyectos/abrir", data={"project_id": project_a(engine)})


def test_where_filter_keeps_one_project_and_drops_org_level_entries():
    engine = new_engine()
    panel = panel_of(engine)
    seeded_audit(engine, panel)
    html = panel.get("/auditoria").text
    assert len(entries(html)) == 3
    assert re.search(r'<select[^>]*name="project".*?Toda la organización.*?Demo 1', html, re.S)
    only = entries(panel.get("/auditoria", params={"project": project_a(engine)}).text)
    assert len(only) == 1 and "En A" in only[0]
    selected = re.search(r'<option value="%s" selected' % project_a(engine), panel.get("/auditoria", params={"project": project_a(engine)}).text)
    assert selected


def test_an_unknown_project_in_the_filter_is_ignored():
    engine = new_engine()
    panel = panel_of(engine)
    seeded_audit(engine, panel)
    assert len(entries(panel.get("/auditoria", params={"project": "p_nope"}).text)) == 3


def test_what_filter_groups_actions():
    engine = new_engine()
    panel = panel_of(engine)
    seeded_audit(engine, panel)
    keys = entries(panel.get("/auditoria", params={"what": "claves"}).text)
    assert len(keys) == 2 and all("clave" in e for e in keys)
    team = entries(panel.get("/auditoria", params={"what": "miembros"}).text)
    assert len(team) == 1 and "marta@example.com" in team[0]
    assert entries(panel.get("/auditoria", params={"what": "bloqueos"}).text) == []
    assert "Ningún cambio coincide con este filtro" in text(panel.get("/auditoria", params={"what": "bloqueos"}).text)
    assert len(entries(panel.get("/auditoria", params={"what": "nonsense"}).text)) == 3


def test_who_filter_lists_the_members_and_filters_by_person():
    engine = new_engine()
    panel = panel_of(engine)
    admin = member_panel(engine, org_a(engine), "admin")
    panel.post("/claves/nueva", data={"name": "De Ana"})
    admin.post("/claves/nueva", data={"name": "De otra"})
    html = panel.get("/auditoria").text
    options = re.search(r'<select[^>]*name="who".*?</select>', html, re.S).group(0)
    assert "Todas las personas" in options and options.count("<option") == 3
    me = next(p.id for p in engine.state.people.values() if p.email == "ana@example.com")
    mine = entries(panel.get("/auditoria", params={"who": me}).text)
    assert len(mine) == 1 and "De Ana" in mine[0]


def test_older_entries_are_a_link_that_keeps_the_filters():
    engine = new_engine()
    panel = panel_of(engine)
    for n in range(55):
        panel.post("/miembros/invitar", data={"email": f"p{n}@example.com", "role": "viewer"})
    first = panel.get("/auditoria").text
    assert len(entries(first)) == 50 and "p54@example.com" in entries(first)[0]
    link = re.search(r'<a[^>]*href="(/auditoria\?[^"]+)"[^>]*>\s*Ver anteriores', first)
    assert link and "before=" in link.group(1)
    older = panel.get(link.group(1).replace("&amp;", "&")).text
    assert len(entries(older)) == 5 and "p0@example.com" in entries(older)[-1]
    assert "Ver anteriores" not in older


def test_the_older_link_carries_the_selected_filters():
    engine = new_engine()
    panel = panel_of(engine)
    for n in range(55):
        panel.post("/miembros/invitar", data={"email": f"p{n}@example.com", "role": "viewer"})
    page = panel.get("/auditoria", params={"what": "miembros", "project": ""}).text
    href = re.search(r'href="(/auditoria\?[^"]+)"[^>]*>\s*Ver anteriores', page).group(1).replace("&amp;", "&")
    assert "what=miembros" in href


# --- the sidebar per role ----------------------------------------------------------------------------------------------


def test_owner_and_admin_see_keys_members_and_audit_links():
    engine = new_engine()
    owner = panel_of(engine)
    admin = member_panel(engine, org_a(engine), "admin")
    for panel in (owner, admin):
        assert nav_links(panel.get("/").text) == ["/", "/actividad", "/usuarios", "/rutas", "/claves", "/proyectos",
                                                   "/miembros", "/auditoria"]
        html = side(panel.get("/").text)
        assert html.index("PROYECTO") < html.index("ORGANIZACIÓN")


def test_a_viewer_sees_neither_keys_nor_audit_links_on_any_page():
    engine = new_engine()
    panel_of(engine)
    viewer = member_panel(engine, org_a(engine), "viewer")
    for path in ("/", "/usuarios", "/miembros", "/actividad"):
        links = nav_links(viewer.get(path).text)
        assert "/claves" not in links and "/auditoria" not in links, path
        assert "/miembros" in links and "/proyectos" in links
    nav = re.search(r'<nav[^>]*aria-label="Secciones"[^>]*>(.*?)</nav>', side(viewer.get("/").text), re.S).group(1)
    assert "PROYECTO" not in nav.replace("Proyectos", "")


def test_the_current_section_is_marked_on_the_new_pages():
    engine = new_engine()
    panel = panel_of(engine)
    for path, href in (("/auditoria", "/auditoria"), ("/claves", "/claves"), ("/miembros", "/miembros")):
        html = panel.get(path).text
        assert re.search(r'<a [^>]*href="%s"[^>]*aria-current="page"' % href, html), path


def test_the_new_pages_link_the_live_pages_with_a_target_that_exists():
    # The sidebar boosts navigation into #live: every page with the sidebar must have it.
    panel = panel_of(new_engine())
    for path in ("/auditoria", "/claves", "/miembros"):
        assert 'id="live"' in panel.get(path).text, path


def test_audit_and_keys_pages_are_usable_at_390px_markup_wise():
    html = panel_of(new_engine()).get("/auditoria").text
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in html
    css = panel_of(new_engine()).get("/static/panel.css").text
    assert ".tm-entry" in css and "grid-template-columns" in css


# --- who may mark a decision normal -----------------------------------------------------------------------------------------


def decision_page(panel, headers=None):
    return panel.get("/users/u_42/decisions/d_1", headers=headers or {})


def test_owner_and_admin_get_the_mark_normal_button():
    engine = blocked_engine()
    owner = panel_of(engine)
    admin = member_panel(engine, org_a(engine), "admin")
    for panel in (owner, admin):
        html = decision_page(panel).text
        assert re.search(r'<button[^>]*class="btn btn-primary"[^>]*>\s*Esto era normal', html)
        assert not re.search(r'\sdisabled[\s>]', re.search(r'<button[^>]*>\s*Esto era normal', html).group(0))


def test_a_viewer_gets_a_disabled_button_with_the_reason_on_the_page_and_on_every_poll():
    engine = blocked_engine()
    panel_of(engine)
    viewer = member_panel(engine, org_a(engine), "viewer")
    for headers in (None, {"HX-Request": "true"}):
        html = decision_page(viewer, headers).text
        button = re.search(r'<button[^>]*>\s*Esto era normal', html).group(0)
        assert re.search(r'\sdisabled[\s>]', button) and 'aria-describedby="mark-why"' in button and 'type="submit"' not in button
        assert re.search(r'id="mark-why"[^>]*>\s*Solo un admin u owner puede marcar una decisión como normal', html)
        assert 'action="/decisions/d_1/normal"' not in html


def test_the_server_rejects_a_viewer_who_forges_the_post_and_says_why():
    engine = blocked_engine()
    panel_of(engine)
    viewer = member_panel(engine, org_a(engine), "viewer")
    r = viewer.post("/decisions/d_1/normal", data={"user": "u_42"})
    assert r.status_code == 303 and "error=forbidden" in r.headers["location"]
    page = viewer.get(r.headers["location"])
    assert page.status_code == 200
    assert "Tu rol no permite marcar decisiones como normales. Solo un admin u owner puede hacerlo." in text(page.text)
    assert "No se pudo marcar" not in page.text
    assert engine.state.projects[project_a(engine)].state.decisions[0]["status"] == "open"
    assert not engine.state.orgs[org_a(engine)].audit


def test_the_same_rejection_in_place_for_htmx():
    engine = blocked_engine()
    panel_of(engine)
    viewer = member_panel(engine, org_a(engine), "viewer")
    r = viewer.post("/decisions/d_1/normal", data={"user": "u_42"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and 'id="live"' in r.text
    assert "Tu rol no permite marcar decisiones como normales" in text(r.text)


def test_an_admin_marks_normal_and_it_shows_up_in_the_audit():
    engine = blocked_engine()
    panel_of(engine)
    admin = member_panel(engine, org_a(engine), "admin")
    assert admin.post("/decisions/d_1/normal", data={"user": "u_42"}).status_code == 303
    assert engine.state.projects[project_a(engine)].state.decisions[0]["status"] == "marked_normal"
    assert "Ana levantó el bloqueo de la decisión d_1" in " ".join(entries(admin.get("/auditoria").text))


def test_a_canned_engine_403_on_mark_normal_is_surfaced_not_a_generic_error():
    def handler(request):
        if request.url.path == "/v1/decisions/d_1/normal":
            return httpx.Response(403, json={"error": "forbidden"})
        if request.url.path == "/v1/decisions":
            return httpx.Response(200, json={"decisions": [{
                "id": "d_1", "ts": "2026-10-26T14:14:05Z", "user_id": "u_42", "decision": "block", "status": "open", "signals": {},
                "rationale": "r", "event_ids": ["e_1"], "access": {"ts": "2026-10-26T14:14:03Z", "method": "POST", "route": "/x"}}]})
        return httpx.Response(404, json={"error": "not_found"})

    panel = make_panel(stub_client(handler))
    r = panel.post("/decisions/d_1/normal", data={"user": "u_42"})
    assert "error=forbidden" in r.headers["location"]
    assert "Tu rol no permite marcar decisiones como normales" in text(panel.get(r.headers["location"]).text)
    # With the role unknown (this engine does not answer /v1/me/orgs) the button is still offered: the engine decides.
    assert "Esto era normal" in panel.get("/users/u_42/decisions/d_1").text
