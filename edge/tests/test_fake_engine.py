"""Contract tests for the fake engine (docs/REPARTO.md, section Contratos)."""

import pytest
from fastapi.testclient import TestClient

from fake_engine import create_app

TOKEN = "tok-a"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PANEL = "panel-secret"
OWNER_EMAIL = "owner@example.com"

SEED = [
    {
        "user_id": "u_42",
        "ips": ["190.202.14.7"],
        "routes": ["GET /invoices", "GET /invoices/{id}", "GET /payouts/bank-account"],
    }
]


def make_event(**over):
    base = {
        "id": "e_1",
        "ts": "2026-10-26T10:14:03Z",
        "user_id": "u_42",
        "session_id": "s_1",
        "ip": "190.202.14.9",
        "user_agent": "Chrome 141 / Windows",
        "device_id": "d_1",
        "method": "GET",
        "route": "/invoices",
        "status": 200,
        "bytes": 512,
    }
    base.update(over)
    return base


def attack(**over):
    fields = dict(
        id="e_atk",
        session_id="s_atk",
        ip="203.0.113.9",
        method="POST",
        route="/payouts/bank-account",
    )
    fields.update(over)
    return make_event(**fields)


def panel_headers(client, sdk_token=TOKEN):
    """Panel principal acting as the bootstrap owner on the project that `sdk_token` belongs to."""
    person = client.post(
        "/v1/people/login",
        json={"google_sub": "sub-owner", "email": OWNER_EMAIL, "name": "Owner"},
        headers={"Authorization": f"Bearer {PANEL}"},
    ).json()["person"]
    return {
        "Authorization": f"Bearer {PANEL}",
        "X-Veridian-Actor": person["id"],
        "X-Veridian-Project": client.app.state.project_id_for(sdk_token),
    }


@pytest.fixture
def client():
    app = create_app(tokens={TOKEN, "tok-b"}, seed=SEED, learn_delay=0,
                     panel_tokens={PANEL}, bootstrap_owners={OWNER_EMAIL})
    c = TestClient(app)
    c.panel_a = panel_headers(c, TOKEN)
    c.panel_b = panel_headers(c, "tok-b")
    return c


def post_events(client, events, headers=AUTH):
    return client.post("/v1/events", json={"events": events}, headers=headers)


# --- auth and validation ---------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/v1/events"),
        ("get", "/v1/blocklist"),
        ("get", "/v1/decisions"),
        ("post", "/v1/decisions/d_1/normal"),
        ("get", "/v1/users/u_42/profile"),
    ],
)
def test_missing_or_bad_token_is_401(client, method, path):
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}):
        r = getattr(client, method)(path, headers=headers)
        assert r.status_code == 401
        assert r.json() == {"error": "unauthorized"}


def test_no_tokens_configured_rejects_everything(monkeypatch):
    monkeypatch.delenv("VERIDIAN_TOKENS", raising=False)
    monkeypatch.delenv("VERIDIAN_ALLOW_DEV_TOKEN", raising=False)
    c = TestClient(create_app())
    assert c.get("/v1/blocklist", headers={"Authorization": "Bearer dev-token"}).status_code == 401


def test_tokens_from_env(monkeypatch):
    monkeypatch.setenv("VERIDIAN_TOKENS", "x1, x2")
    c = TestClient(create_app())
    assert c.get("/v1/blocklist", headers={"Authorization": "Bearer x2"}).status_code == 200


def test_dev_token_only_when_env_allows(monkeypatch):
    monkeypatch.delenv("VERIDIAN_TOKENS", raising=False)
    monkeypatch.setenv("VERIDIAN_ALLOW_DEV_TOKEN", "1")
    c = TestClient(create_app())
    assert c.get("/v1/blocklist", headers={"Authorization": "Bearer dev-token"}).status_code == 200


def test_invalid_event_body_is_422(client):
    assert client.post("/v1/events", json={"events": [{"id": "x"}]}, headers=AUTH).status_code == 422
    assert client.post("/v1/events", json={}, headers=AUTH).status_code == 422


# --- events ----------------------------------------------------------------


def test_events_accepted_and_idempotent(client):
    r = post_events(client, [make_event(id="e_1"), make_event(id="e_2")])
    assert r.status_code == 202
    assert r.json() == {"accepted": 2}
    again = post_events(client, [make_event(id="e_1"), make_event(id="e_3")])
    assert again.json() == {"accepted": 1}


