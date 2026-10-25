import pytest
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


def stub_panel(decisions, profile=None, activity=None, blocklist=None):
    """Panel over a canned engine: `profile` None means 404 (no profile yet);
    `activity` None means the engine has no /v1/activity (404); `blocklist` None
    means /v1/blocklist fails (404)."""
    def handler(request):
        if request.url.path == "/v1/blocklist" and blocklist is not None:
            return httpx.Response(200, json={"version": 1, "users": [], **blocklist})
        if request.url.path == "/v1/activity" and activity is not None:
            return httpx.Response(200, json={"activity": activity})
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
    r = stub_panel([dec()], blocklist={"sessions": ["s_1"], "ips": []}).get("/")
    assert "La sesión no puede seguir." in r.text
    assert "la IP" not in r.text and "La IP es la del dueño" not in r.text


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
    # (the decision list's short "Levantado" word is covered by the no-activity fallback test below)
    assert "Bloqueo levantado" in r.text
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
    assert r.status_code == 200 and "Todavía no hay accesos" in r.text and "En vivo" in r.text


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
    assert 'id="live-refresh"' not in r.text


def test_live_region_polls_with_htmx_and_swaps_region_and_status():
    _, panel = blocked_setup()
    r = panel.get("/")
    live = re.search(r'<main id="live"[^>]*>', r.text, re.S).group(0)
    did = re.search(r'hx-get="([^"]+)"', live).group(1)
    assert did.startswith("/users/u_42/decisions/")
    assert 'hx-trigger="every 2s [refreshAllowed()]"' in live
    assert 'hx-select="#live"' in live and 'hx-select-oob="#conn"' in live
    assert 'hx-swap="outerHTML' in live
    assert 'id="conn"' in r.text and 'role="status"' in r.text
    # the polled URL is the canonical page
    assert panel.get(did).status_code == 200


