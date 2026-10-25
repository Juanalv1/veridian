from datetime import timedelta

import pytest

from conftest import AUTH_B, NOW, ROUTES_FILE, days_ago, make_event, post_events

from veridian_engine.route_classes import RouteClasses


def seed_history(client):
    events = []
    for day in range(1, 15):
        events.append(make_event(f"h{day}a", days_ago(day, hour=9), route="/invoices"))
        events.append(make_event(f"h{day}b", days_ago(day, hour=13), route="/invoices/{id}"))
    events.append(make_event("hx", days_ago(3, hour=13), ip="190.202.14.200", device_id=""))
    assert post_events(client, events).status_code == 202


def test_route_classes_from_yaml():
    classes = RouteClasses.load(str(ROUTES_FILE))
    assert classes.classify("POST", "/payouts/bank-account") == "critical"
    assert classes.classify("GET", "/invoices/export") == "sensitive"
    assert classes.classify("GET", "/invoices") == "normal"
    assert classes.classify("DELETE", "/unknown") == "normal"


def test_same_route_with_different_method_gets_different_class():
    classes = RouteClasses.load(str(ROUTES_FILE))
    assert classes.classify("GET", "/payouts/bank-account") == "sensitive"
    assert classes.classify("post", "/payouts/bank-account") == "critical"
    assert classes.classify("PUT", "/payouts/bank-account") == "normal"


def test_route_keys_without_method_are_rejected(tmp_path):
    bad = tmp_path / "routes.yaml"
    bad.write_text("routes:
  /payouts/bank-account: critical
", encoding="utf-8")
    with pytest.raises(ValueError):
        RouteClasses.load(str(bad))


def test_profile_shape_matches_contract(client):
    seed_history(client)
    response = client.get("/v1/users/u_42/profile", headers={"Authorization": "Bearer tok_a"})
    assert response.status_code == 200
    assert response.json() == {
        "user_id": "u_42",
        "history_days": 14,
        "networks": ["190.202.14.0/24"],
        "devices": ["d_9f3"],
        "hours": [9, 13],
        "routes": ["GET /invoices", "GET /invoices/{id}"],
        "state": "normal",
    }


def test_ipv6_network_is_a_64(client):
    post_events(client, [make_event("v6", days_ago(1), ip="2001:db8:1:2:3:4:5:6")])
    profile = client.get("/v1/users/u_42/profile", headers={"Authorization": "Bearer tok_a"}).json()
    assert profile["networks"] == ["2001:db8:1:2::/64"]


def test_events_within_learn_delay_are_not_learned(client, clock):
    post_events(client, [make_event("recent", NOW - timedelta(minutes=5), route="/reports")])
    headers = {"Authorization": "Bearer tok_a"}
    assert client.get("/v1/users/u_42/profile", headers=headers).json()["routes"] == []

    clock.now = NOW + timedelta(minutes=6)
    assert client.get("/v1/users/u_42/profile", headers=headers).json()["routes"] == ["GET /reports"]


def test_unknown_user_has_empty_profile(client):
    profile = client.get("/v1/users/nobody/profile", headers={"Authorization": "Bearer tok_a"}).json()
    assert profile == {
        "user_id": "nobody",
        "history_days": 0,
        "networks": [],
        "devices": [],
        "hours": [],
        "routes": [],
        "state": "normal",
    }


def test_profile_is_isolated_per_client(client):
    seed_history(client)
    profile = client.get("/v1/users/u_42/profile", headers=AUTH_B).json()
    assert profile["routes"] == [] and profile["history_days"] == 0


def test_profile_requires_token(client):
    assert client.get("/v1/users/u_42/profile").status_code == 401


def test_profile_routes_keep_method_apart(client):
    post_events(client, [make_event("g", days_ago(2), method="GET", route="/payouts/bank-account")])
    profile = client.get("/v1/users/u_42/profile", headers={"Authorization": "Bearer tok_a"}).json()
    assert profile["routes"] == ["GET /payouts/bank-account"]
    assert "POST /payouts/bank-account" not in profile["routes"]
