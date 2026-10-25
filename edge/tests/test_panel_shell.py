"""Shell (sidebar), Resumen, Usuarios and Rutas pages of the panel."""

import re
from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

from panel.main import create_app

TOKEN = "tok"
NOW = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)  # 11:00 in Caracas (UTC-4)
CARACAS = {"vtz": "-240"}


def dec(id="d_1", user="u_1", decision="block", status="open", ts="2026-10-26T14:14:05Z", access_ts="2026-10-26T14:14:03Z", **over):
    base = {"id": id, "ts": ts, "user_id": user, "decision": decision, "status": status,
            "signals": {}, "rationale": "r", "event_ids": ["e_1"],
            "access": {"ts": access_ts, "ip": "203.0.113.9", "method": "POST", "route": "/payouts/bank-account"}}
    base.update(over)
    return base


def act(event_id="e_1", ts="2026-10-26T14:14:03Z", user="u_1", verdict="block", decision_id="d_1", **over):
    base = {"event_id": event_id, "ts": ts, "user_id": user, "session_id": "s", "ip": "1.1.1.1", "device_id": "d",
            "method": "GET", "route": "/invoices", "verdict": verdict, "status": 200, "decision_id": decision_id}
    base.update(over)
    return base


def summary(**over):
    base = {"since": "2026-10-26T04:00:00Z", "until": "2026-10-27T04:00:00Z", "accesses": 137, "users": 9,
            "reviewed_by_ai": 5, "decisions": {"allow": 0, "alert": 0, "step_up": 0, "block": 1},
            "blocks_active": {"decisions": 1, "sessions": 3, "ips": 1},
            "by_hour": [{"hour": "2026-10-26T13:00:00Z", "accesses": 3, "blocks": 0},
                        {"hour": "2026-10-26T14:00:00Z", "accesses": 11, "blocks": 1}]}
    base.update(over)
    return base


USERS = {"users": [
    {"user_id": "u_1", "state": "blocked", "history_days": 14, "networks": 1, "devices": 1,
     "last_seen": "2026-10-26T14:14:03Z", "open_blocks": 1},
    {"user_id": "u_17", "state": "normal", "history_days": 9, "networks": 2, "devices": 1,
     "last_seen": "2026-10-26T13:40:10Z", "open_blocks": 0}]}


class Engine:
    """Canned engine. None for summary/users/activity means the endpoint answers 404."""

    def __init__(self, decisions=None, activity=None, summary_body=None, users=None, blocklist=None, down=False):
        self.decisions = [dec()] if decisions is None else decisions
        self.activity = [act()] if activity is None else activity
        self.summary_body, self.users, self.down = summary_body, users, down
        self.blocklist = {"version": 1, "users": [], "sessions": ["s"], "ips": ["203.0.113.9"]} if blocklist is None else blocklist
        self.requests = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("down")
        path = request.url.path
        if path == "/v1/decisions":
            return httpx.Response(200, json={"decisions": self.decisions})
        if path == "/v1/activity" and self.activity is not None:
            return httpx.Response(200, json={"activity": self.activity})
        if path == "/v1/blocklist":
            return httpx.Response(200, json=self.blocklist)
        if path == "/v1/summary" and self.summary_body is not None:
            return httpx.Response(200, json=self.summary_body)
        if path == "/v1/users" and self.users is not None:
            return httpx.Response(200, json=self.users)
        if path.endswith("/profile"):
            return httpx.Response(200, json={"networks": [], "devices": [], "routes": [], "hours": [], "history_days": 14})
        return httpx.Response(404, json={"error": "not_found"})

    def panel(self, cookies=CARACAS):
        app = create_app("http://engine", TOKEN, http_client=self.client, now=lambda: NOW)
        return TestClient(app, follow_redirects=False, cookies=cookies)

    def summary_requests(self):
        return [r for r in self.requests if r.url.path == "/v1/summary"]


def full(**kw):
    return Engine(summary_body=summary(), users=USERS, **kw)


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def tile(html, label):
    m = re.search(r'<div class="kpi[^"]*"[^>]*>\s*<span class="kpi-label">' + re.escape(label) + r'</span>(.*?)</div>', html, re.S)
    return text(m.group(1)).strip() if m else None


# -- shell -----------------------------------------------------------------------------------------