def test_htmx_is_vendored_and_loaded_from_static_without_cdn():
    _, panel = blocked_setup()
    js = panel.get("/static/htmx.min.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert 'version:"2.0.4"' in js.text
    page = panel.get("/").text
    assert 'src="/static/htmx.min.js?v=' in page
    assert "unpkg" not in page and "cdn" not in page.lower().replace("fonts.googleapis", "")
    assert page.index("htmx.min.js") < page.index("panel.js")


def test_nav_links_are_boosted_into_the_live_region():
    _, panel = blocked_setup()
    nav = re.search(r"<nav[^>]*>", panel.get("/").text).group(0)
    assert 'hx-boost="true"' in nav and 'hx-target="#live"' in nav


def test_htmx_post_mark_normal_returns_updated_page_not_redirect():
    engine, panel = blocked_setup()
    did = decision_id(engine)
    r = panel.post(f"/decisions/{did}/normal", data={"user": "u_42"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Bloqueo levantado" in r.text and "Esto era normal" not in r.text
    assert 'id="live"' in r.text and 'id="conn"' in r.text
    status = TestClient(engine).get("/v1/decisions", headers=AUTH).json()["decisions"][0]["status"]
    assert status == "marked_normal"


def test_htmx_post_mark_normal_failure_shows_same_error_in_place():
    _, panel = blocked_setup()
    r = panel.post("/decisions/d_nope/normal", data={"user": "u_42"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "No se pudo marcar" in r.text and 'id="live"' in r.text


def test_mark_normal_form_keeps_real_post_and_adds_hx_post():
    engine, panel = blocked_setup()
    page = panel.get("/").text
    action = f"/decisions/{decision_id(engine)}/normal"
    assert f'action="{action}"' in page and 'method="post"' in page
    assert f'hx-post="{action}"' in page


def test_panel_serves_its_stylesheet_and_script():
    _, panel = blocked_setup()
    css = panel.get("/static/panel.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert panel.get("/static/panel.js").status_code == 200
    page = panel.get("/").text
    assert '<link rel="stylesheet" href="/static/panel.css?v=' in page
    assert 'src="/static/panel.js?v=' in page


def test_panel_loads_tokens_before_panel_css_and_design_fonts():
    _, panel = blocked_setup()
    page = panel.get("/").text
    assert '/static/tokens.css?v=' in page
    assert page.index("tokens.css") < page.index("panel.css")
    assert "family=Archivo" in page and "Urbanist" not in page and "JetBrains" not in page
    assert panel.get("/static/tokens.css").status_code == 200


def test_mark_normal_is_the_primary_button_and_disables_itself_in_flight():
    engine, panel = blocked_setup()
    page = panel.get("/").text
    assert 'class="btn btn-primary"' in page
    assert 'hx-disabled-elt="this"' in page
    css = panel.get("/static/panel.css").text
    for old in ("#111010", "#49d08b", "#ff4d4d", "Urbanist", "JetBrains"):
        assert old not in css


def _access(ts):
    return {"ip": "203.0.113.9", "ts": ts, "method": "POST", "route": "/x"}


@pytest.mark.parametrize("access_ts,expected", [
    ("2026-10-26T10:14:02Z", "Detectado y bloqueado en 3 s"),
    ("2026-10-26T10:14:04.2Z", "Detectado y bloqueado en 0,8 s"),
    ("2026-10-26T10:13:00Z", "Detectado y bloqueado en 1 min 5 s"),
    ("2026-10-26T10:13:55Z", "Detectado y bloqueado en 10 s"),
])
def test_detection_time_is_shown_with_spanish_format(access_ts, expected):
    r = stub_panel([dec(access=_access(access_ts))]).get("/")
    assert expected in r.text


def test_detection_time_wording_for_non_block():
    r = stub_panel([dec(decision="alert", access=_access("2026-10-26T10:14:02Z"))]).get("/")
    assert "Detectado en 3 s" in r.text and "bloqueado en" not in r.text


@pytest.mark.parametrize("access", [None, {"ip": "1.1.1.1"}, _access("2026-10-26T10:14:09Z"), _access("garbage")])
def test_detection_time_hidden_when_missing_negative_or_invalid(access):
    r = stub_panel([dec(access=access) if access is not None else dec()]).get("/")
    assert "Detectado" not in r.text


def test_detection_time_is_muted_evidence_not_the_accent():
    r = stub_panel([dec(access=_access("2026-10-26T10:14:02Z"))]).get("/")
    assert 'class="detected"' in r.text



# --- live activity feed (contract 5) ---------------------------------------


def act(event_id="e_1", user="u_1", method="GET", route="/invoices", verdict="normal",
        decision_id=None, ts="2026-10-26T10:14:03Z", **extra):
    return {"event_id": event_id, "ts": ts, "user_id": user, "session_id": "s_1", "ip": "1.2.3.4",
            "device_id": "d_1", "method": method, "route": route, "verdict": verdict,
            "decision_id": decision_id, **extra}


def feed(html):
    """The activity nav, as text."""
    m = re.search(r'<nav[^>]*aria-label="Actividad".*?</nav>', html, re.S)
    assert m, "no Actividad feed"
    return m.group(0)


def rows(html):
    return re.findall(r'<(?:a|div)[^>]*class="[^"]*\brow\b[^"]*"[^>]*>.*?</(?:a|div)>', feed(html), re.S)


def test_feed_rows_show_time_verdict_word_user_and_action():
    acts = [act("e_3", verdict="block", method="POST", route="/payouts/bank-account", decision_id="d_1"),
            act("e_2", verdict="review", decision_id="d_2"), act("e_1")]
    decisions = [dec(id="d_1", event_ids=["e_3"]), dec(id="d_2", decision="alert", event_ids=["e_2"])]
    html = stub_panel(decisions, activity=acts).get("/").text
    r = rows(html)
    assert len(r) == 3
    assert "Bloqueado" in r[0] and "u_1" in r[0] and "POST /payouts/bank-account" in r[0]
    assert "Raro" in r[1]
    assert "Normal" in r[2] and "GET /invoices" in r[2]
    for row in r:
        assert re.search(r'<time class="ts" datetime="2026-10-26T10:14:03Z">10:14:03 UTC</time>', row)
    assert 'class="v block"' in r[0] and 'class="v alert"' in r[1] and 'class="v normal"' in r[2]


def test_feed_replaces_the_decisions_nav_when_the_engine_has_activity():
    html = stub_panel([dec(event_ids=["e_1"])], activity=[act(verdict="block", decision_id="d_1")]).get("/").text
    assert "Decisiones recientes" not in html
    assert 'aria-label="Actividad"' in html and "Actividad</span>" in html


def test_rows_with_a_decision_link_to_its_detail_and_others_do_not():
    acts = [act("e_2", user="u 1", verdict="block", decision_id="d_1"), act("e_1")]
    html = stub_panel([dec(user="u 1", event_ids=["e_2"])], activity=acts).get("/").text
    r = rows(html)
    assert r[0].startswith("<a") and 'href="/users/u%201/decisions/d_1"' in r[0]
    assert r[1].startswith("<div") and "href" not in r[1]
    assert 'hx-boost="true"' in re.search(r"<nav[^>]*>", feed(html)).group(0)


def test_rows_back_to_normal_after_marking_are_quiet_and_not_rejected():
    acts = [act("e_r", verdict="normal", decision_id="d_1"), act("e_t", verdict="normal", decision_id="d_1")]
    html = stub_panel([dec(status="marked_normal", event_ids=["e_t"])], activity=acts).get("/").text
    assert all("detenido" not in r and "pasó" not in r and "Normal" in r for r in rows(html))


def test_unknown_verdict_is_shown_as_review_not_hidden_as_normal():
    r = rows(stub_panel([], activity=[act(verdict="quarantine")]).get("/").text)
    assert "Raro" in r[0]


def test_empty_state_without_decisions_uses_real_counts():
    acts = [act("e_3", user="u_1"), act("e_2", user="u_2"), act("e_1", user="u_1")]
    html = stub_panel([], activity=acts).get("/").text
    assert "Todo normal. Veridian está vigilando 3 accesos de 2 usuarios." in html
    assert "Sin decisiones" not in html and "<h1" not in html


def test_empty_state_singular_and_window_wording():
    html = stub_panel([], activity=[act()]).get("/").text
    assert "Todo normal. Veridian está vigilando 1 acceso de 1 usuario." in html
    full = [act(f"e_{n}") for n in range(50)]
    html = stub_panel([], activity=full).get("/").text
    assert "Todo normal. Veridian está vigilando los últimos 50 accesos de 1 usuario." in html


def test_empty_state_with_no_activity_yet():
    html = stub_panel([], activity=[]).get("/").text
    assert "Todavía no hay accesos" in html and "<h1" not in html


def test_decision_stays_the_hero_next_to_the_feed():
    acts = [act("e_t", verdict="block", decision_id="d_1")]
    html = stub_panel([dec(event_ids=["e_t"])], activity=acts).get("/").text
    assert "<h1" in html and "Bloqueado" in html and "Todo normal" not in html
    assert 'aria-current="true"' in rows(html)[0]


def test_engine_without_activity_degrades_to_the_decisions_list_without_error():
    html = stub_panel([dec()], activity=None).get("/").text
    assert "Decisiones recientes" in html and 'aria-label="Actividad"' not in html
    assert "note warn" not in html and "En vivo" in html


def test_engine_without_activity_and_without_decisions_keeps_old_empty_state():
    r = stub_panel([], activity=None).get("/")
    assert "Sin decisiones por ahora. Todo normal." in r.text


def test_mark_normal_is_hidden_for_allow_decisions():
    html = stub_panel([dec(decision="allow")]).get("/").text
    assert "Esto era normal" not in html and "<h1" in html and "Permitido" in html


def test_end_to_end_owner_then_attacker_then_rejections_then_mark_normal():
    engine = create_engine(tokens={TOKEN}, seed=SEED, learn_delay=0)
    api = TestClient(engine)
    panel = TestClient(create_app("http://engine", TOKEN, http_client=engine_client(engine)), follow_redirects=False)
    api.post("/v1/events", json={"events": [event(id="e_own", ip="190.202.14.7", session_id="s_own",
                                                  method="GET", route="/invoices")]}, headers=AUTH)
    html = panel.get("/").text
    assert "Todo normal. Veridian está vigilando 1 acceso de 1 usuario." in html and "<h1" not in html
    api.post("/v1/events", json={"events": [event(id="e_atk")]}, headers=AUTH)
    api.post("/v1/events", json={"events": [event(id="e_r1", method="GET", route="/invoices", status=403, bytes=21),
                                            event(id="e_r2", method="GET", route="/invoices", status=403, bytes=21)]}, headers=AUTH)
    html = panel.get("/").text
    r = rows(html)
    assert [("Bloqueado" in x, "detenido (403)" in x, "pasó antes del bloqueo (200)" in x) for x in r] == [
        (True, True, False), (True, True, False), (True, False, True), (False, False, False)]
    assert "Normal" in r[3]
    did = decision_id(engine)
    panel.post(f"/decisions/{did}/normal", data={"user": "u_42"})
    r = rows(panel.get("/").text)
    assert all("Normal" in x and "Bloqueado" not in x and "detenido" not in x for x in r)


def test_lifted_block_is_listed_as_levantado_when_the_engine_has_no_activity():
    html = stub_panel([dec(status="marked_normal")], activity=None).get("/").text
    assert "Levantado" in html and "Bloqueo levantado" in html


def test_feed_css_is_one_flat_list_distinguished_only_by_the_word():
    css = TestClient(create_app("http://engine", TOKEN)).get("/static/panel.css").text
    feed_css = css.split(".feed {")[1].split("@media (max-width:960px)")[0]
    assert re.search(r"\.row \{[^}]*border-bottom:1px solid var\(--line\)", feed_css)
    assert "border-left" not in feed_css and "box-shadow" not in css
    assert not re.search(r"\.row\.k-\w+ \{[^}]*background", feed_css)  # no tint at rest
    assert re.search(r"\.row \.v \{[^}]*color:var\(--muted\)", feed_css)  # Normal is the quiet default
    assert re.search(r"\.row\.k-block \.v \{[^}]*var\(--state-block\)[^}]*font-weight:600", feed_css)
    assert re.search(r"\.row\.k-alert \.v \{[^}]*var\(--state-review\)", feed_css)
    assert re.search(r"a\.row\[aria-current\] \{[^}]*background:var\(--surface\)[^}]*border-radius", feed_css)
    assert re.search(r"a\.row:hover \{[^}]*var\(--surface-hover\)", feed_css)
    assert "--viridian" not in feed_css


# --- new rows enter smoothly -----------------------------------------------


def test_activity_rows_have_a_stable_key_from_the_event_id():
    html = stub_panel([], activity=[act("e_9"), act("e_8")]).get("/").text
    # data-row, not id: htmx copies old attributes onto same-id elements while settling
    assert 'data-row="ev-e_9"' in html and 'data-row="ev-e_8"' in html and 'id="ev-' not in html


def test_decision_list_fallback_rows_have_a_stable_key_from_the_decision_id():
    html = stub_panel([dec(id="d_7")], activity=None).get("/").text
    assert 'data-row="dc-d_7"' in html


def test_script_marks_only_unseen_rows_as_new_after_a_swap_never_on_first_load():
    js = TestClient(create_app("http://engine", TOKEN)).get("/static/panel.js").text
    assert "is-new" in js and "htmx:afterSwap" in js
    # first load only records the ids already on screen
    assert re.search(r"seen\s*=\s*new Set", js) and "markNew" in js


def test_css_animates_new_rows_with_height_fade_slide_and_respects_reduced_motion():
    css = TestClient(create_app("http://engine", TOKEN)).get("/static/panel.css").text
    assert "@keyframes row-in" in css and "@keyframes row-flash" in css
    assert re.search(r"\.row\.is-new,\s*\.item\.is-new \{[^}]*animation:[^;}]*row-in 220ms var\(--ease-out\)", css)
    assert re.search(r"max-height:\s*0", css) and "translateY(-6px)" in css
    assert re.search(r"\.k-block\.is-new \{[^}]*row-flash", css)
    reduced = css.split("prefers-reduced-motion: reduce")[1]
    assert "animation:none" in reduced.replace(" ", "")


# --- status suffix on block rows ------------------------------------------


def suffix_of(row):
    m = re.search(r'<span class="st">(.*?)</span>', row, re.S)
    return m.group(1).strip() if m else None


def test_block_rows_say_what_happened_to_the_request_using_the_real_status():
    acts = [act("e_4", verdict="block", decision_id="d_1", status=403),
            act("e_3", verdict="block", decision_id="d_1", status=302),
            act("e_2", verdict="block", decision_id="d_1", status=200),
            act("e_1", verdict="block", decision_id="d_1", status=404)]
    r = rows(stub_panel([dec()], activity=acts).get("/").text)
    assert [suffix_of(x) for x in r] == ["detenido (403)", "pasó antes del bloqueo (302)",
                                         "pasó antes del bloqueo (200)", "404"]
    assert all("Bloqueado" in x for x in r)


def test_block_row_without_status_has_no_suffix_and_normal_rows_none():
    acts = [act("e_2", verdict="block", decision_id="d_1"), act("e_1", status=200)]
    r = rows(stub_panel([dec()], activity=acts).get("/").text)
    assert suffix_of(r[0]) is None and suffix_of(r[1]) is None
    assert 'class="st"' not in r[0] and 'class="st"' not in r[1]


def test_status_suffix_ignores_non_numeric_status():
    r = rows(stub_panel([dec()], activity=[act(verdict="block", decision_id="d_1", status="ok")]).get("/").text)
    assert suffix_of(r[0]) is None


# --- consequence text follows the blocklist --------------------------------


def block_dec(ip="203.0.113.9", **over):
    return dec(access={"ip": ip, "session_id": "s_x"}, **over)


def test_consequence_names_the_ip_only_when_it_is_on_the_blocklist():
    html = stub_panel([block_dec()], blocklist={"sessions": ["s_x"], "ips": ["203.0.113.9"]}).get("/").text
    assert "La sesión y la IP 203.0.113.9 no pueden entrar. El dueño sigue trabajando." in html


def test_session_only_block_does_not_claim_the_ip_is_blocked():
    html = stub_panel([block_dec("190.202.14.7")], blocklist={"sessions": ["s_x"], "ips": ["203.0.113.9"]}).get("/").text
    assert "La sesión no puede seguir. La IP es la del dueño: no se bloquea." in html
    assert "no pueden entrar" not in html


def test_blocklist_failure_gives_a_generic_text_and_never_names_the_ip():
    html = stub_panel([block_dec()], blocklist=None).get("/").text
    assert "La sesión está bloqueada." in html
    assert "203.0.113.9 no pueden" not in html and "La IP es la del dueño" not in html


def test_blocklist_failure_does_not_show_an_engine_error():
    html = stub_panel([block_dec()], blocklist=None).get("/").text
    assert "note warn" not in html and "En vivo" in html


# --- ritmo y camino in the comparison table --------------------------------


def sig_panel(signals, access=None, profile=None):
    d = dec(signals=signals, access=access or {"ts": "2026-10-26T10:14:03Z", "ip": "1.2.3.4",
                                              "device_id": "d_1", "session_id": "s", "method": "GET", "route": "/x"})
    return stub_panel([d], profile=profile or {"user_id": "u_1", "networks": [], "devices": [], "hours": [9, 13],
                                               "routes": [], "history_days": 3, "usual_max_rate_per_min": 6}).get("/").text


def row_of(html, name):
    m = re.search(rf'<th scope="row">{name}</th>(.*?)</tr>', html, re.S)
    return m.group(1) if m else None


def test_rate_row_shows_usual_max_and_current_rate_with_pico():
    html = sig_panel({"rate_per_min": 40, "usual_max_rate_per_min": 6, "rate_spike": True})
    row = row_of(html, "Ritmo")
    assert "hasta 6 por minuto" in row and "40 por minuto" in row
    assert re.search(r'<span class="flag[^"]*">pico</span>', row)


def test_rate_row_without_spike_has_no_pico_and_hides_when_absent():
    html = sig_panel({"rate_per_min": 2, "rate_spike": False})
    assert "2 por minuto" in row_of(html, "Ritmo") and "pico" not in html
    assert row_of(sig_panel({}), "Ritmo") is None


def test_rate_row_without_usual_rate_shows_dash_on_the_normal_side():
    html = sig_panel({"rate_per_min": 5},
                     profile={"user_id": "u_1", "networks": [], "devices": [], "hours": [], "routes": [],
                              "usual_max_rate_per_min": 0})
    row = row_of(html, "Ritmo")
    assert "hasta" not in row and "—" in row and "5 por minuto" in row


def test_unusual_hour_adds_fuera_de_su_horario_to_the_schedule_row():
    assert "fuera de su horario" in row_of(sig_panel({"unusual_hour": True}), "Horario")
    assert "fuera de su horario" not in sig_panel({"unusual_hour": False})


def test_new_transition_adds_camino_nuevo_to_the_path_row():
    html = sig_panel({"path": ["/login", "/x"], "new_transition": True})
    assert "camino nuevo" in row_of(html, "Recorrido")
    html = sig_panel({"new_transition": True})  # no path: the row still says it
    assert "camino nuevo" in row_of(html, "Recorrido")
    assert row_of(sig_panel({"path": ["/a", "/b"]}), "Recorrido") and "camino nuevo" not in sig_panel({"path": ["/a"]})


def test_unknown_signal_keys_are_ignored():
    html = sig_panel({"future_signal": 3, "rate_per_min": "many", "rate_spike": "yes"})
    assert "future_signal" not in html and row_of(html, "Ritmo") is None and "pico" not in html


# --- only the triggering row is the selected one ---------------------------


def test_only_the_row_that_triggered_the_decision_is_selected():
    acts = [act("e_r", verdict="block", decision_id="d_1", status=403),
            act("e_t", verdict="block", decision_id="d_1", status=200)]
    html = stub_panel([dec(event_ids=["e_t"])], activity=acts).get("/").text
    r = rows(html)
    assert 'aria-current="true"' not in r[0] and 'aria-current="true"' in r[1]


def test_selection_falls_back_to_the_first_row_of_the_decision_without_event_ids():
    acts = [act("e_r", verdict="block", decision_id="d_1"), act("e_t", verdict="block", decision_id="d_1")]
    html = stub_panel([dec(event_ids=[])], activity=acts).get("/").text
    r = rows(html)
    assert 'aria-current="true"' in r[0] and 'aria-current="true"' not in r[1]
