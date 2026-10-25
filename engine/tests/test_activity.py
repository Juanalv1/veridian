from datetime import timedelta

from conftest import AUTH_A, AUTH_B, NOW, make_event, post_events
from test_ai_wiring import NEW_NETWORK, FakeJudge, make_client  # noqa: F401 (fixture)
from test_decisions import ATTACKER, attack, decisions, seed_owner_history

ITEM_KEYS = {
    "event_id",
    "ts",
    "user_id",
    "session_id",
    "ip",
    "device_id",
    "method",
    "route",
    "verdict",
    "decision_id",
    "status",
}


def activity(client, headers=AUTH_A, **params):
    response = client.get("/v1/activity", headers=headers, params=params)
    assert response.status_code == 200
    return response.json()["activity"]


def by_id(items):
    return {item["event_id"]: item for item in items}


def blocked_scenario(client):
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW - timedelta(minutes=1), route="/invoices/{id}")])
    attack(client)
    later = make_event("a3", NOW + timedelta(seconds=5), route="/invoices", **ATTACKER)
    post_events(client, [later])
    [decision] = decisions(client)
    return decision["id"]


def test_owner_traffic_is_normal_without_decision(client):
    seed_owner_history(client)
    items = activity(client)
    assert len(items) == 28
    assert all(item["verdict"] == "normal" and item["decision_id"] is None for item in items)


def test_items_have_exact_fields_and_no_user_agent_or_bytes(client):
    blocked_scenario(client)
    items = activity(client)
    assert all(set(item) == ITEM_KEYS for item in items)
    first = items[0]
    assert first == {
        "event_id": "a3",
        "ts": "2026-10-26T12:00:05Z",
        "user_id": "u_42",
        "session_id": "s_evil",
        "ip": "203.0.113.9",
        "device_id": "d_evil",
        "method": "GET",
        "route": "/invoices",
        "verdict": "block",
        "decision_id": first["decision_id"],
        "status": 200,
    }


def test_attack_scenario_verdicts(client):
    decision_id = blocked_scenario(client)
    items = by_id(activity(client))
    # The triggering access carries the block.
    assert items["a2"]["verdict"] == "block"
    assert items["a2"]["decision_id"] == decision_id
    # The attacker's login happened before the block: still normal.
    assert items["a1"]["verdict"] == "normal"
    assert items["a1"]["decision_id"] is None
    # A later request of the blocked session (rejected with 403 by the middleware) is shown as block.
    assert items["a3"]["verdict"] == "block"
    assert items["a3"]["decision_id"] == decision_id
    # The owner is untouched.
    assert items["o1"]["verdict"] == "normal"
    assert items["o1"]["decision_id"] is None


def test_block_marked_normal_returns_to_normal(client):
    decision_id = blocked_scenario(client)
    assert client.post(f"/v1/decisions/{decision_id}/normal", headers=AUTH_A).status_code == 200
    items = by_id(activity(client))
    assert items["a2"]["verdict"] == "normal"
    assert items["a2"]["decision_id"] == decision_id
    assert items["a3"]["verdict"] == "normal"
    assert items["a3"]["decision_id"] is None


def test_ai_alert_is_review(make_client):  # noqa: F811
    client, _ = make_client(FakeJudge(decision="alert"))
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/invoices/{id}", **NEW_NETWORK)])
    [decision] = decisions(client)
    item = by_id(activity(client))["o1"]
    assert item["verdict"] == "review"
    assert item["decision_id"] == decision["id"]


def test_ai_allow_is_normal_with_decision(make_client):  # noqa: F811
    client, _ = make_client(FakeJudge(decision="allow"))
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/invoices/{id}", **NEW_NETWORK)])
    [decision] = decisions(client)
    item = by_id(activity(client))["o1"]
    assert item["verdict"] == "normal"
    assert item["decision_id"] == decision["id"]


def test_newest_first_and_limit(client):
    blocked_scenario(client)
    assert [item["event_id"] for item in activity(client, limit=4)] == ["a3", "a2", "a1", "o1"]
    assert len(activity(client, limit=200)) == 32  # all of them


def test_default_limit_is_50(client):
    events = [make_event(f"e{i}", NOW - timedelta(minutes=i)) for i in range(60)]
    post_events(client, events)
    items = activity(client)
    assert len(items) == 50
    assert items[0]["event_id"] == "e0"


def test_limit_bounds(client):
    for limit in (0, 201):
        response = client.get("/v1/activity", headers=AUTH_A, params={"limit": limit})
        assert response.status_code == 422
    assert len(activity(client, limit=1)) == 0


def test_client_isolation(client):
    blocked_scenario(client)
    assert activity(client, headers=AUTH_B) == []
    post_events(client, [make_event("b1", NOW)], headers=AUTH_B)
    assert [item["event_id"] for item in activity(client, headers=AUTH_B)] == ["b1"]


def test_requires_token(client):
    assert client.get("/v1/activity").status_code == 401
    bad = client.get("/v1/activity", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    assert bad.json() == {"error": "unauthorized"}


def test_status_tells_served_from_rejected(client):
    blocked_scenario(client)
    post_events(client, [make_event("a4", NOW + timedelta(seconds=9), route="/invoices", status=403, **ATTACKER)])
    items = by_id(activity(client))
    assert items["a2"]["status"] == 200
    assert items["a4"]["status"] == 403
    assert items["a4"]["verdict"] == "block"


def test_blocked_ip_with_another_session_is_block(client):
    decision_id = blocked_scenario(client)
    other = {**ATTACKER, "session_id": "s_evil_2", "device_id": "d_evil_2"}
    post_events(client, [make_event("a5", NOW + timedelta(seconds=9), route="/login", status=403, **other)])
    item = by_id(activity(client))["a5"]
    assert item["verdict"] == "block"
    assert item["decision_id"] == decision_id


def test_session_only_block_does_not_cover_the_owner_ip(make_client):  # noqa: F811
    client, _ = make_client(FakeJudge(decision="block", rationale="Script en la red del dueño."))
    seed_owner_history(client)
    post_events(client, [make_event("b1", NOW, route="/invoices", session_id="s_bot", device_id="d_bot")])
    assert by_id(activity(client))["b1"]["verdict"] == "block"
    post_events(client, [make_event("o9", NOW + timedelta(seconds=5), route="/invoices", session_id="s_owner_2")])
    item = by_id(activity(client))["o9"]
    assert item["verdict"] == "normal"
    assert item["decision_id"] is None