# --- block rule ------------------------------------------------------------


def test_critical_unused_route_from_new_network_blocks(client):
    post_events(client, [attack()])
    bl = client.get("/v1/blocklist", headers=AUTH).json()
    assert bl == {"version": 1, "users": [], "sessions": ["s_atk"], "ips": ["203.0.113.9"]}

    decisions = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"]
    assert len(decisions) == 1
    d = decisions[0]
    assert d["decision"] == "block"
    assert d["user_id"] == "u_42"
    assert d["event_ids"] == ["e_atk"]
    assert d["ts"].endswith("Z")
    assert d["signals"]["new_network"] is True
    assert d["signals"]["route"] == "/payouts/bank-account"
    assert d["signals"]["route_class"] == "critical"
    assert d["signals"]["route_never_used"] is True
    assert d["rationale"]


def test_owner_from_known_network_is_not_blocked(client):
    # same /24 as the seeded history, even if never used the critical route
    post_events(client, [make_event(id="e_own", ip="190.202.14.200", method="POST", route="/payouts/bank-account")])
    assert client.get("/v1/blocklist", headers=AUTH).json()["version"] == 0
    assert client.get("/v1/decisions", headers=client.panel_a).json()["decisions"] == []


def test_new_network_on_normal_route_is_not_blocked(client):
    post_events(client, [make_event(id="e_n", ip="203.0.113.9")])
    assert client.get("/v1/blocklist", headers=AUTH).json()["version"] == 0


def test_used_critical_route_from_new_network_is_not_blocked(client):
    post_events(client, [make_event(id="e_h", ip="190.202.14.5", method="POST", route="/payouts/bank-account")])
    # now the route is in the profile; a new network no longer trips the rule
    post_events(client, [make_event(id="e_y", ip="203.0.113.9", method="POST", route="/payouts/bank-account")])
    assert client.get("/v1/blocklist", headers=AUTH).json()["version"] == 0


def test_blocked_event_is_not_learned(client):
    post_events(client, [attack()])
    prof = client.get("/v1/users/u_42/profile", headers=client.panel_a).json()
    assert "203.0.113.0/24" not in prof["networks"]
    assert prof["routes"] == ["GET /invoices", "GET /invoices/{id}", "GET /payouts/bank-account"]  # seed only


def test_normal_events_are_learned(client):
    post_events(client, [make_event(id="e_l", ip="198.51.100.4", route="/invoices/export")])
    prof = client.get("/v1/users/u_42/profile", headers=client.panel_a).json()
    assert "198.51.100.0/24" in prof["networks"]
    assert "GET /invoices/export" in prof["routes"]
    assert "d_1" in prof["devices"]


def test_user_without_history_is_learned_not_blocked(client):
    post_events(client, [make_event(id="e_f", user_id="u_new", ip="203.0.113.9", method="POST", route="/payouts/bank-account")])
    assert client.get("/v1/blocklist", headers=AUTH).json()["version"] == 0


# --- blocklist -------------------------------------------------------------


def test_blocklist_since_current_version_is_304(client):
    post_events(client, [attack()])
    r = client.get("/v1/blocklist?since=1", headers=AUTH)
    assert r.status_code == 304
    assert r.content == b""
    full = client.get("/v1/blocklist?since=0", headers=AUTH)
    assert full.status_code == 200 and full.json()["version"] == 1


def test_blocklist_without_since_returns_current(client):
    assert client.get("/v1/blocklist", headers=AUTH).json() == {
        "version": 0, "users": [], "sessions": [], "ips": [],
    }


def test_blocklist_bad_since_is_422(client):
    assert client.get("/v1/blocklist?since=abc", headers=AUTH).status_code == 422


# --- decisions and feedback ------------------------------------------------


def test_decisions_newest_first_and_limit(client):
    post_events(client, [attack(id="e_1", session_id="s_1", ip="203.0.113.1")])
    post_events(client, [attack(id="e_2", session_id="s_2", ip="198.51.100.1")])
    ds = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"]
    assert [d["event_ids"] for d in ds] == [["e_2"], ["e_1"]]
    ds = client.get("/v1/decisions?limit=1", headers=client.panel_a).json()["decisions"]
    assert len(ds) == 1 and ds[0]["event_ids"] == ["e_2"]
    assert client.get("/v1/decisions?limit=0", headers=client.panel_a).status_code == 422


