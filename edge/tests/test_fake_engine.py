"""Contract tests for the fake engine (docs/REPARTO.md, section Contratos)."""

import pytest
from fastapi.testclient import TestClient

from fake_engine import create_app

TOKEN = "tok-a"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

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


@pytest.fixture
def client():
    app = create_app(tokens={TOKEN, "tok-b"}, seed=SEED, learn_delay=0)
    return TestClient(app)


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

    decisions = client.get("/v1/decisions", headers=AUTH).json()["decisions"]
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
    assert client.get("/v1/decisions", headers=AUTH).json()["decisions"] == []


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
    prof = client.get("/v1/users/u_42/profile", headers=AUTH).json()
    assert "203.0.113.0/24" not in prof["networks"]
    assert prof["routes"] == ["GET /invoices", "GET /invoices/{id}", "GET /payouts/bank-account"]  # seed only


def test_normal_events_are_learned(client):
    post_events(client, [make_event(id="e_l", ip="198.51.100.4", route="/invoices/export")])
    prof = client.get("/v1/users/u_42/profile", headers=AUTH).json()
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
    ds = client.get("/v1/decisions", headers=AUTH).json()["decisions"]
    assert [d["event_ids"] for d in ds] == [["e_2"], ["e_1"]]
    ds = client.get("/v1/decisions?limit=1", headers=AUTH).json()["decisions"]
    assert len(ds) == 1 and ds[0]["event_ids"] == ["e_2"]
    assert client.get("/v1/decisions?limit=0", headers=AUTH).status_code == 422


def test_mark_normal_unblocks_and_bumps_version(client):
    post_events(client, [attack()])
    d = client.get("/v1/decisions", headers=AUTH).json()["decisions"][0]
    r = client.post(f"/v1/decisions/{d['id']}/normal", headers=AUTH)
    assert r.status_code == 200
    bl = client.get("/v1/blocklist", headers=AUTH).json()
    assert bl == {"version": 2, "users": [], "sessions": [], "ips": []}
    # the profile learned it: the same action is no longer suspicious
    prof = client.get("/v1/users/u_42/profile", headers=AUTH).json()
    assert "203.0.113.0/24" in prof["networks"]
    # calling again does not bump the version
    client.post(f"/v1/decisions/{d['id']}/normal", headers=AUTH)
    assert client.get("/v1/blocklist", headers=AUTH).json()["version"] == 2


def test_decision_status_goes_from_open_to_marked_normal(client):
    post_events(client, [attack()])
    d = client.get("/v1/decisions", headers=AUTH).json()["decisions"][0]
    assert d["status"] == "open"
    r = client.post(f"/v1/decisions/{d['id']}/normal", headers=AUTH)
    assert r.json()["status"] == "marked_normal"
    d = client.get("/v1/decisions", headers=AUTH).json()["decisions"][0]
    assert d["status"] == "marked_normal"


def test_mark_normal_unknown_decision_is_404(client):
    assert client.post("/v1/decisions/d_nope/normal", headers=AUTH).status_code == 404


# --- profile ---------------------------------------------------------------


def test_profile_shape_and_state(client):
    prof = client.get("/v1/users/u_42/profile", headers=AUTH).json()
    assert set(prof) == {"user_id", "history_days", "networks", "devices", "hours", "routes", "state"}
    assert prof["networks"] == ["190.202.14.0/24"]
    assert prof["state"] == "normal"
    post_events(client, [attack()])
    assert client.get("/v1/users/u_42/profile", headers=AUTH).json()["state"] == "blocked"


def test_profile_unknown_user_is_empty(client):
    prof = client.get("/v1/users/u_nobody/profile", headers=AUTH).json()
    assert prof["networks"] == [] and prof["routes"] == []


# --- isolation -------------------------------------------------------------


def test_clients_are_isolated(client):
    post_events(client, [attack()])
    other = client.get("/v1/blocklist", headers={"Authorization": "Bearer tok-b"}).json()
    assert other["version"] == 0


# --- delayed learning ------------------------------------------------------


def test_attackers_own_events_do_not_whitelist_their_network_before_the_delay():
    now = [0.0]
    c = TestClient(create_app(tokens={TOKEN}, seed=SEED, learn_delay=300, clock=lambda: now[0]))
    # the attacker logs in first (normal route, new network): not learned yet
    post_events(c, [make_event(id="e_login", session_id="s_atk", ip="203.0.113.9", method="POST", route="/login")])
    post_events(c, [attack()])
    assert c.get("/v1/blocklist", headers=AUTH).json()["sessions"] == ["s_atk"]
    # after the delay the unflagged login would be learned
    now[0] = 301.0
    prof = c.get("/v1/users/u_42/profile", headers=AUTH).json()
    assert "203.0.113.0/24" in prof["networks"]
