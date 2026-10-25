import httpx
from fastapi.testclient import TestClient

from fake_engine import create_app as create_engine
from panel.main import create_app

TOKEN = "tok-panel"


def engine_client(engine):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=engine), base_url="http://engine")


def event(**over):
    base = {"id": "e_1", "ts": "2026-10-26T10:14:03Z", "user_id": "u_42", "session_id": "s_x",
            "ip": "203.0.113.9", "user_agent": "t", "device_id": "d_1", "method": "POST",
            "route": "/payouts/bank-account", "status": 200, "bytes": 1}
    base.update(over)
    return base


def test_panel_lists_block_decision_with_rationale():
    engine = create_engine(tokens={TOKEN}, seed=[{"user_id": "u_42", "ips": ["190.202.14.7"]}], learn_delay=0)
    c = TestClient(engine)
    c.post("/v1/events", json={"events": [event()]}, headers={"Authorization": f"Bearer {TOKEN}"})
    panel = TestClient(create_app("http://engine", TOKEN, http_client=engine_client(engine)))
    r = panel.get("/")
    assert r.status_code == 200
    assert "block" in r.text and "u_42" in r.text
    assert "Red nunca vista" in r.text
    assert "/payouts/bank-account" in r.text
    assert 'http-equiv="refresh"' in r.text


def test_panel_empty_state():
    engine = create_engine(tokens={TOKEN}, seed=[])
    panel = TestClient(create_app("http://engine", TOKEN, http_client=engine_client(engine)))
    r = panel.get("/")
    assert r.status_code == 200 and "Sin decisiones" in r.text


def test_panel_engine_down_shows_friendly_message():
    def boom(request):
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    r = TestClient(create_app("http://engine", TOKEN, http_client=client)).get("/")
    assert r.status_code == 200
    assert "No se pudo conectar" in r.text and 'http-equiv="refresh"' in r.text


def test_panel_engine_rejects_token_shows_message():
    engine = create_engine(tokens={"other"})
    r = TestClient(create_app("http://engine", TOKEN, http_client=engine_client(engine))).get("/")
    assert r.status_code == 200 and "token" in r.text.lower()


def test_panel_escapes_untrusted_text():
    def handler(request):
        return httpx.Response(200, json={"decisions": [{
            "id": "d_1", "ts": "2026-10-26T10:14:05Z", "user_id": "<script>alert(1)</script>",
            "decision": "block", "signals": {"route": "/x"}, "rationale": "<b>x</b>", "event_ids": []}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = TestClient(create_app("http://engine", TOKEN, http_client=client)).get("/")
    assert "<script>alert(1)</script>" not in r.text