def test_mark_normal_unblocks_and_bumps_version(client):
    post_events(client, [attack()])
    d = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]
    r = client.post(f"/v1/decisions/{d['id']}/normal", headers=client.panel_a)
    assert r.status_code == 200
    bl = client.get("/v1/blocklist", headers=AUTH).json()
    assert bl == {"version": 2, "users": [], "sessions": [], "ips": []}
    # the profile learned it: the same action is no longer suspicious
    prof = client.get("/v1/users/u_42/profile", headers=client.panel_a).json()
    assert "203.0.113.0/24" in prof["networks"]
    # calling again does not bump the version
    client.post(f"/v1/decisions/{d['id']}/normal", headers=client.panel_a)
    assert client.get("/v1/blocklist", headers=AUTH).json()["version"] == 2


def test_decision_status_goes_from_open_to_marked_normal(client):
    post_events(client, [attack()])
    d = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]
    assert d["status"] == "open"
    r = client.post(f"/v1/decisions/{d['id']}/normal", headers=client.panel_a)
    assert r.json()["status"] == "marked_normal"
    d = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]
    assert d["status"] == "marked_normal"


def test_mark_normal_unknown_decision_is_404(client):
    assert client.post("/v1/decisions/d_nope/normal", headers=client.panel_a).status_code == 404


# --- profile ---------------------------------------------------------------


def test_profile_shape_and_state(client):
    prof = client.get("/v1/users/u_42/profile", headers=client.panel_a).json()
    assert set(prof) == {"user_id", "history_days", "networks", "devices", "hours", "routes", "state"}
    assert prof["networks"] == ["190.202.14.0/24"]
    assert prof["state"] == "normal"
    post_events(client, [attack()])
    assert client.get("/v1/users/u_42/profile", headers=client.panel_a).json()["state"] == "blocked"


def test_profile_unknown_user_is_empty(client):
    prof = client.get("/v1/users/u_nobody/profile", headers=client.panel_a).json()
    assert prof["networks"] == [] and prof["routes"] == []


# --- isolation -------------------------------------------------------------


def test_clients_are_isolated(client):
    post_events(client, [attack()])
    other = client.get("/v1/blocklist", headers={"Authorization": "Bearer tok-b"}).json()
    assert other["version"] == 0


# --- delayed learning ------------------------------------------------------


def test_attackers_own_events_do_not_whitelist_their_network_before_the_delay():
    now = [0.0]
    c = TestClient(create_app(tokens={TOKEN}, seed=SEED, learn_delay=300, clock=lambda: now[0],
                              panel_tokens={PANEL}, bootstrap_owners={OWNER_EMAIL}))
    c.panel_a = panel_headers(c)
    # the attacker logs in first (normal route, new network): not learned yet
    post_events(c, [make_event(id="e_login", session_id="s_atk", ip="203.0.113.9", method="POST", route="/login")])
    post_events(c, [attack()])
    assert c.get("/v1/blocklist", headers=AUTH).json()["sessions"] == ["s_atk"]
    # after the delay the unflagged login would be learned
    now[0] = 301.0
    prof = c.get("/v1/users/u_42/profile", headers=c.panel_a).json()
    assert "203.0.113.0/24" in prof["networks"]


def test_decision_carries_the_triggering_access(client):
    post_events(client, [attack()])
    d = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]
    assert d["access"] == {
        "ts": "2026-10-26T10:14:03Z",
        "ip": "203.0.113.9",
        "device_id": "d_1",
        "session_id": "s_atk",
        "method": "POST",
        "route": "/payouts/bank-account",
    }


# --- 403 events (rejected by the middleware) -------------------------------


def rejected(**over):
    fields = dict(id="e_rej1", session_id="s_atk", ip="203.0.113.9", method="GET",
                  route="/invoices", status=403, bytes=21, ts="2026-10-26T10:14:09Z")
    fields.update(over)
    return make_event(**fields)


def test_403_events_never_enter_the_profile(client):
    r = post_events(client, [rejected(ip="198.51.100.4", route="/invoices/export", device_id="d_rej")])
    assert r.json() == {"accepted": 1}
    prof = client.get("/v1/users/u_42/profile", headers=client.panel_a).json()
    assert "198.51.100.0/24" not in prof["networks"]
    assert "GET /invoices/export" not in prof["routes"]
    assert "d_rej" not in prof["devices"]


