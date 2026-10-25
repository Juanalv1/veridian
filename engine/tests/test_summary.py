from datetime import timedelta

import pytest
from conftest import AUTH_A, AUTH_B, NOW, days_ago, iso, make_event, make_settings, post_events, as_panel
from fastapi.testclient import TestClient
from test_ai_wiring import NEW_NETWORK, FakeJudge
from test_decisions import ATTACKER, attack, decisions, seed_owner_history

from veridian_engine.api import create_app

EMPTY_DECISIONS = {"allow": 0, "alert": 0, "step_up": 0, "block": 0}
NO_BLOCKS = {"decisions": 0, "sessions": 0, "ips": 0}


@pytest.fixture
def judged_client(tmp_path, clock):
    clients = []

    def factory(judge, **overrides):
        test_client = TestClient(create_app(make_settings(tmp_path, clock, judge=judge, **overrides)))
        clients.append(test_client)
        return test_client

    yield factory
    for test_client in clients:
        test_client.close()


def summary(client, headers=AUTH_A, **params):
    return client.get("/v1/summary", headers=as_panel(client, headers), params=params)


def today(client, headers=AUTH_A):
    """The demo day: [NOW - 12 h, NOW + 1 h), so events at NOW count."""
    return summary(client, headers, since=iso(NOW - timedelta(hours=12)), until=iso(NOW + timedelta(hours=1))).json()


def users(client, headers=AUTH_A):
    return client.get("/v1/users", headers=as_panel(client, headers))


# --- summary ---


def test_summary_of_an_empty_client(client):
    body = summary(client).json()
    assert body == {
        "since": "2026-10-25T12:00:00Z",
        "until": "2026-10-26T12:00:00Z",
        "accesses": 0,
        "users": 0,
        "reviewed_by_ai": 0,
        "decisions": EMPTY_DECISIONS,
        "blocks_active": NO_BLOCKS,
        "by_hour": [],
    }


def test_summary_defaults_to_the_last_24_hours(client, clock):
    events = [
        make_event("old", NOW - timedelta(hours=25)),
        make_event("in1", NOW - timedelta(hours=23)),
        make_event("in2", NOW - timedelta(minutes=5), user_id="u_17", session_id="s_17"),
    ]
    post_events(client, events)
    clock.now = NOW + timedelta(minutes=1)

    body = summary(client).json()

    assert body["since"] == "2026-10-25T12:01:00Z"
    assert body["until"] == "2026-10-26T12:01:00Z"
    assert body["accesses"] == 2
    assert body["users"] == 2


def test_summary_filters_by_explicit_range_half_open(client):
    events = [
        make_event("before", NOW - timedelta(hours=2, seconds=1)),
        make_event("at_since", NOW - timedelta(hours=2)),
        make_event("inside", NOW - timedelta(hours=1), user_id="u_17", session_id="s_17"),
        make_event("at_until", NOW),
    ]
    post_events(client, events)

    body = summary(client, since=iso(NOW - timedelta(hours=2)), until=iso(NOW)).json()

    assert body["since"] == "2026-10-26T10:00:00Z"
    assert body["until"] == "2026-10-26T12:00:00Z"
    assert body["accesses"] == 2
    assert body["users"] == 2


@pytest.mark.parametrize(
    "params",
    [
        {"since": iso(NOW), "until": iso(NOW)},
        {"since": iso(NOW), "until": iso(NOW - timedelta(hours=1))},
        {"since": "yesterday"},
    ],
)
def test_summary_rejects_a_bad_range(client, params):
    assert summary(client, **params).status_code == 422


def test_summary_counts_decisions_and_active_blocks(client):
    seed_owner_history(client)
    attack(client)

    body = today(client)
    assert body["decisions"] == {"allow": 0, "alert": 0, "step_up": 0, "block": 1}
    assert body["blocks_active"] == {"decisions": 1, "sessions": 1, "ips": 1}
    # Without a judge nothing reaches the AI.
    assert body["reviewed_by_ai"] == 0

    [decision] = decisions(client)
    client.post(f"/v1/decisions/{decision['id']}/normal", headers=as_panel(client, AUTH_A))

    body = today(client)
    # The decision still happened in the range; it is just no longer active.
    assert body["decisions"]["block"] == 1
    assert body["blocks_active"] == NO_BLOCKS


def test_summary_decisions_outside_the_range_are_not_counted(client):
    seed_owner_history(client)
    attack(client)

    body = summary(client, since=iso(NOW - timedelta(hours=3)), until=iso(NOW - timedelta(hours=1))).json()

    assert body["decisions"] == EMPTY_DECISIONS
    # blocks_active is the current state, not the range's.
    assert body["blocks_active"] == {"decisions": 1, "sessions": 1, "ips": 1}


