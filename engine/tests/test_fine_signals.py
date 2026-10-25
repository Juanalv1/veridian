from datetime import timedelta

import pytest
from conftest import AUTH_A, NOW, days_ago, make_event, make_settings, post_events
from fastapi.testclient import TestClient
from test_ai_wiring import NEW_NETWORK, FakeJudge
from test_decisions import blocklist, decisions, seed_owner_history

from veridian_engine.api import create_app
from veridian_engine.settings import Settings


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


def burst(prefix, count, end, seconds=60, **overrides):
    """count events of one session spread evenly over the `seconds` up to `end`."""
    step = seconds / count
    fields = {"session_id": "s_bot", "route": "/invoices/{id}", **overrides}
    return [
        make_event(f"{prefix}{index}", end - timedelta(seconds=step * (count - 1 - index)), **fields)
        for index in range(count)
    ]


def test_burst_from_owner_network_and_device_is_judged_and_blocks_only_the_session(judged_client):
    judge = FakeJudge(decision="block", rationale="Ritmo de script.")
    client = judged_client(judge)
    seed_owner_history(client)

    assert post_events(client, burst("b", 40, NOW)).status_code == 202

    assert len(judge.calls) == 1
    signals, profile = judge.calls[0]
    assert signals["new_network"] is False
    assert signals["new_device"] is False
    assert signals["rate_spike"] is True
    assert signals["rate_per_min"] == 40
    assert signals["usual_max_rate_per_min"] == 1
    assert profile["usual_max_rate_per_min"] == 1
    [decision] = decisions(client)
    assert decision["decision"] == "block"
    listed = blocklist(client).json()
    assert listed["sessions"] == ["s_bot"]
    assert listed["ips"] == []
    assert listed["version"] == 1


def test_owner_at_normal_pace_is_not_judged(judged_client):
    judge = FakeJudge()
    client = judged_client(judge)
    seed_owner_history(client)
    events = [
        make_event(f"o{index}", NOW - timedelta(minutes=5 - index), route="/invoices/{id}") for index in range(5)
    ]
    post_events(client, events)
    assert judge.calls == []
    assert decisions(client) == []


def test_burst_below_the_floor_is_not_a_spike(judged_client):
    judge = FakeJudge()
    client = judged_client(judge)
    seed_owner_history(client)
    post_events(client, burst("b", 30, NOW))
    assert judge.calls == []


def test_user_with_a_high_usual_rate_does_not_spike(judged_client):
    # Seeded without a judge: an unjudged spike leaves no decision, so all 40 are learned.
    seeder = judged_client(None)
    seed_owner_history(seeder)
    post_events(seeder, burst("fast", 40, days_ago(2, hour=9), session_id="s_fast_old"))
    judge = FakeJudge()
    client = judged_client(judge)
    profile = client.get("/v1/users/u_42/profile", headers=AUTH_A).json()
    assert profile["usual_max_rate_per_min"] == 40

    post_events(client, burst("b", 50, NOW))
    assert judge.calls == []
    assert decisions(client) == []


def test_rate_settings_are_injectable(judged_client):
    judge = FakeJudge()
    client = judged_client(judge, rate_factor=2.0, rate_floor=5)
    seed_owner_history(client)
    post_events(client, burst("b", 6, NOW))
    assert len(judge.calls) == 1
    assert judge.calls[0][0]["rate_spike"] is True


def test_spiking_session_judged_earlier_is_judged_again_only_on_its_first_spike(judged_client):
    judge = FakeJudge(decision="alert")
    client = judged_client(judge)
    seed_owner_history(client)
    first = make_event("o0", NOW - timedelta(minutes=5), session_id="s_bot", **NEW_NETWORK)
    post_events(client, [first])
    assert len(judge.calls) == 1
    assert judge.calls[0][0]["rate_spike"] is False

    post_events(client, burst("b", 40, NOW - timedelta(seconds=10), **NEW_NETWORK))
    assert len(judge.calls) == 2
    assert judge.calls[1][0]["rate_spike"] is True

    post_events(client, burst("c", 10, NOW, seconds=10, **NEW_NETWORK))
    assert len(judge.calls) == 2
    assert [d["decision"] for d in decisions(client)] == ["alert"]