def test_403_events_never_trigger_a_decision(client):
    post_events(client, [rejected(method="POST", route="/payouts/bank-account")])
    assert client.get("/v1/decisions", headers=client.panel_a).json()["decisions"] == []
    assert client.get("/v1/blocklist", headers=AUTH).json()["version"] == 0


# --- activity (contract 5) -------------------------------------------------


def activity(client, query="", headers=None):
    return client.get(f"/v1/activity{query}", headers=client.panel_a if headers is None else headers)


def test_activity_requires_auth(client):
    r = activity(client, headers={})
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}


def test_activity_empty(client):
    assert activity(client).json() == {"activity": []}


def test_activity_normal_events_have_no_decision_newest_first(client):
    post_events(client, [make_event(id="e_a", ts="2026-10-26T10:00:00Z")])
    post_events(client, [make_event(id="e_b", ts="2026-10-26T10:00:05Z", route="/invoices/{id}")])
    items = activity(client).json()["activity"]
    assert [i["event_id"] for i in items] == ["e_b", "e_a"]
    assert items[0] == {
        "event_id": "e_b", "ts": "2026-10-26T10:00:05Z", "user_id": "u_42", "session_id": "s_1",
        "ip": "190.202.14.9", "device_id": "d_1", "method": "GET", "route": "/invoices/{id}",
        "verdict": "normal", "status": 200, "decision_id": None,
    }
    assert "user_agent" not in items[0] and "bytes" not in items[0]


def test_activity_limit_default_bounds_and_422(client):
    post_events(client, [make_event(id=f"e_{n}") for n in range(60)])
    assert len(activity(client).json()["activity"]) == 50
    assert len(activity(client, "?limit=3").json()["activity"]) == 3
    assert len(activity(client, "?limit=200").json()["activity"]) == 60
    for bad in ("0", "201", "abc"):
        assert activity(client, f"?limit={bad}").status_code == 422


def test_activity_triggering_access_is_block_with_its_decision(client):
    post_events(client, [make_event(id="e_ok"), attack()])
    d = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]
    items = {i["event_id"]: i for i in activity(client).json()["activity"]}
    assert items["e_atk"]["verdict"] == "block" and items["e_atk"]["decision_id"] == d["id"]
    assert items["e_ok"]["verdict"] == "normal" and items["e_ok"]["decision_id"] is None


def test_activity_rejected_requests_of_a_blocked_session_are_block(client):
    post_events(client, [attack()])
    d = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]
    post_events(client, [rejected(id="e_r1"), rejected(id="e_r2", route="/invoices/export")])
    items = activity(client).json()["activity"]
    assert [i["event_id"] for i in items] == ["e_r2", "e_r1", "e_atk"]
    assert all(i["verdict"] == "block" and i["decision_id"] == d["id"] for i in items)


def test_activity_owner_in_another_session_stays_normal_after_a_block(client):
    post_events(client, [attack()])
    post_events(client, [make_event(id="e_own", session_id="s_own", ts="2026-10-26T10:20:00Z")])
    items = {i["event_id"]: i for i in activity(client).json()["activity"]}
    assert items["e_own"]["verdict"] == "normal" and items["e_own"]["decision_id"] is None


def test_activity_same_session_before_the_block_stays_normal(client):
    # (a different network: learn_delay=0 here would otherwise whitelist the attacker's /24)
    post_events(client, [make_event(id="e_login", session_id="s_atk", ip="198.51.100.7", method="POST", route="/login")])
    post_events(client, [attack()])
    items = {i["event_id"]: i for i in activity(client).json()["activity"]}
    assert items["e_login"]["verdict"] == "normal" and items["e_login"]["decision_id"] is None
    assert items["e_atk"]["verdict"] == "block"


def test_activity_marked_normal_turns_its_accesses_back_to_normal(client):
    post_events(client, [attack()])
    d = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]
    post_events(client, [rejected(id="e_r1")])
    client.post(f"/v1/decisions/{d['id']}/normal", headers=client.panel_a)
    items = activity(client).json()["activity"]
    assert {i["event_id"] for i in items} == {"e_r1", "e_atk"}
    assert all(i["verdict"] == "normal" for i in items)