def test_sidebar_has_four_sections_and_marks_the_current_one():
    html = full().panel().get("/").text
    nav = re.search(r'<nav[^>]*aria-label="Secciones"[^>]*>(.*?)</nav>', html, re.S).group(1)
    links = re.findall(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', nav, re.S)
    assert [(h, text(t).split()[0]) for h, t in links] == [("/", "Resumen"), ("/actividad", "Actividad"),
                                                          ("/usuarios", "Usuarios"), ("/rutas", "Rutas")]
    assert nav.count('aria-current="page"') == 1
    assert re.search(r'<a [^>]*href="/"[^>]*aria-current="page"', nav)


def test_current_section_follows_the_page():
    e = full()
    for path, href in [("/actividad", "/actividad"), ("/users/u_1/decisions/d_1", "/actividad"),
                       ("/usuarios", "/usuarios"), ("/rutas", "/rutas")]:
        html = e.panel().get(path).text
        assert html.count('aria-current="page"') == 1, path
        assert re.search(r'<a [^>]*href="%s"[^>]*aria-current="page"' % href, html), path


def test_sidebar_shows_wordmark_with_logo_and_favicon_link():
    panel = full().panel()
    html = panel.get("/").text
    assert re.search(r'<img [^>]*src="/static/mark\.svg[^"]*"[^>]*alt=""[^>]*width="26"', html)
    assert 'rel="icon"' in html and "/static/favicon.svg" in html and "Veridian" in html
    for f in ("mark.svg", "favicon.svg"):
        r = panel.get(f"/static/{f}")
        assert r.status_code == 200 and "svg" in r.headers["content-type"] and "<svg" in r.text


def test_badge_counts_open_blocks_with_words_for_screen_readers():
    e = full(decisions=[dec("d_1"), dec("d_2", user="u_2"), dec("d_3", status="marked_normal"), dec("d_4", decision="alert")])
    html = e.panel().get("/usuarios").text
    badge = re.search(r'<span id="nav-badge"[^>]*>(.*?)</span>\s*</a>', html, re.S) or re.search(r'id="nav-badge".*?</span>.*?</span>', html, re.S)
    assert badge and 'aria-hidden="true">2<' in html
    assert "2 bloqueos abiertos" in html
    assert "1 bloqueo abierto" in full().panel().get("/").text
    quiet = full(decisions=[]).panel().get("/").text
    assert "bloqueo abierto" not in quiet and "bloqueos abiertos" not in quiet


def test_connection_card_live_and_last_access_from_newest_row():
    html = full(activity=[act("e_9", ts="2026-10-26T14:59:50Z", verdict="normal", decision_id=None), act()]).panel().get("/").text
    assert "En vivo" in html and 'id="conn"' in html
    assert re.search(r'data-last-access="2026-10-26T14:59:50Z"', html)
    assert "último acceso" in html


def test_connection_card_when_engine_is_down():
    html = Engine(down=True).panel().get("/").text
    assert "Motor sin conexión" in html and 'class="conn down"' in html.replace("conn-card ", "")
    assert "data-last-access" not in html


def test_menu_button_for_narrow_screens():
    html = full().panel().get("/").text
    assert re.search(r'<button[^>]*class="menu-btn"[^>]*aria-expanded="false"[^>]*aria-controls="side"', html) or \
        re.search(r'<button[^>]*aria-controls="side"[^>]*aria-expanded="false"', html)
    assert 'id="side"' in html


def test_every_page_polls_its_own_url_and_has_a_noscript_refresh():
    e = full()
    for path in ("/", "/usuarios", "/rutas"):
        html = e.panel().get(path).text
        assert f'hx-get="{path}"' in html and 'hx-trigger="every 2s [refreshAllowed()]"' in html, path
        assert f'<noscript><meta http-equiv="refresh" content="2;url={path}">' in html, path


# -- resumen ---------------------------------------------------------------------------------------

def test_headline_counts_todays_stopped_blocks():
    one = full().panel().get("/").text
    assert "Un robo detenido hoy. El resto, uso normal." in one
    two = full(decisions=[dec("d_1"), dec("d_2", user="u_2")]).panel().get("/").text
    assert "2 robos detenidos hoy. El resto, uso normal." in two


def test_headline_ignores_marked_normal_old_and_non_block_decisions():
    e = full(decisions=[dec("d_1", status="marked_normal"), dec("d_2", ts="2026-10-25T14:00:00Z"), dec("d_3", decision="alert")])
    html = e.panel().get("/").text
    assert "Todo normal hoy." in html and "robo" not in text(re.search(r"<h1.*?</h1>", html, re.S).group(0))


def test_headline_when_engine_is_down_does_not_claim_normal():
    html = Engine(down=True).panel().get("/").text
    assert "Todo normal hoy" not in html and "No se pudo conectar con el motor" in html


def test_summary_is_requested_for_the_local_day_and_never_with_an_empty_range():
    e = full()
    e.panel().get("/")
    q = e.summary_requests()[0].url.params
    assert (q["since"], q["until"]) == ("2026-10-26T04:00:00Z", "2026-10-27T04:00:00Z")
    e2 = full()
    e2.panel(cookies={}).get("/")  # no timezone cookie yet: default Caracas
    q = e2.summary_requests()[0].url.params
    assert q["since"] < q["until"]
    e3 = full()
    e3.panel(cookies={"vtz": "banana"}).get("/")
    assert e3.summary_requests()[0].url.params["since"] < e3.summary_requests()[0].url.params["until"]
    e4 = full()
    e4.panel(cookies={"vtz": "330"}).get("/")  # UTC+5:30
    assert e4.summary_requests()[0].url.params["since"] == "2026-10-25T18:30:00Z"


def test_tiles_show_the_engine_numbers_and_nothing_hardcoded():
    html = full().panel().get("/").text
    assert tile(html, "Accesos vigilados") == "137 de 9 usuarios"
    assert tile(html, "Bloqueos activos") == "1 3 sesiones y 1 IP"
    ai = tile(html, "Consultas a la IA")
    assert ai == "5 por 137 accesos vigilados" and "%" not in html
    assert "Revisados por la IA" not in html
    assert 'class="kpi-value block"' in html  # active blocks use the block color


def test_blocks_tile_is_quiet_without_blocks():
    e = Engine(decisions=[], summary_body=summary(blocks_active={"decisions": 0, "sessions": 0, "ips": 0}), users=USERS)
    html = e.panel().get("/").text
    assert tile(html, "Bloqueos activos").startswith("0")
    assert 'class="kpi-value block"' not in html


def test_ai_tile_says_when_the_ai_is_off_only_if_deviations_are_visible():
    off = full(); off.summary_body = summary(reviewed_by_ai=0)
    assert tile(off.panel().get("/").text, "Consultas a la IA") == "0 IA desactivada"
    calm = Engine(decisions=[], summary_body=summary(reviewed_by_ai=0, blocks_active={"decisions": 0, "sessions": 0, "ips": 0}), users=USERS)
    assert tile(calm.panel().get("/").text, "Consultas a la IA") == "0"


def test_detection_tile_is_the_median_over_todays_blocks_and_hidden_without_them():
    ds = [dec("d_1", ts="2026-10-26T14:00:02Z", access_ts="2026-10-26T14:00:00Z"),
          dec("d_2", user="u_2", ts="2026-10-26T14:10:04Z", access_ts="2026-10-26T14:10:00Z"),
          dec("d_3", user="u_3", ts="2026-10-26T14:20:10Z", access_ts="2026-10-26T14:20:00Z"),
          dec("d_old", ts="2026-10-20T14:00:59Z", access_ts="2026-10-20T14:00:00Z")]
    html = full(decisions=ds).panel().get("/").text
    assert tile(html, "Detección") == "4 s del acceso al bloqueo"
    assert "Detección" not in Engine(decisions=[dec(decision="alert")], summary_body=summary(), users=USERS).panel().get("/").text


def test_chart_uses_local_hours_markers_tooltips_and_a_data_table():
    html = full().panel().get("/").text
    chart = re.search(r'<div class="chart"[^>]*role="img"[^>]*aria-label="([^"]+)"', html)
    assert chart and "de 9 a 10 h" in chart.group(1) and "Pico de 11 accesos a las 10 h" in chart.group(1)
    assert "09 h · 3 accesos" in html and "10 h · 11 accesos · 1 bloqueo" in html
    assert html.count('class="bar has-block"') == 1 and "Hora con un bloqueo" in html
    table = re.search(r'<table class="vh">(.*?)</table>', html, re.S).group(1)
    assert "<caption>" in table and re.findall(r"<td>(\d+)</td>", table) == ["3", "0", "11", "1"]
    heights = [float(h) for h in re.findall(r'class="fill" style="height:([\d.]+)%', html)]
    assert heights[1] == 100.0 and 0 < heights[0] < 100


def test_chart_is_omitted_without_accesses():
    e = Engine(decisions=[], activity=[], summary_body=summary(accesses=0, users=0, by_hour=[],
               blocks_active={"decisions": 0, "sessions": 0, "ips": 0}), users=USERS)
    html = e.panel().get("/").text
    assert 'role="img"' not in html and "Sin accesos hoy" in html


def test_active_blocks_list_links_to_the_decision():
    html = full().panel().get("/").text
    sec = re.search(r'<section[^>]*aria-labelledby="blocks-title".*?</section>', html, re.S).group(0)
    assert 'href="/users/u_1/decisions/d_1"' in sec and "Bloqueado" in sec and "u_1" in sec and "POST /payouts/bank-account" in sec
    quiet = full(decisions=[]).panel().get("/").text
    assert "Ningún bloqueo activo" in quiet


def test_users_list_rows_with_state_and_history():
    html = full().panel().get("/").text
    sec = re.search(r'<section[^>]*aria-labelledby="users-title".*?</section>', html, re.S).group(0)
    assert "14 días · 1 red" in text(sec) and "9 días · 2 redes" in text(sec)
    assert "Bloqueado" in sec and "Normal" in sec
    assert 'href="/users/u_1/decisions/d_1"' in sec


# -- fallback when the engine has no summary / users yet ---------------------------------------------

def degraded():
    return Engine(activity=[act("e_1"), act("e_2", verdict="normal", decision_id=None, user="u_2"),
                            act("e_3", ts="2026-10-26T13:10:00Z", verdict="normal", decision_id=None, user="u_2")])


def test_without_summary_numbers_are_computed_from_the_window_and_labelled():
    html = degraded().panel().get("/").text
    assert tile(html, "Accesos vigilados") == "3 en los últimos 3 accesos, de 2 usuarios"
    assert "Consultas a la IA" not in html  # nothing honest to show
    assert tile(html, "Bloqueos activos") == "1 1 sesión y 1 IP"  # blocklist + decisions are real
    assert "Un robo detenido hoy" in html
    assert "en los últimos 3 accesos" in html.split('role="img"')[1]  # chart says what window it covers


def test_without_users_endpoint_rows_come_from_what_the_panel_knows():
    html = degraded().panel().get("/").text
    sec = re.search(r'<section[^>]*aria-labelledby="users-title".*?</section>', html, re.S).group(0)
    assert "u_1" in sec and "u_2" in sec and "Bloqueado" in sec and "días" not in sec


def test_without_anything_the_overview_has_no_chart_or_tiles_invented():
    e = Engine(decisions=[], activity=None)
    html = e.panel().get("/").text
    assert "Todo normal hoy." in html and 'role="img"' not in html
    assert "Accesos vigilados" not in html and "Consultas a la IA" not in html


# -- usuarios, rutas --------------------------------------------------------------------------------

def test_usuarios_table_from_the_engine():
    html = full().panel().get("/usuarios").text
    table = re.search(r"<table.*?</table>", html, re.S).group(0)
    for h in ("Usuario", "Estado", "Historial", "Redes", "Dispositivos", "Último acceso", "Bloqueos abiertos"):
        assert h in table
    rows = re.findall(r"<tr>.*?</tr>", table.split("<tbody>")[1], re.S)
    assert len(rows) == 2 and "u_1" in rows[0] and "Bloqueado" in rows[0] and "14 días" in rows[0]
    assert 'href="/users/u_1/decisions/d_1"' in rows[0]
    assert "href" not in rows[1]  # u_17 has no decision to open
    assert re.search(r'<time class="ts"[^>]*datetime="2026-10-26T14:14:03Z"', rows[0])


def test_usuarios_without_the_endpoint_says_so():
    html = Engine(users=None, summary_body=None).panel().get("/usuarios").text
    assert "<table" not in html and "El motor todavía no entrega la lista de usuarios" in html


def test_rutas_is_an_honest_empty_state():
    html = full().panel().get("/rutas").text
    assert "La clasificación de rutas vive en el motor (routes.yaml). Pronto aquí." in html
    assert "<table" not in html and "/payouts" not in text(re.search(r'<main id="live".*?</main>', html, re.S).group(0))


def test_actividad_is_the_old_live_view():
    html = full().panel().get("/actividad").text
    assert 'aria-label="Actividad"' in html and "<h1" in html and 'id="detalle"' in html


def test_poll_response_of_each_page_carries_the_sidebar_status_for_oob_swap():
    html = full().panel().get("/usuarios").text
    live = re.search(r'<main id="live"[^>]*>', html).group(0)
    assert 'hx-select-oob="#conn,#nav-badge"' in live
    assert 'id="nav-badge"' in html and 'id="conn"' in html