def test_summary_by_hour_is_ordered_and_counts_blocks(client):
    events = [
        make_event("e1", NOW - timedelta(hours=2, minutes=10)),
        make_event("e2", NOW - timedelta(hours=2, minutes=5)),
        make_event("e3", NOW - timedelta(minutes=30), route="/invoices/{id}"),
    ]
    seed_owner_history(client)
    post_events(client, events)
    attack(client)

    body = today(client)

    assert body["by_hour"] == [
        {"hour": "2026-10-26T09:00:00Z", "accesses": 2, "blocks": 0},
        {"hour": "2026-10-26T11:00:00Z", "accesses": 2, "blocks": 0},
        {"hour": "2026-10-26T12:00:00Z", "accesses": 1, "blocks": 1},
    ]
    assert body["accesses"] == 5


def test_reviewed_by_ai_counts_real_judge_calls(judged_client):
    judge = FakeJudge(decision="alert")
    client = judged_client(judge)
    seed_owner_history(client)

    post_events(client, [make_event("o1", NOW - timedelta(seconds=10), route="/invoices/{id}", **NEW_NETWORK)])
    # The same deviating session keeps sending batches: the judge is not asked again.
    post_events(client, [make_event("o2", NOW, route="/invoices/{id}", **NEW_NETWORK)])

    assert len(judge.calls) == 1
    body = today(client)
    assert body["reviewed_by_ai"] == 1
    assert body["accesses"] == 2
    assert body["decisions"]["alert"] == 1


def test_reviewed_by_ai_follows_the_judged_event_ts(judged_client):
    judge = FakeJudge(decision="alert")
    client = judged_client(judge)
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW - timedelta(minutes=5), route="/invoices/{id}", **NEW_NETWORK)])

    assert len(judge.calls) == 1
    later = summary(client, since=iso(NOW - timedelta(minutes=4)), until=iso(NOW + timedelta(hours=1)))
    assert later.json()["reviewed_by_ai"] == 0
    around = summary(client, since=iso(NOW - timedelta(minutes=6)), until=iso(NOW - timedelta(minutes=4)))
    assert around.json()["reviewed_by_ai"] == 1


def test_summary_is_isolated_per_client(client):
    seed_owner_history(client)
    attack(client)

    body = today(client, headers=AUTH_B)

    assert body["accesses"] == 0
    assert body["decisions"] == EMPTY_DECISIONS
    assert body["blocks_active"] == NO_BLOCKS


def test_summary_requires_a_token(client):
    response = client.get("/v1/summary")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


# --- users ---


def test_users_of_an_empty_client(client):
    assert users(client).json() == {"users": []}


def test_users_lists_blocked_first_then_by_last_seen(client):
    seed_owner_history(client)
    others = [
        make_event("x1", days_ago(2, hour=10), user_id="u_17", session_id="s_17", ip="10.0.0.1", device_id="d_17"),
        make_event("x2", days_ago(3, hour=10), user_id="u_17", session_id="s_17b", ip="10.9.0.1", device_id="d_17"),
        make_event("x3", NOW - timedelta(hours=1), user_id="u_17", session_id="s_17c", ip="10.0.0.2", device_id=""),
        make_event("y1", NOW - timedelta(hours=3), user_id="u_99", session_id="s_99", ip="10.5.0.1", device_id="d_99"),
    ]
    post_events(client, others)
    attack(client)

    body = users(client).json()

    assert body == {
        "users": [
            # u_42's last access is the attacker's, at NOW.
            {
                "user_id": "u_42",
                "state": "blocked",
                "history_days": 14,
                "networks": 1,
                "devices": 1,
                "last_seen": "2026-10-26T12:00:00Z",
                "open_blocks": 1,
            },
            {
                "user_id": "u_17",
                "state": "normal",
                "history_days": 3,
                "networks": 2,
                "devices": 1,
                "last_seen": "2026-10-26T11:00:00Z",
                "open_blocks": 0,
            },
            {
                "user_id": "u_99",
                "state": "normal",
                "history_days": 1,
                "networks": 1,
                "devices": 1,
                "last_seen": "2026-10-26T09:00:00Z",
                "open_blocks": 0,
            },
        ]
    }


def test_users_blocked_user_returns_to_normal_after_mark_normal(client):
    seed_owner_history(client)
    attack(client)
    post_events(client, [make_event("z1", NOW + timedelta(minutes=1), user_id="u_17", session_id="s_17")])
    assert [user["user_id"] for user in users(client).json()["users"]] == ["u_42", "u_17"]

    [decision] = decisions(client)
    client.post(f"/v1/decisions/{decision['id']}/normal", headers=as_panel(client, AUTH_A))

    listed = users(client).json()["users"]
    assert [user["user_id"] for user in listed] == ["u_17", "u_42"]
    assert listed[1]["state"] == "normal"
    assert listed[1]["open_blocks"] == 0


def test_users_are_isolated_per_client(client):
    seed_owner_history(client)
    post_events(client, [make_event("b1", NOW, user_id="u_b", **ATTACKER)], headers=AUTH_B)

    assert [user["user_id"] for user in users(client).json()["users"]] == ["u_42"]
    assert [user["user_id"] for user in users(client, headers=AUTH_B).json()["users"]] == ["u_b"]


def test_users_requires_a_token(client):
    response = client.get("/v1/users")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
