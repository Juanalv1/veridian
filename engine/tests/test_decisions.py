from datetime import timedelta

from conftest import AUTH_A, AUTH_B, NOW, days_ago, make_event, post_events

ATTACKER = {"session_id": "s_evil", "ip": "203.0.113.9", "device_id": "d_evil"}


def seed_owner_history(client, method="GET", route="/invoices"):
    events = []
    for day in range(1, 15):
        events.append(make_event(f"h{day}a", days_ago(day, hour=9), method=method, route=route))
        events.append(make_event(f"h{day}b", days_ago(day, hour=13), route="/invoices/{id}"))
    assert post_events(client, events).json() == {"accepted": 28}


def attack(client, prefix="a", **overrides):
    fields = {**ATTACKER, **overrides}
    events = [
        make_event(f"{prefix}1", NOW - timedelta(seconds=30), route="/login", **fields),
        make_event(f"{prefix}2", NOW, method="POST", route="/payouts/bank-account", **fields),
    ]
    return post_events(client, events)


def decisions(client, headers=AUTH_A):
    return client.get("/v1/decisions", headers=headers).json()["decisions"]


def blocklist(client, headers=AUTH_A, since=None):
    params = {} if since is None else {"since": since}
    return client.get("/v1/blocklist", headers=headers, params=params)


def test_acceptance_scenario(client):
    seed_owner_history(client)

    # The owner keeps using invoices from the usual network: no decision.
    owner_now = [make_event("o1", NOW - timedelta(minutes=1), route="/invoices/{id}")]
    assert post_events(client, owner_now).json() == {"accepted": 1}
    assert decisions(client) == []
    assert blocklist(client).json() == {"version": 0, "users": [], "sessions": [], "ips": []}

    # The attacker comes from a new network and changes the bank account.
    assert attack(client).json() == {"accepted": 2}
    [decision] = decisions(client)
    assert decision["decision"] == "block"
    assert decision["user_id"] == "u_42"
    assert decision["ts"] == "2026-10-26T12:00:00Z"
    assert decision["event_ids"] == ["a2"]
    assert decision["signals"] == {
        "new_network": True,
        "new_device": True,
        "route": "/payouts/bank-account",
        "route_class": "critical",
        "route_never_used": True,
        "path": ["/login", "/payouts/bank-account"],
    }
    assert decision["rationale"]
    assert decision["id"].startswith("d_")

    listed = blocklist(client).json()
    assert listed == {"version": 1, "users": [], "sessions": ["s_evil"], "ips": ["203.0.113.9"]}
    profile = client.get("/v1/users/u_42/profile", headers=AUTH_A).json()
    assert profile["state"] == "blocked"

    # The same session hitting the critical route again does not create a second decision.
    again = [make_event("a3", NOW + timedelta(seconds=5), method="POST", route="/payouts/bank-account", **ATTACKER)]
    assert post_events(client, again).json() == {"accepted": 1}
    assert len(decisions(client)) == 1
    assert blocklist(client).json()["version"] == 1

    # Marking it normal removes the block and bumps the version.
    response = client.post(f"/v1/decisions/{decision['id']}/normal", headers=AUTH_A)
    assert response.status_code == 200
    listed = blocklist(client).json()
    assert listed == {"version": 2, "users": [], "sessions": [], "ips": []}
    assert client.get("/v1/users/u_42/profile", headers=AUTH_A).json()["state"] == "normal"

    # The middleware is already up to date.
    not_modified = blocklist(client, since=2)
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert blocklist(client, since=1).json()["version"] == 2


def test_get_history_is_not_prior_use_of_post(client):
    seed_owner_history(client, method="GET", route="/payouts/bank-account")
    attack(client)
    assert [d["decision"] for d in decisions(client)] == ["block"]


def test_known_network_does_not_block(client):
    seed_owner_history(client)
    attack(client, ip="190.202.14.99")
    assert decisions(client) == []


def test_route_already_used_does_not_block(client):
    seed_owner_history(client, method="POST", route="/payouts/bank-account")
    attack(client)
    assert decisions(client) == []


def test_non_critical_route_from_new_network_does_not_block(client):
    seed_owner_history(client)
    post_events(client, [make_event("x1", NOW, route="/invoices/export", **ATTACKER)])
    assert decisions(client) == []


def test_events_in_block_decision_are_not_learned(client, clock):
    seed_owner_history(client)
    attack(client)
    clock.now = NOW + timedelta(days=1)
    profile = client.get("/v1/users/u_42/profile", headers=AUTH_A).json()
    assert "POST /payouts/bank-account" not in profile["routes"]
    assert "203.0.113.0/24" in profile["networks"]  # the /login event is not part of the decision


def test_events_of_decision_marked_normal_are_learned_immediately(client):
    seed_owner_history(client)
    attack(client)
    [decision] = decisions(client)
    client.post(f"/v1/decisions/{decision['id']}/normal", headers=AUTH_A)
    profile = client.get("/v1/users/u_42/profile", headers=AUTH_A).json()
    assert "POST /payouts/bank-account" in profile["routes"]
    assert "203.0.113.0/24" in profile["networks"]


def test_marking_normal_twice_does_not_bump_version_again(client):
    seed_owner_history(client)
    attack(client)
    [decision] = decisions(client)
    client.post(f"/v1/decisions/{decision['id']}/normal", headers=AUTH_A)
    client.post(f"/v1/decisions/{decision['id']}/normal", headers=AUTH_A)
    assert blocklist(client).json()["version"] == 2


def test_unknown_decision_returns_404(client):
    assert client.post("/v1/decisions/d_999/normal", headers=AUTH_A).status_code == 404


def test_decisions_are_newest_first_and_limited(client):
    seed_owner_history(client)
    for index in range(3):
        attack(client, prefix=f"s{index}", session_id=f"s_evil_{index}")
    listed = decisions(client)
    assert [d["event_ids"] for d in listed] == [["s22"], ["s12"], ["s02"]]
    limited = client.get("/v1/decisions", headers=AUTH_A, params={"limit": 2}).json()["decisions"]
    assert len(limited) == 2
    assert client.get("/v1/decisions", headers=AUTH_A, params={"limit": 0}).status_code == 422


def test_client_isolation(client):
    seed_owner_history(client)
    attack(client)
    [decision] = decisions(client)
    assert decisions(client, headers=AUTH_B) == []
    assert blocklist(client, headers=AUTH_B).json() == {"version": 0, "users": [], "sessions": [], "ips": []}
    assert client.get("/v1/users/u_42/profile", headers=AUTH_B).json()["state"] == "normal"
    assert client.post(f"/v1/decisions/{decision['id']}/normal", headers=AUTH_B).status_code == 404
    assert blocklist(client).json()["version"] == 1


def test_decision_and_blocklist_endpoints_require_token(client):
    assert client.get("/v1/decisions").status_code == 401
    assert client.get("/v1/blocklist").status_code == 401
    assert client.post("/v1/decisions/d_1/normal").status_code == 401


def test_decision_status_follows_mark_normal(client):
    seed_owner_history(client)
    attack(client)
    [decision] = decisions(client)
    assert decision["status"] == "open"
    client.post(f"/v1/decisions/{decision['id']}/normal", headers=AUTH_A)
    [decision] = decisions(client)
    assert decision["status"] == "marked_normal"