def test_activity_review_verdict_for_alert_and_step_up():
    from fake_engine.app import verdict_of

    assert verdict_of({"decision": "alert", "status": "open"}) == "review"
    assert verdict_of({"decision": "step_up", "status": "open"}) == "review"
    assert verdict_of({"decision": "allow", "status": "open"}) == "normal"
    assert verdict_of({"decision": "block", "status": "open"}) == "block"
    assert verdict_of({"decision": "block", "status": "marked_normal"}) == "normal"
    assert verdict_of({"decision": "alert", "status": "marked_normal"}) == "normal"


def test_activity_clients_are_isolated(client):
    post_events(client, [make_event()])
    assert activity(client, headers=client.panel_b).json() == {"activity": []}


def test_activity_idempotent_events_listed_once(client):
    post_events(client, [make_event(id="e_x")])
    post_events(client, [make_event(id="e_x")])
    assert len(activity(client).json()["activity"]) == 1


# --- activity: status, session rule for every status, IP rule --------------


def test_activity_rows_carry_the_http_status(client):
    post_events(client, [make_event(id="e_a", status=200), make_event(id="e_b", status=302),
                         rejected(id="e_c")])
    items = {i["event_id"]: i for i in activity(client).json()["activity"]}
    assert (items["e_a"]["status"], items["e_b"]["status"], items["e_c"]["status"]) == (200, 302, 403)


def test_activity_served_requests_after_the_trigger_in_the_session_are_block_too(client):
    post_events(client, [attack()])
    d = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]
    # in flight before the middleware's next poll: the app answered 200
    post_events(client, [make_event(id="e_late", session_id="s_atk", ip="203.0.113.9", status=200,
                                    route="/invoices/export")])
    items = {i["event_id"]: i for i in activity(client).json()["activity"]}
    assert items["e_late"]["verdict"] == "block" and items["e_late"]["decision_id"] == d["id"]
    assert items["e_late"]["status"] == 200
    # the trigger itself was served too, and is block
    assert items["e_atk"]["status"] == 200 and items["e_atk"]["verdict"] == "block"


def test_activity_blocked_ip_from_another_session_is_block_with_the_decision(client):
    post_events(client, [attack()])
    d = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]
    post_events(client, [rejected(id="e_other", session_id="s_other", ip="203.0.113.9")])
    items = {i["event_id"]: i for i in activity(client).json()["activity"]}
    assert items["e_other"]["verdict"] == "block" and items["e_other"]["decision_id"] == d["id"]


def test_activity_ip_rule_applies_from_the_block_on_not_before(client):
    post_events(client, [make_event(id="e_before", session_id="s_old", ip="203.0.113.9", method="POST", route="/login")])
    post_events(client, [attack(id="e_atk", ip="203.0.113.9")])
    items = {i["event_id"]: i for i in activity(client).json()["activity"]}
    assert items["e_before"]["verdict"] == "normal" and items["e_before"]["decision_id"] is None


def test_activity_ip_rule_without_a_session_on_the_trigger(client):
    post_events(client, [attack(session_id=None)])
    post_events(client, [make_event(id="e_x", session_id="s_x", ip="203.0.113.9")])
    items = {i["event_id"]: i for i in activity(client).json()["activity"]}
    assert items["e_x"]["verdict"] == "block"
    # a session-less trigger must not drag every session-less access into the block
    post_events(client, [make_event(id="e_none", session_id=None, ip="198.51.100.3")])
    items = {i["event_id"]: i for i in activity(client).json()["activity"]}
    assert items["e_none"]["verdict"] == "normal"


def test_activity_other_ip_and_other_session_stay_normal_after_a_block(client):
    post_events(client, [attack()])
    post_events(client, [make_event(id="e_own", session_id="s_own", ip="190.202.14.7")])
    items = {i["event_id"]: i for i in activity(client).json()["activity"]}
    assert items["e_own"]["verdict"] == "normal" and items["e_own"]["decision_id"] is None


def test_activity_marked_normal_turns_session_and_ip_accesses_back_to_normal(client):
    post_events(client, [attack()])
    d = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]
    post_events(client, [make_event(id="e_late", session_id="s_atk", ip="203.0.113.9", status=200),
                         rejected(id="e_other", session_id="s_other")])
    client.post(f"/v1/decisions/{d['id']}/normal", headers=client.panel_a)
    assert all(i["verdict"] == "normal" for i in activity(client).json()["activity"])