def test_ai_block_on_a_new_network_still_blocks_session_and_ip(judged_client):
    judge = FakeJudge(decision="block", rationale="Red nueva y ritmo de script.")
    client = judged_client(judge)
    seed_owner_history(client)
    post_events(client, burst("b", 40, NOW, **NEW_NETWORK))
    listed = blocklist(client).json()
    assert listed["sessions"] == ["s_bot"]
    assert listed["ips"] == ["200.44.10.5"]


def test_path_and_hour_signals_reach_the_judge(judged_client):
    judge = FakeJudge()
    client = judged_client(judge)
    seed_owner_history(client)
    events = [
        make_event("o1", NOW - timedelta(seconds=20), route="/invoices/{id}", session_id="s_x"),
        make_event("o2", NOW, route="/invoices/{id}", session_id="s_x", device_id="d_new"),
    ]
    post_events(client, events)
    [(signals, _)] = judge.calls
    assert signals["new_transition"] is True  # history only goes /invoices <-> /invoices/{id}
    assert signals["unusual_hour"] is True  # 12 UTC, history is at 9 and 13
    assert signals["rate_per_min"] == 2
    assert signals["rate_spike"] is False


def test_known_transition_at_a_usual_hour_is_not_flagged(judged_client):
    judge = FakeJudge()
    client = judged_client(judge)
    seed_owner_history(client)
    post_events(client, [make_event("h_noon", days_ago(3, hour=12))])
    events = [
        make_event("o1", NOW - timedelta(seconds=20), route="/invoices", session_id="s_x"),
        make_event("o2", NOW, route="/invoices/{id}", session_id="s_x", device_id="d_new"),
    ]
    post_events(client, events)
    [(signals, _)] = judge.calls
    assert signals["new_transition"] is False
    assert signals["unusual_hour"] is False


def test_new_transition_and_unusual_hour_alone_do_not_call_the_judge(judged_client):
    judge = FakeJudge()
    client = judged_client(judge)
    seed_owner_history(client)
    events = [
        make_event("o1", NOW - timedelta(seconds=20), route="/invoices/{id}", session_id="s_x"),
        make_event("o2", NOW, route="/invoices/{id}", session_id="s_x"),
        make_event("o3", NOW.replace(hour=3), route="/invoices", session_id="s_y"),
    ]
    post_events(client, events)
    assert judge.calls == []


def test_unusual_hour_is_false_without_history(judged_client):
    judge = FakeJudge()
    client = judged_client(judge)
    post_events(client, [make_event("o1", NOW)])
    [(signals, profile)] = judge.calls
    assert signals["unusual_hour"] is False
    assert signals["new_transition"] is False
    assert signals["usual_max_rate_per_min"] == 0
    assert profile["usual_max_rate_per_min"] == 0


def test_profile_endpoint_includes_usual_max_rate(judged_client):
    client = judged_client(None)
    assert client.get("/v1/users/u_42/profile", headers=AUTH_A).json()["usual_max_rate_per_min"] == 0
    seed_owner_history(client)
    assert client.get("/v1/users/u_42/profile", headers=AUTH_A).json()["usual_max_rate_per_min"] == 1


def test_rate_settings_from_env(monkeypatch):
    monkeypatch.setenv("VERIDIAN_TOKEN", "dev")
    monkeypatch.delenv("VERIDIAN_RATE_FACTOR", raising=False)
    monkeypatch.delenv("VERIDIAN_RATE_FLOOR", raising=False)
    settings = Settings.from_env()
    assert (settings.rate_factor, settings.rate_floor) == (3.0, 30)
    monkeypatch.setenv("VERIDIAN_RATE_FACTOR", "2.5")
    monkeypatch.setenv("VERIDIAN_RATE_FLOOR", "12")
    settings = Settings.from_env()
    assert (settings.rate_factor, settings.rate_floor) == (2.5, 12)
