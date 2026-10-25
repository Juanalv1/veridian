import re
import httpx
from fastapi.testclient import TestClient

from fake_engine import create_app as create_engine
from panel.main import create_app

TOKEN = "tok-panel"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SEED = [{"user_id": "u_42", "ips": ["190.202.14.7"], "routes": ["GET /invoices"]}]


def engine_client(engine):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=engine), base_url="http://engine")


def event(**over):
    base = {"id": "e_1", "ts": "2026-10-26T10:14:03Z", "user_id": "u_42", "session_id": "s_x",
            "ip": "203.0.113.9", "user_agent": "t", "device_id": "d_1", "method": "POST",
            "route": "/payouts/bank-account", "status": 200, "bytes": 1}
    base.update(over)
    return base


def blocked_setup(seed=SEED):
    engine = create_engine(tokens={TOKEN}, seed=seed, learn_delay=0)
    TestClient(engine).post("/v1/events", json={"events": [event()]}, headers=AUTH)
    panel = TestClient(create_app("http://engine", TOKEN, http_client=engine_client(engine)),
                       follow_redirects=False)
    return engine, panel


def decision_id(engine):
    return TestClient(engine).get("/v1/decisions", headers=AUTH).json()["decisions"][0]["id"]


def stub_panel(decisions, profile=None):
    """Panel over a canned engine: `profile` None means 404 (no profile yet)."""
    def handler(request):
        if request.url.path == "/v1/decisions":
            return httpx.Response(200, json={"decisions": decisions})
        if profile is not None and request.url.path.endswith("/profile"):
            return httpx.Response(200, json=profile)
        return httpx.Response(404, json={"error": "not_found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TestClient(create_app("http://engine", TOKEN, http_client=client), follow_redirects=False)


def dec(id="d_1", user="u_1", decision="block", status="open", ts="2026-10-26T10:14:05Z", **over):
    base = {"id": id, "ts": ts, "user_id": user, "decision": decision, "status": status,
            "signals": {}, "rationale": "r", "event_ids": []}
    base.update(over)
    return base


def test_panel_shows_verdict_rationale_and_en_vivo():
    _, panel = blocked_setup()
    r = panel.get("/")
    assert r.status_code == 200
    assert "<h1" in r.text and "Bloqueado" in r.text and "u_42" in r.text
    assert "Red nunca vista" in r.text
    assert "POST /payouts/bank-account" in r.text
    assert "En vivo" in r.text
    assert "Silkscreen" not in r.text
    assert r.text.count('http-equiv="refresh"') == 1


def test_comparison_table_profile_vs_access_with_flags():
    _, panel = blocked_setup()
    r = panel.get("/")
    assert "Lo normal para u_42" in r.text and "Este acceso" in r.text
    assert 'scope="col"' in r.text and 'scope="row"' in r.text
    assert "190.202.14.0/24" in r.text and "203.0.113.9" in r.text
    assert re.search(r"203\.0\.113\.9\s*<span[^>]*>nueva</span>", r.text)
    assert re.search(r"d_1\s*<span[^>]*>nuevo</span>", r.text)
    assert "GET /invoices" in r.text
    assert "crítico, nunca lo hizo" in r.text
    assert "Horario" in r.text


def test_missing_access_renders_dashes_and_falls_back_to_signals_route_in_nav():
    panel = stub_panel([dec(signals={"route": "/x"})], profile=None)
    r = panel.get("/")
    assert r.status_code == 200
    assert "—" in r.text
    assert "u_1 · /x" in r.text
    assert "nueva</span>" not in r.text


def test_recorrido_row_only_with_path():
    with_path = stub_panel([dec(signals={"path": ["/login", "/payouts/bank-account"]})]).get("/")
    assert "Recorrido" in with_path.text and "/login → /payouts/bank-account" in with_path.text
    assert "Recorrido" not in stub_panel([dec()]).get("/").text


def test_open_block_renders_mark_normal_button_and_consequence():
    engine, panel = blocked_setup()
    r = panel.get("/")
    assert "Esto era normal" in r.text
    assert f'action="/decisions/{decision_id(engine)}/normal"' in r.text
    assert 'method="post"' in r.text
    assert "La sesión y la IP 203.0.113.9 no pueden entrar. El dueño sigue trabajando." in r.text


def test_open_non_block_says_it_will_join_the_profile():
    r = stub_panel([dec(decision="alert")]).get("/")
    assert "Esto era normal" in r.text and "Veridian lo sumará al perfil de u_1." in r.text
    assert "no pueden entrar" not in r.text


def test_block_without_access_ip_omits_the_ip_part():
    r = stub_panel([dec()]).get("/")
    assert "La sesión no puede entrar. El dueño sigue trabajando." in r.text
    assert "la IP" not in r.text


def test_open_block_renders_mark_normal_button():
    engine, panel = blocked_setup()
    r = panel.get("/")
    assert "Esto era normal" in r.text
    assert f'action="/decisions/{decision_id(engine)}/normal"' in r.text
    assert 'method="post"' in r.text


def test_marked_normal_block_shows_lifted_muted_line_and_no_button():
    engine, panel = blocked_setup()
    TestClient(engine).post(f"/v1/decisions/{decision_id(engine)}/normal", headers=AUTH)
    r = panel.get("/")
    assert "Bloqueo levantado" in r.text and "Levantado" in r.text
    assert "Marcado como normal. Veridian lo suma al perfil de u_42." in r.text
    assert "Esto era normal" not in r.text
    assert "<button" not in r.text


def test_post_mark_normal_calls_engine_and_redirects():
    engine, panel = blocked_setup()
    did = decision_id(engine)
    r = panel.post(f"/decisions/{did}/normal", data={"user": "u_42"})
    assert r.status_code == 303
    assert r.headers["location"] == "/users/u_42"
    status = TestClient(engine).get("/v1/decisions", headers=AUTH).json()["decisions"][0]["status"]
    assert status == "marked_normal"


def test_post_mark_normal_engine_error_redirects_with_message_not_500():
    _, panel = blocked_setup()
    r = panel.post("/decisions/d_nope/normal", data={"user": "u_42"})
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    page = panel.get(r.headers["location"])
    assert page.status_code == 200 and "No se pudo marcar" in page.text


def test_post_mark_normal_engine_down_redirects():
    def boom(request):
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    panel = TestClient(create_app("http://engine", TOKEN, http_client=client), follow_redirects=False)
    r = panel.post("/decisions/d_1/normal", data={})
    assert r.status_code == 303 and "error=" in r.headers["location"]


def test_unknown_status_counts_as_open():
    def handler(request):
        if request.url.path == "/v1/decisions":
            return httpx.Response(200, json={"decisions": [{
                "id": "d_1", "ts": "2026-10-26T10:14:05Z", "user_id": "u_1", "decision": "block",
                "signals": {}, "rationale": "x", "event_ids": [], "status": "expired"}]})
        return httpx.Response(404, json={"error": "not_found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = TestClient(create_app("http://engine", TOKEN, http_client=client)).get("/")
    assert "Esto era normal" in r.text and "Bloqueo levantado" not in r.text


def test_history_days_pluralized_and_hidden_when_absent():
    base = {"user_id": "u_1", "networks": [], "devices": [], "hours": [], "routes": [], "state": "normal"}
    one = stub_panel([dec()], {**base, "history_days": 1}).get("/").text
    assert "1 día de historial" in one and "1 días" not in one
    many = stub_panel([dec()], {**base, "history_days": 14}).get("/").text
    assert "14 días de historial" in many
    assert "historial" not in stub_panel([dec()], base).get("/").text


def test_selecting_a_decision_by_id():
    ds = [dec(id="d_2", decision="allow", ts="2026-10-26T10:20:00Z", signals={"route": "/b"}, rationale="newest one"),
          dec(id="d_1", decision="alert", ts="2026-10-26T10:10:00Z", signals={"route": "/a"}, rationale="older one")]
    panel = stub_panel(ds)
    default = panel.get("/").text
    assert "newest one" in default and "older one" not in default
    assert 'href="/users/u_1/decisions/d_1"' in default and 'href="/users/u_1/decisions/d_2"' in default
    picked = panel.get("/users/u_1/decisions/d_1")
    assert picked.status_code == 200
    assert "older one" in picked.text and "newest one" not in picked.text
    assert re.search(r'href="/users/u_1/decisions/d_1"[^>]*aria-current="true"', picked.text)
    assert "newest one" in panel.get("/users/u_1").text


def test_decision_url_escapes_ids_and_user_ids():
    ds = [dec(id="d/1 x", user="acme/ana torres")]
    panel = stub_panel(ds)
    assert 'href="/users/acme%2Fana%20torres/decisions/d%2F1%20x"' in panel.get("/").text
    r = panel.get("/users/acme%2Fana%20torres/decisions/d%2F1%20x")
    assert r.status_code == 200 and 'aria-current="true"' in r.text


def test_unknown_decision_id_falls_back_to_the_users_newest():
    panel = stub_panel([dec(rationale="the only one")])
    r = panel.get("/users/u_1/decisions/d_nope")
    assert r.status_code == 200 and "the only one" in r.text


def test_nav_verdict_words():
    ds = [dec(id="d_1", decision="block"), dec(id="d_2", decision="alert"),
          dec(id="d_3", decision="allow"), dec(id="d_4", decision="step_up"),
          dec(id="d_5", decision="block", status="marked_normal")]
    nav = stub_panel(ds).get("/").text.split("</nav>")[0]
    for word in ("Bloqueado", "Alerta", "Permitido", "Segundo factor", "Levantado"):
        assert word in nav


def test_user_param_selects_profile():
    engine = create_engine(tokens={TOKEN}, seed=[
        {"user_id": "u_42", "ips": ["190.202.14.7"], "routes": ["GET /invoices"]},
        {"user_id": "u_17", "ips": ["10.1.2.3"], "routes": ["GET /ledger"]}], learn_delay=0)
    c = TestClient(engine)
    c.post("/v1/events", json={"events": [event()]}, headers=AUTH)
    c.post("/v1/events", json={"events": [event(id="e_2", user_id="u_17", ip="172.16.5.4")]}, headers=AUTH)
    panel = TestClient(create_app("http://engine", TOKEN, http_client=engine_client(engine)))
    r = panel.get("/users/u_17")
    assert "Lo normal para u_17" in r.text
    assert "10.1.2.0/24" in r.text and "GET /ledger" in r.text and "190.202.14.0/24" not in r.text


def test_profile_hours_range_formatted():
    def handler(request):
        if request.url.path == "/v1/decisions":
            return httpx.Response(200, json={"decisions": [{
                "id": "d_1", "ts": "2026-10-26T10:14:05Z", "user_id": "u_1", "decision": "allow",
                "signals": {"route": "/x"}, "rationale": "", "event_ids": [], "status": "open"}]})
        return httpx.Response(200, json={"user_id": "u_1", "history_days": 3, "networks": [],
                                         "devices": [], "hours": [9, 13], "routes": [], "state": "normal"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = TestClient(create_app("http://engine", TOKEN, http_client=client)).get("/")
    assert "09:00 – 13:00 UTC" in r.text and "Permitido" in r.text
    # the browser rewrites UTC hours to the viewer's local time
    assert 'data-utc-hours="9,13"' in r.text


def test_profile_missing_shows_placeholder():
    def handler(request):
        if request.url.path == "/v1/decisions":
            return httpx.Response(200, json={"decisions": [{
                "id": "d_1", "ts": "2026-10-26T10:14:05Z", "user_id": "u_1", "decision": "alert",
                "signals": {}, "rationale": "r", "event_ids": [], "status": "open"}]})
        return httpx.Response(404, json={"error": "not_found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = TestClient(create_app("http://engine", TOKEN, http_client=client)).get("/")
    assert r.status_code == 200 and "Alerta" in r.text and "Lo normal para u_1" in r.text


def test_step_up_badge():
    def handler(request):
        return httpx.Response(200, json={"decisions": [{
            "id": "d_1", "ts": "2026-10-26T10:14:05Z", "user_id": "u_1", "decision": "step_up",
            "signals": {}, "rationale": "r", "event_ids": []}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = TestClient(create_app("http://engine", TOKEN, http_client=client)).get("/")
    assert "Segundo factor" in r.text


def test_panel_empty_state():
    engine = create_engine(tokens={TOKEN}, seed=[])
    panel = TestClient(create_app("http://engine", TOKEN, http_client=engine_client(engine)))
    r = panel.get("/")
    assert r.status_code == 200 and "Sin decisiones" in r.text and "En vivo" in r.text


def test_panel_engine_down_shows_friendly_message():
    def boom(request):
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    r = TestClient(create_app("http://engine", TOKEN, http_client=client)).get("/")
    assert r.status_code == 200
    assert "No se pudo conectar" in r.text and "Motor sin conexión" in r.text
    assert 'http-equiv="refresh"' in r.text


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
    assert "<b>x</b>" not in r.text


def test_user_url_escapes_ids_with_slashes_and_spaces():
    def handler(request):
        if request.url.path == "/v1/decisions":
            return httpx.Response(200, json={"decisions": [{
                "id": "d_1", "ts": "2026-10-26T10:14:05Z", "user_id": "acme/ana torres", "decision": "block",
                "signals": {}, "rationale": "x", "event_ids": [], "status": "open"}]})
        return httpx.Response(404, json={"error": "not_found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    panel = TestClient(create_app("http://engine", TOKEN, http_client=client))
    r = panel.get("/users/acme%2Fana%20torres")
    assert r.status_code == 200 and 'aria-current="true"' in r.text


def test_decision_time_is_machine_readable_for_local_conversion():
    _, panel = blocked_setup()
    r = panel.get("/")
    assert re.search(r'<time class="ts"[^>]*datetime="\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"[^>]*>\d{2}:\d{2}:\d{2} UTC</time>', r.text)


def test_live_refresh_swaps_data_and_keeps_meta_refresh_only_without_js():
    _, panel = blocked_setup()
    r = panel.get("/")
    assert '<noscript><meta http-equiv="refresh"' in r.text
    assert r.text.count('http-equiv="refresh"') == 1
    assert 'id="live-refresh"' in r.text
