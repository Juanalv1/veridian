"""Panel: API keys of the open project (T5, contract 12) and the end-to-end path panel -> SDK middleware -> revoke.

The plaintext key exists in exactly one place: the response of the POST that created it.
"""

import re

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tests.panel_support import make_panel, session_of
from tests.test_panel_accounts import (OTHER, link, member_panel, new_engine, org_a, panel_of, project_a, project_b, text)
from veridian_sdk import VeridianMiddleware

KEY = re.compile(r"vrd_[a-z0-9]{8}_[A-Za-z0-9_-]{43}")


def create(panel, name="Producción"):
    r = panel.post("/claves/nueva", data={"name": name})
    assert r.status_code == 200, r.text
    return r, KEY.search(r.text).group(0)


def row(html, name):
    m = re.search(r'<tr[^>]*data-key="%s"[^>]*>(.*?)</tr>' % re.escape(name), html, re.S)
    return m.group(1) if m else None


def flash_of(panel, path="/claves"):
    return text(re.search(r'<div class="note flash[^"]*"[^>]*>(.*?)</div>', panel.get(path).text, re.S).group(1))


def key_id(engine, name):
    return next(k.id for k in engine.state.api_keys.values() if k.name == name)


# --- the page ------------------------------------------------------------------------------------------------


def test_an_owner_sees_the_keys_page_with_an_empty_state_and_a_create_form():
    html = panel_of(new_engine()).get("/claves").text
    assert "Claves de API" in html and "El middleware de tu app usa una clave" in text(html)
    assert "Este proyecto no tiene claves activas" in text(html) and "Sin clave, el middleware no puede reportar" in text(html)
    assert 'action="/claves/nueva"' in html and "Crear clave" in html and "<table" not in html


def test_the_list_shows_name_prefix_created_last_use_and_status_but_never_a_key():
    engine = new_engine()
    panel = panel_of(engine)
    _, key = create(panel, "Producción")
    html = panel.get("/claves").text
    assert key not in html and key.split("_", 2)[2] not in html
    line = text(row(html, "Producción"))
    assert f"{key[:12]}…" in line and ", por Ana" in line and "sin uso todavía" in line and "Activa" in line
    assert "Revocar" in line
    for head in ("NOMBRE", "CLAVE", "CREADA", "ÚLTIMO USO", "ESTADO"):
        assert head in html
    assert "Este proyecto no tiene claves activas" not in text(html)


def test_last_use_is_shown_once_the_key_is_used():
    engine = new_engine()
    panel = panel_of(engine)
    _, key = create(panel)
    TestClient(engine).get("/v1/blocklist", headers={"Authorization": f"Bearer {key}"})
    assert "hace unos segundos" in text(row(panel.get("/claves").text, "Producción"))


def test_a_revoked_key_stays_listed_as_revoked_with_who_and_when_and_no_button():
    engine = new_engine()
    panel = panel_of(engine)
    create(panel, "Anterior")
    panel.post(f"/claves/{key_id(engine, 'Anterior')}/revocar")
    line = text(row(panel.get("/claves").text, "Anterior"))
    assert "Revocada" in line and "revocada el " in line and "por Ana" in line and "Revocar" not in line


def test_keys_are_listed_per_project():
    engine = new_engine()
    panel = panel_of(engine)
    create(panel, "En A")
    panel.post("/proyectos/abrir", data={"project_id": project_b(engine)})
    assert row(panel.get("/claves").text, "En A") is None
    assert "Este proyecto no tiene claves activas" in text(panel.get("/claves").text)


def test_a_viewer_gets_the_no_access_state_and_the_engine_is_the_one_that_decides():
    engine = new_engine()
    panel_of(engine)
    viewer = member_panel(engine, org_a(engine), "viewer")
    r = viewer.get("/claves")
    assert r.status_code == 403
    assert "No tienes acceso a las claves" in text(r.text)
    assert "Las claves de API las ven y administran los admins y owners de Demo 1." in text(r.text)
    assert "Crear clave" not in r.text and "<table" not in r.text
    forged = viewer.post("/claves/nueva", data={"name": "mine"})
    assert forged.status_code == 403 and KEY.search(forged.text) is None
    assert not engine.state.api_keys


