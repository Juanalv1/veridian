"""The keys page says whether the project is wired: "Esperando el primer evento" until its first access arrives,
then "Conectado". Read from GET /v1/activity, which the panel already uses: no new engine field."""

import httpx
from fastapi.testclient import TestClient

from tests.panel_support import google_transport, make_panel
from tests.test_panel_keys import create
from tests.test_panel_signup import link, new_engine, panel_of, text

EVENT = {"id": "e_1", "ts": "2026-10-25T10:00:00Z", "user_id": "u_1", "session_id": "s_1", "ip": "190.202.14.7",
         "method": "GET", "route": "/invoices", "status": 200, "bytes": 10}


def send_event(engine, key, event=EVENT):
    r = TestClient(engine).post("/v1/events", json={"events": [event]}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 202, r.text


def signed_up(engine=None):
    engine = engine or new_engine()
    panel = panel_of(engine)
    panel.post("/sin-proyectos", data={"name": "Acme"})
    return engine, panel


def test_a_project_without_events_waits_for_the_first_one_and_keeps_polling():
    _, panel = signed_up()
    html = panel.get("/claves").text
    assert "Esperando el primer evento" in text(html) and "tm-conn waiting" in html and "tm-conn connected" not in html
    assert 'hx-get="/claves/conexion"' in html and "every" in html


def test_the_state_is_also_shown_with_a_freshly_created_key_and_its_snippet():
    _, panel = signed_up()
    made, key = create(panel)
    assert f"VERIDIAN_API_KEY={key}" in made.text and "Esperando el primer evento" in text(made.text)
    assert made.headers["cache-control"] == "no-store"


def test_the_first_access_turns_the_state_into_connected_and_stops_the_polling():
    engine, panel = signed_up()
    _, key = create(panel)
    waiting = panel.get("/claves/conexion")
    assert waiting.status_code == 200 and "Esperando el primer evento" in text(waiting.text)
    send_event(engine, key)
    done = panel.get("/claves/conexion")
    assert "tm-conn connected" in done.text and "hx-get" not in done.text and "Esperando" not in text(done.text)
    assert "tm-conn connected" in panel.get("/claves").text


def test_the_fragment_never_carries_a_key_and_is_not_cached():
    engine, panel = signed_up()
    _, key = create(panel)
    r = panel.get("/claves/conexion")
    assert key not in r.text and r.headers["cache-control"] == "no-store"


def test_another_projects_events_do_not_connect_this_one():
    engine, panel = signed_up()
    send_event(engine, "tok-a")  # the seeded demo project receives an access, not the new one
    assert "Esperando el primer evento" in text(panel.get("/claves/conexion").text)


class FlakyActivity(httpx.AsyncBaseTransport):
    """The real fake engine, except that reading the activity fails."""

    def __init__(self, engine, status=None):
        self.inner, self.status = httpx.ASGITransport(app=engine), status

    async def handle_async_request(self, request):
        if request.url.path == "/v1/activity":
            if self.status:
                return httpx.Response(self.status, json={"error": "x"})
            raise httpx.ConnectError("down")
        return await self.inner.handle_async_request(request)


def test_if_the_engine_cannot_answer_the_state_says_so_instead_of_claiming_anything():
    for status in (None, 500):
        engine = new_engine()
        client = httpx.AsyncClient(transport=FlakyActivity(engine, status), base_url="http://engine")
        panel = make_panel(client, transport=google_transport())
        panel.post("/sin-proyectos", data={"name": "Acme"})
        for html in (panel.get("/claves").text, panel.get("/claves/conexion").text):
            t = text(html)
            assert "No se pudo comprobar la conexión" in t
            assert "Conectado" not in t and "Esperando el primer evento" not in t


def test_the_fragment_needs_a_session():
    _, panel = signed_up()
    anonymous = panel_of(new_engine(), login=False)
    assert anonymous.get("/claves/conexion").status_code == 303