# --- session-only blocks (known network) -----------------------------------


def session_only_state():
    """The rule in this fake always comes from a new network (IP blocked too);
    a session-only block (e.g. a rate spike from the owner's network) is
    produced here by calling the block directly, as the real engine does."""
    from fake_engine.app import ClientState, DEFAULT_ROUTES, EventIn

    st = ClientState(routes=dict(DEFAULT_ROUTES))
    ev = lambda **o: EventIn(**{**make_event(), **o})  # noqa: E731
    return st, ev


def test_session_only_block_lists_the_session_but_not_the_ip():
    st, ev = session_only_state()
    st.ingest(ev(id="e_t", session_id="s_own", ip="190.202.14.7"))
    st._block(ev(id="e_t", session_id="s_own", ip="190.202.14.7"), "normal", block_ip=False)
    assert st.sessions == ["s_own"] and st.ips == []


def test_session_only_block_activity_matches_the_session_but_not_the_ip():
    st, ev = session_only_state()
    trigger = ev(id="e_t", session_id="s_own", ip="190.202.14.7")
    st.log.append(trigger)
    st._block(trigger, "normal", block_ip=False)
    st.log.append(ev(id="e_same_session", session_id="s_own", ip="190.202.14.7"))
    st.log.append(ev(id="e_same_ip", session_id="s_other", ip="190.202.14.7"))
    items = {i["event_id"]: i for i in st.activity(10)}
    assert items["e_t"]["verdict"] == "block"
    assert items["e_same_session"]["verdict"] == "block"
    assert items["e_same_ip"]["verdict"] == "normal" and items["e_same_ip"]["decision_id"] is None


def test_marking_a_session_only_block_normal_keeps_an_ip_listed_by_another_block():
    st, ev = session_only_state()
    st.ips.append("190.202.14.7")  # listed by some other open block
    trigger = ev(id="e_t", session_id="s_own", ip="190.202.14.7")
    st.log.append(trigger)
    st._block(trigger, "normal", block_ip=False)
    st.mark_normal(st.decisions[0]["id"])
    assert st.sessions == [] and st.ips == ["190.202.14.7"]


def test_block_from_a_new_network_still_lists_session_and_ip(client):
    post_events(client, [attack()])
    assert client.get("/v1/blocklist", headers=AUTH).json()["ips"] == ["203.0.113.9"]


# --- contract 6: summary, contract 7: users ----------------------------------