def test_an_admin_manages_keys_too():
    engine = new_engine()
    panel_of(engine)
    admin = member_panel(engine, org_a(engine), "admin")
    _, key = create(admin)
    assert KEY.fullmatch(key)


# --- creating: the one time the key is shown -----------------------------------------------------------------------


def test_creating_shows_the_key_once_with_copy_snippet_and_confirmation():
    engine = new_engine()
    panel = panel_of(engine)
    r, key = create(panel, "Producción")
    page = text(r.text)
    assert "Copia la clave «Producción» ahora" in page
    assert "Es la única vez que la mostramos. Guardamos solo su huella, así que si la pierdes tendrás que crear otra." in page
    # the visible key, the copy button's payload, and the key already in the environment line of the snippet
    assert f"<code" in r.text and r.text.count(key) == 3
    assert f"VERIDIAN_API_KEY={key}</code>" in r.text
    assert "VeridianMiddleware" in r.text and 'data-copy-from="snippet-python"' in r.text
    assert re.search(r'<a[^>]*href="/claves"[^>]*>\s*Ya la guardé', r.text)
    assert "data-copy" in r.text
    assert 'action="/claves/nueva"' not in r.text  # the form is hidden while the key is shown


def test_the_response_that_shows_the_key_is_never_cached():
    r, _ = create(panel_of(new_engine()))
    assert "no-store" in r.headers["cache-control"]


def test_the_key_is_not_in_the_session_the_next_page_or_the_panels_other_pages():
    engine = new_engine()
    panel = panel_of(engine)
    _, key = create(panel)
    assert key not in str(session_of(panel)) and key.split("_", 2)[2] not in str(session_of(panel))
    for path in ("/claves", "/miembros", "/proyectos", "/"):
        assert key not in panel.get(path).text, path
    assert key not in panel.cookies.get("session", "")


def test_the_panel_keeps_no_plaintext_and_the_engine_only_a_hash():
    engine = new_engine()
    panel = panel_of(engine)
    _, key = create(panel)
    assert key not in repr(list(engine.state.api_keys.values()))


def test_a_name_is_required_and_limited():
    engine = new_engine()
    panel = panel_of(engine)
    for bad in ("", "   ", "x" * 81):
        r = panel.post("/claves/nueva", data={"name": bad})
        assert r.status_code == 400 and "Escribe un nombre para la clave (hasta 80 caracteres)." in text(r.text)
        assert KEY.search(r.text) is None
    assert not engine.state.api_keys


def test_the_name_is_escaped_in_the_reveal_and_the_list():
    engine = new_engine()
    panel = panel_of(engine)
    r, _ = create(panel, "<img src=x onerror=alert(1)>")
    assert "<img src=x" not in r.text and "&lt;img src=x" in r.text
    assert "<img src=x" not in panel.get("/claves").text


# --- revoking ------------------------------------------------------------------------------------------------------


