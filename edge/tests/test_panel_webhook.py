"""Panel: the project webhook (contract 13). The signing secret exists in exactly one place: the response of the
PUT that created it. Over the real fake engine, so what the panel sends is what the engine accepts."""

import re

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.panel_support import make_panel, session_of, stub_client
from tests.test_panel_accounts import member_panel, new_engine, org_a, panel_of, project_a, text

URL = "https://tuapp.example.com/veridian/webhook"
SECRET = re.compile(r"whsec_[A-Za-z0-9_-]{20,}")
SEED = [{"user_id": "u_42", "ips": ["190.202.14.7"], "routes": ["GET /invoices"]}]
ATTACK = {"id": "e_atk", "ts": "2026-10-26T10:14:03Z", "user_id": "u_42", "session_id": "s_atk", "ip": "203.0.113.9",
          "method": "POST", "route": "/payouts/bank-account", "status": 200, "bytes": 1}


def save(panel, url=URL):
    return panel.post("/webhook", data={"url": url})


def flash_of(panel, path="/webhook"):
    return text(re.search(r'<div class="note flash[^"]*"[^>]*>(.*?)</div>', panel.get(path).text, re.S).group(1))


def state(engine):
    return engine.state.projects[project_a(engine)].state


def test_an_owner_sees_the_empty_form():
    r = panel_of(new_engine()).get("/webhook")
    assert r.status_code == 200
    page = text(r.text)
    assert "Webhook" in page and "Cuando Veridian bloquea un acceso, avisa a tu sistema" in page
    assert 'type="url"' in r.text and 'maxlength="2048"' in r.text and "Guardar" in page
    assert "Todavía no hubo envíos." in page
    assert r.text.count("btn-primary") == 1


def test_saving_shows_the_secret_once_never_cached_never_kept():
    engine = new_engine()
    panel = panel_of(engine)
    r = save(panel)
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    secret = SECRET.search(r.text).group(0)
    page = text(r.text)
    assert "Copia el secreto de firma ahora" in page and "Es la única vez que lo mostramos." in page
    assert "X-Veridian-Signature" in page and "data-copy" in r.text
    assert re.search(r'<a[^>]*href="/webhook"[^>]*>\s*Ya lo guardé', r.text)
    assert secret not in str(session_of(panel)) and secret not in panel.cookies.get("session", "")
    for path in ("/webhook", "/claves", "/auditoria", "/"):
        assert secret not in panel.get(path).text, path
    assert state(engine).webhook["url"] == URL


def test_with_a_webhook_the_page_shows_url_state_and_actions():
    engine = new_engine()
    panel = panel_of(engine)
    save(panel)
    r = panel.get("/webhook")
    page = text(r.text)
    assert URL in page and "Activo" in page and "Enviar prueba" in page and "Pausar" in page and "Eliminar" in page
    assert "genera un secreto nuevo" in page
    assert r.text.count("btn-primary") <= 1
    assert SECRET.search(r.text) is None


def test_pausing_and_activating():
    engine = new_engine()
    panel = panel_of(engine)
    save(panel)
    r = panel.post("/webhook/estado", data={"enabled": "false"})
    assert r.status_code == 303 and r.headers["location"] == "/webhook"
    assert state(engine).webhook["enabled"] is False
    assert "Pausado" in text(panel.get("/webhook").text) and "Activar" in text(panel.get("/webhook").text)
    panel.post("/webhook/estado", data={"enabled": "true"})
    assert state(engine).webhook["enabled"] is True


def test_deleting():
    engine = new_engine()
    panel = panel_of(engine)
    save(panel)
    r = panel.post("/webhook/eliminar")
    assert r.status_code == 303 and state(engine).webhook is None
    assert "Webhook eliminado" in flash_of(panel)


def test_the_test_send_flashes_both_outcomes_escaped():
    engine = new_engine()
    panel = panel_of(engine)
    save(panel)
    panel.post("/webhook/prueba")
    assert "La prueba llegó (HTTP 200)." in flash_of(panel)
    engine.state.webhook_test_result = {"delivered": False, "status_code": None, "error": "<b>timeout</b>"}
    panel.post("/webhook/prueba")
    html = panel.get("/webhook").text
    assert "<b>timeout</b>" not in html and "&lt;b&gt;timeout&lt;/b&gt;" in html
    assert "La prueba no llegó: &lt;b&gt;timeout&lt;/b&gt;" in text(html)


def test_an_unsafe_url_gets_a_field_error_and_nothing_is_stored():
    engine = new_engine()
    r = save(panel_of(engine), "http://localhost/hook")
    assert r.status_code == 422
    assert "La URL tiene que ser https y pública." in text(r.text) and 'aria-invalid="true"' in r.text
    assert state(engine).webhook is None


def test_deliveries_are_listed_after_a_block():
    engine = new_engine(seed=SEED)
    panel = panel_of(engine)
    save(panel)
    assert TestClient(engine).post("/v1/check", json={"event": ATTACK},
                                   headers={"Authorization": "Bearer tok-a"}).json()["verdict"] == "block"
    page = text(panel.get("/webhook").text)
    for head in ("ESTADO", "INTENTOS", "DECISIÓN", "ÚLTIMO ERROR", "CREADA"):
        assert head in page
    assert "Entregada" in page and "d_1" in page and "Todavía no hubo envíos." not in page


def test_a_viewer_gets_the_denied_state_and_cannot_forge_a_write():
    engine = new_engine()
    panel_of(engine)
    viewer = member_panel(engine, org_a(engine), "viewer")
    r = viewer.get("/webhook")
    assert r.status_code == 403 and "No tienes acceso al webhook" in text(r.text) and "Guardar" not in text(r.text)
    forged = save(viewer)
    assert forged.status_code == 403 and SECRET.search(forged.text) is None
    assert state(engine).webhook is None


def test_an_admin_manages_it_too():
    engine = new_engine()
    panel_of(engine)
    admin = member_panel(engine, org_a(engine), "admin")
    assert SECRET.search(save(admin).text)


@pytest.mark.parametrize("path", ["/webhook", "/webhook/estado", "/webhook/eliminar", "/webhook/prueba"])
def test_writes_need_csrf_and_a_session(path):
    engine = new_engine()
    panel = panel_of(engine)
    assert panel.post(path, data={"url": URL}, csrf=False).status_code == 403
    assert panel.post(path, data={"url": URL}, headers={"Origin": "http://evil.example"}).status_code == 403
    anonymous = panel_of(engine, login=False)
    assert anonymous.post(path, data={"url": URL}, csrf=False).status_code == 401
    assert state(engine).webhook is None


def test_the_url_is_escaped():
    engine = new_engine()
    panel = panel_of(engine)
    save(panel, 'https://example.com/"><script>x</script>')
    html = panel.get("/webhook").text
    assert "<script>x</script>" not in html


def test_an_unreachable_engine_gets_a_message():
    def down(request):
        raise httpx.ConnectError("down")

    r = make_panel(stub_client(down)).get("/webhook")
    assert "No se pudo conectar con el motor" in r.text and r.status_code == 502


def test_the_sidebar_offers_webhook_to_managers_only():
    engine = new_engine()
    assert 'href="/webhook"' in panel_of(engine).get("/claves").text
    viewer = member_panel(engine, org_a(engine), "viewer")
    assert 'href="/webhook"' not in viewer.get("/miembros").text