from datetime import datetime, timedelta, timezone


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def hour_start(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def now_utc():
    return datetime.now(timezone.utc)


def window(hours_back=1, hours_ahead=1):
    h = hour_start(now_utc())
    return {"since": iso(h - timedelta(hours=hours_back)), "until": iso(h + timedelta(hours=hours_ahead))}


@pytest.mark.parametrize("path", ["/v1/summary", "/v1/users"])
def test_summary_and_users_need_a_token(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_summary_counts_accesses_users_and_rejected_requests(client):
    ts = iso(now_utc())
    post_events(client, [
        make_event(id="e_a", ts=ts, ip="190.202.14.7"),
        make_event(id="e_b", ts=ts, user_id="u_9", ip="190.202.14.7"),
        make_event(id="e_c", ts=ts, status=403),
    ])
    body = client.get("/v1/summary", params=window(), headers=client.panel_a).json()
    assert body["accesses"] == 3 and body["users"] == 2
    assert body["since"] and body["until"]
    assert body["decisions"] == {"allow": 0, "alert": 0, "step_up": 0, "block": 0}
    assert body["blocks_active"] == {"decisions": 0, "sessions": 0, "ips": 0}
    assert body["reviewed_by_ai"] == 0


def test_summary_only_counts_the_requested_range(client):
    old = iso(now_utc() - timedelta(days=3))
    post_events(client, [make_event(id="e_old", ts=old), make_event(id="e_new", ts=iso(now_utc()))])
    assert client.get("/v1/summary", params=window(), headers=client.panel_a).json()["accesses"] == 1
    wide = {"since": iso(now_utc() - timedelta(days=4)), "until": iso(now_utc() + timedelta(hours=1))}
    assert client.get("/v1/summary", params=wide, headers=client.panel_a).json()["accesses"] == 2


def test_summary_without_params_covers_the_last_24_hours(client):
    post_events(client, [make_event(id="e_old", ts=iso(now_utc() - timedelta(days=2))),
                         make_event(id="e_new", ts=iso(now_utc() - timedelta(hours=2)))])
    body = client.get("/v1/summary", headers=client.panel_a).json()
    assert body["accesses"] == 1
    assert datetime.fromisoformat(body["until"].replace("Z", "+00:00")) - datetime.fromisoformat(
        body["since"].replace("Z", "+00:00")) == timedelta(hours=24)


@pytest.mark.parametrize("params", [
    {"since": "2026-10-26T10:00:00Z", "until": "2026-10-26T10:00:00Z"},
    {"since": "2026-10-26T11:00:00Z", "until": "2026-10-26T10:00:00Z"},
    {"since": "not-a-date", "until": "2026-10-26T10:00:00Z"},
])
def test_summary_rejects_an_empty_or_inverted_range_with_422(client, params):
    assert client.get("/v1/summary", params=params, headers=client.panel_a).status_code == 422


def test_summary_block_counts_active_blocks_and_ai_calls_once_per_judged_session(client):
    ts = iso(now_utc())
    post_events(client, [attack(ts=ts)])
    body = client.get("/v1/summary", params=window(), headers=client.panel_a).json()
    assert body["decisions"]["block"] == 1
    assert body["blocks_active"] == {"decisions": 1, "sessions": 1, "ips": 1}
    assert body["reviewed_by_ai"] == 1
    # A second block in the same session is not a second model call; another session is.
    post_events(client, [attack(id="e_atk2", ts=ts, route="/payouts/bank-account")])
    assert client.get("/v1/summary", params=window(), headers=client.panel_a).json()["reviewed_by_ai"] == 1
    post_events(client, [attack(id="e_atk3", ts=ts, session_id="s_other", ip="198.51.100.4")])
    assert client.get("/v1/summary", params=window(), headers=client.panel_a).json()["reviewed_by_ai"] == 2


def test_summary_active_blocks_are_the_current_state_not_the_range(client):
    post_events(client, [attack(ts=iso(now_utc()))])
    did = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]["id"]
    past = {"since": iso(now_utc() - timedelta(days=5)), "until": iso(now_utc() - timedelta(days=4))}
    body = client.get("/v1/summary", params=past, headers=client.panel_a).json()
    assert body["decisions"]["block"] == 0 and body["blocks_active"]["decisions"] == 1
    client.post(f"/v1/decisions/{did}/normal", headers=client.panel_a)
    assert client.get("/v1/summary", params=window(), headers=client.panel_a).json()["blocks_active"] == {
        "decisions": 0, "sessions": 0, "ips": 0}


def test_summary_by_hour_has_one_row_per_hour_with_accesses_in_order(client):
    h = hour_start(now_utc())
    post_events(client, [
        make_event(id="e_1", ts=iso(h - timedelta(hours=2) + timedelta(minutes=5))),
        make_event(id="e_2", ts=iso(h - timedelta(hours=2) + timedelta(minutes=9))),
        make_event(id="e_3", ts=iso(h + timedelta(minutes=1)), ip="190.202.14.7"),
    ])
    body = client.get("/v1/summary", params=window(hours_back=3), headers=client.panel_a).json()
    assert body["by_hour"] == [
        {"hour": iso(h - timedelta(hours=2)), "accesses": 2, "blocks": 0},
        {"hour": iso(h), "accesses": 1, "blocks": 0},
    ]


def test_summary_by_hour_counts_blocks_in_the_hour_the_decision_was_created(client):
    post_events(client, [attack(ts=iso(now_utc()))])
    row = client.get("/v1/summary", params=window(), headers=client.panel_a).json()["by_hour"][-1]
    assert row["hour"] == iso(hour_start(now_utc())) and row["accesses"] == 1 and row["blocks"] == 1


def test_users_lists_known_users_blocked_first_then_newest_first(client):
    post_events(client, [
        make_event(id="e_1", user_id="u_17", ts="2026-10-26T13:40:10Z", ip="190.202.14.7", device_id="d_a"),
        make_event(id="e_2", user_id="u_9", ts="2026-10-26T15:00:00Z", ip="190.202.14.7"),
        attack(ts="2026-10-26T10:00:00Z"),
    ])
    users = client.get("/v1/users", headers=client.panel_a).json()["users"]
    assert [u["user_id"] for u in users] == ["u_42", "u_9", "u_17"]
    first = users[0]
    assert first["state"] == "blocked" and first["open_blocks"] == 1
    assert set(first) == {"user_id", "state", "history_days", "networks", "devices", "last_seen", "open_blocks"}
    assert first["last_seen"] == "2026-10-26T10:00:00Z"
    assert isinstance(first["networks"], int) and isinstance(first["devices"], int)
    assert users[2]["state"] == "normal" and users[2]["open_blocks"] == 0 and users[2]["last_seen"] == "2026-10-26T13:40:10Z"


def test_users_includes_seeded_users_without_events_and_drops_open_block_on_mark_normal(client):
    users = client.get("/v1/users", headers=client.panel_a).json()["users"]
    assert users == [{"user_id": "u_42", "state": "normal", "history_days": 14, "networks": 1,
                      "devices": 0, "last_seen": None, "open_blocks": 0}]
    post_events(client, [attack(ts=iso(now_utc()))])
    did = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"][0]["id"]
    client.post(f"/v1/decisions/{did}/normal", headers=client.panel_a)
    u = client.get("/v1/users", headers=client.panel_a).json()["users"][0]
    assert u["state"] == "normal" and u["open_blocks"] == 0


def test_routes_lists_the_classification_critical_first_and_needs_a_token(client):
    assert client.get("/v1/routes").status_code == 401
    body = client.get("/v1/routes", headers=AUTH).json()
    assert body["default"] == "normal"
    assert body["routes"] == [
        {"method": "POST", "route": "/payouts/bank-account", "class": "critical"},
        {"method": "GET", "route": "/invoices/export", "class": "sensitive"},
        {"method": "GET", "route": "/payouts/bank-account", "class": "sensitive"},
    ]


# --- contract 9: POST /v1/check -----------------------------------------------


def check(client, event, headers=AUTH):
    return client.post("/v1/check", json={"event": event}, headers=headers)


def test_check_requires_a_valid_token_and_body(client):
    assert check(client, attack(), headers={}).status_code == 401
    assert client.post("/v1/check", json={}, headers=AUTH).status_code == 422
    assert check(client, {"id": "x"}).status_code == 422


def test_check_blocks_critical_unused_route_from_new_network(client):
    r = check(client, attack())
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "block" and body["decision_id"]
    bl = client.get("/v1/blocklist", headers=AUTH).json()
    assert bl["sessions"] == ["s_atk"] and bl["ips"] == ["203.0.113.9"]
    (d,) = client.get("/v1/decisions", headers=client.panel_a).json()["decisions"]
    assert d["id"] == body["decision_id"] and d["event_ids"] == ["e_atk"]


def test_check_allows_the_owner(client):
    r = check(client, make_event(id="e_own", ip="190.202.14.200", method="POST", route="/payouts/bank-account"))
    assert r.json() == {"verdict": "allow", "decision_id": None}
    assert client.get("/v1/blocklist", headers=AUTH).json()["version"] == 0


def test_check_records_the_event_and_the_batch_resend_is_idempotent(client):
    check(client, attack())
    r = post_events(client, [attack(status=403)])
    assert r.json() == {"accepted": 0}
    assert len(client.get("/v1/decisions", headers=client.panel_a).json()["decisions"]) == 1
    assert client.get("/v1/blocklist", headers=AUTH).json()["version"] == 1


def test_check_repeated_id_returns_the_same_block(client):
    first = check(client, attack()).json()
    again = check(client, attack()).json()
    assert again == first
    assert client.get("/v1/blocklist", headers=AUTH).json()["version"] == 1


def test_check_of_an_already_blocked_session_blocks(client):
    check(client, attack())
    r = check(client, attack(id="e_atk2", route="/invoices", method="GET"))
    assert r.json()["verdict"] == "block"


def test_check_is_isolated_per_client(client):
    check(client, attack())
    other = {"Authorization": "Bearer tok-b"}
    assert client.get("/v1/blocklist", headers=other).json()["sessions"] == []


def test_the_route_report_is_accepted_and_changes_nothing(client):
    before = client.get("/v1/routes", headers=AUTH).json()
    response = client.put("/v1/routes/catalog", json={"routes": [{"method": "GET", "route": "/x"}]}, headers=AUTH)
    assert response.status_code == 200 and response.json() == {"received": 1, "to_classify": 0}
    assert client.get("/v1/routes", headers=AUTH).json() == before