def test_revoking_is_immediate_and_says_so():
    engine = new_engine()
    panel = panel_of(engine)
    _, key = create(panel, "Producción")
    create(panel, "Staging")
    r = panel.post(f"/claves/{key_id(engine, 'Producción')}/revocar")
    assert r.status_code == 303 and r.headers["location"] == "/claves"
    assert "Clave «Producción» revocada" in flash_of(panel)
    assert TestClient(engine).get("/v1/blocklist", headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_revoking_the_last_active_key_is_allowed_with_a_warning():
    engine = new_engine()
    panel = panel_of(engine)
    create(panel, "Única")
    panel.post(f"/claves/{key_id(engine, 'Única')}/revocar")
    page = panel.get("/claves").text
    assert re.search(r'class="note flash warn"', page)
    assert "Ya no queda ninguna clave activa: el middleware no puede reportar y Veridian no protege la app" in text(page)
    assert "Ya no queda ninguna clave activa" not in text(panel.get("/claves").text).replace("Este proyecto no tiene claves activas", "")


def test_a_key_of_another_project_cannot_be_revoked_through_this_one():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/proyectos/abrir", data={"project_id": project_b(engine)})
    create(panel, "En B")
    panel.post("/proyectos/abrir", data={"project_id": project_a(engine)})
    panel.post(f"/claves/{key_id(engine, 'En B')}/revocar")
    assert "Esa clave ya no existe" in flash_of(panel)
    assert next(iter(engine.state.api_keys.values())).revoked_at is None


def test_a_viewer_forging_a_revoke_is_refused():
    engine = new_engine()
    owner = panel_of(engine)
    create(owner, "Producción")
    viewer = member_panel(engine, org_a(engine), "viewer")
    viewer.post(f"/claves/{key_id(engine, 'Producción')}/revocar")
    assert "No tienes permiso" in flash_of(viewer, "/miembros")
    assert next(iter(engine.state.api_keys.values())).revoked_at is None


# --- security ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/claves/nueva", "/claves/key_x/revocar"])
def test_key_writes_need_csrf_and_a_session(path):
    engine = new_engine()
    panel = panel_of(engine)
    assert panel.post(path, data={"name": "x"}, csrf=False).status_code == 403
    assert panel.post(path, data={"name": "x"}, headers={"Origin": "http://evil.example"}).status_code == 403
    anonymous = panel_of(engine, login=False)
    assert anonymous.post(path, data={"name": "x"}, csrf=False).status_code == 401
    assert anonymous.get("/claves").headers["location"] == "/login"
    assert not engine.state.api_keys


def test_an_unreachable_engine_gets_a_message():
    def down(request):
        raise httpx.ConnectError("down")

    from tests.panel_support import stub_client
    panel = make_panel(stub_client(down))
    r = panel.get("/claves")
    assert "No se pudo conectar con el motor" in r.text and r.status_code == 502


# --- end to end: panel -> SDK middleware -> revoke ---------------------------------------------------------------------


ENGINE_URL = "http://engine"


def protected_app(calls):
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def item(item_id: str, request: Request):
        calls.append(item_id)
        return {"id": item_id}

    return app


async def test_a_key_made_in_the_panel_works_in_the_sdk_and_stops_when_revoked():
    engine = new_engine()
    panel = panel_of(engine)
    _, key = create(panel, "Producción")
    pid = project_a(engine)
    engine_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=engine), base_url=ENGINE_URL)
    calls = []
    mw = VeridianMiddleware(protected_app(calls), token=key, base_url=ENGINE_URL, http_client=engine_client, background=False,
                            user_id=lambda r: r.cookies.get("uid"), session_id=lambda r: r.cookies.get("sid"))
    app_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mw, client=("198.51.100.1", 5555)), base_url="http://app")

    # 1. The new key authenticates the middleware at once: its events reach the engine, under this project.
    r = await app_client.get("/items/1", cookies={"uid": "u_1", "sid": "s_1"})
    assert r.status_code == 200 and calls == ["1"]
    await mw.flush()
    await mw.refresh_blocklist()
    assert [e.route for e in engine.state.projects[pid].state.log] == ["/items/{item_id}"]
    assert "hace unos segundos" in text(row(panel.get("/claves").text, "Producción"))  # the panel shows it was used

    # 2. Revoke in the panel: the engine answers 401 to that key, like to an unknown one.
    assert panel.post(f"/claves/{key_id(engine, 'Producción')}/revocar").status_code == 303
    direct = await engine_client.get("/v1/blocklist", headers={"Authorization": f"Bearer {key}"})
    assert direct.status_code == 401 and direct.json() == {"error": "unauthorized"}
    unknown = await engine_client.get("/v1/blocklist", headers={"Authorization": "Bearer vrd_zzzzzzzz_" + "A" * 43})
    assert (unknown.status_code, unknown.json()) == (direct.status_code, direct.json())

    # 3. The SDK fails open (its documented behavior): the app keeps answering, nothing is learned.
    await mw.refresh_blocklist()
    r = await app_client.get("/items/2", cookies={"uid": "u_1", "sid": "s_1"})
    assert r.status_code == 200 and calls == ["1", "2"]
    await mw.flush()
    assert len(engine.state.projects[pid].state.log) == 1  # the second event never got in
    await mw.aclose()
    await app_client.aclose()
