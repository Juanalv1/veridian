from datetime import timedelta

import pytest
from conftest import NOW, make_event, make_settings, post_events
from fastapi.testclient import TestClient
from test_decisions import ATTACKER, attack, blocklist, decisions, seed_owner_history

from veridian_engine import db
from veridian_engine.ai.judge import Judgment
from veridian_engine.api import create_app
from veridian_engine.decisions import blocklist as read_blocklist
from veridian_engine.rules import RATIONALE
from veridian_engine.settings import Settings

NEW_NETWORK = {"ip": "200.44.10.5"}


class FakeJudge:
    def __init__(self, decision="alert", rationale="Red nueva, pero el resto coincide con el dueño.", on_call=None):
        self.decision = decision
        self.rationale = rationale
        self.on_call = on_call
        self.calls = []

    def __call__(self, signals: dict, profile: dict) -> Judgment:
        self.calls.append((signals, profile))
        if self.on_call:
            self.on_call(signals, profile)
        return Judgment(decision=self.decision, rationale=self.rationale)


def raising_judge(signals, profile):
    raise RuntimeError("boom")


@pytest.fixture
def make_client(tmp_path, clock):
    clients = []

    def factory(judge):
        settings = make_settings(tmp_path, clock, judge=judge)
        test_client = TestClient(create_app(settings))
        clients.append(test_client)
        return test_client, settings

    yield factory
    for test_client in clients:
        test_client.close()


def test_owner_on_new_network_gets_one_ai_decision(make_client):
    judge = FakeJudge(decision="alert")
    client, _ = make_client(judge)
    seed_owner_history(client)

    response = post_events(client, [make_event("o1", NOW, route="/invoices/{id}", **NEW_NETWORK)])

    assert response.status_code == 202
    assert len(judge.calls) == 1
    signals, profile = judge.calls[0]
    assert signals["new_network"] is True
    assert signals["new_device"] is False
    assert signals["route_class"] == "normal"
    assert profile["user_id"] == "u_42"
    [decision] = decisions(client)
    assert decision["decision"] == "alert"
    assert decision["rationale"] == judge.rationale
    assert decision["event_ids"] == ["o1"]
    assert blocklist(client).json()["version"] == 0


def test_attacker_block_is_sync_and_rationale_replaced_by_ai(make_client):
    seen = {}

    def check_blocklist(signals, profile):
        with db.connect(settings.db_path) as conn:
            seen["blocklist"] = read_blocklist(conn, "client_a")

    judge = FakeJudge(decision="block", rationale="La IA vio un atacante.", on_call=check_blocklist)
    client, settings = make_client(judge)
    seed_owner_history(client)

    assert attack(client).status_code == 202

    assert seen["blocklist"]["sessions"] == ["s_evil"]
    assert seen["blocklist"]["version"] == 1
    assert len(judge.calls) == 1
    assert judge.calls[0][0]["route_class"] == "critical"
    [decision] = decisions(client)
    assert decision["decision"] == "block"
    assert decision["rationale"] == "La IA vio un atacante."
    assert blocklist(client).json()["version"] == 1


def test_no_judge_keeps_rule_only_behavior(make_client):
    client, _ = make_client(None)
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/invoices/{id}", **NEW_NETWORK)])
    assert decisions(client) == []
    attack(client)
    [decision] = decisions(client)
    assert decision["decision"] == "block"
    assert decision["rationale"] == RATIONALE.format(
        route_key="POST /payouts/bank-account", network="203.0.113.0/24"
    )


def test_judge_raising_keeps_ingestion_and_deterministic_rationale(make_client):
    client, _ = make_client(raising_judge)
    seed_owner_history(client)
    assert attack(client).status_code == 202
    [decision] = decisions(client)
    assert decision["decision"] == "block"
    assert decision["rationale"].startswith("La ruta crítica POST /payouts/bank-account")


def test_no_deviation_does_not_call_judge(make_client):
    judge = FakeJudge()
    client, _ = make_client(judge)
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/invoices/{id}")])
    assert judge.calls == []
    assert decisions(client) == []


def test_empty_device_id_is_not_a_deviation(make_client):
    judge = FakeJudge()
    client, _ = make_client(judge)
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/invoices/{id}", device_id="")])
    assert judge.calls == []


def test_new_device_is_a_deviation(make_client):
    judge = FakeJudge()
    client, _ = make_client(judge)
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/invoices/{id}", device_id="d_new")])
    assert len(judge.calls) == 1
    assert judge.calls[0][0]["new_device"] is True


def test_never_used_sensitive_route_is_a_deviation(make_client):
    judge = FakeJudge(decision="allow")
    client, _ = make_client(judge)
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/invoices/export")])
    assert len(judge.calls) == 1
    assert judge.calls[0][0]["route_never_used"] is True
    assert [d["decision"] for d in decisions(client)] == ["allow"]


def test_never_used_normal_route_is_not_a_deviation(make_client):
    judge = FakeJudge()
    client, _ = make_client(judge)
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/reports")])
    assert judge.calls == []


def test_repeated_deviation_in_a_judged_session_is_not_judged_again(make_client):
    judge = FakeJudge(decision="alert")
    client, _ = make_client(judge)
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/invoices/{id}", **NEW_NETWORK)])
    post_events(client, [make_event("o2", NOW + timedelta(seconds=5), route="/invoices", **NEW_NETWORK)])
    post_events(client, [make_event("o3", NOW + timedelta(seconds=10), route="/invoices", **NEW_NETWORK)])
    assert len(judge.calls) == 1
    assert [d["decision"] for d in decisions(client)] == ["alert"]


def test_never_used_sensitive_route_is_judged_again_in_a_judged_session(make_client):
    judge = FakeJudge(decision="alert")
    client, _ = make_client(judge)
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/invoices/{id}", **NEW_NETWORK)])
    assert [d["decision"] for d in decisions(client)] == ["alert"]

    judge.decision = "block"
    post_events(client, [make_event("o3", NOW + timedelta(seconds=10), route="/invoices/export", **NEW_NETWORK)])
    assert len(judge.calls) == 2
    assert [d["decision"] for d in decisions(client)] == ["block", "alert"]
    listed = blocklist(client).json()
    assert listed["sessions"] == ["s_owner"]
    assert listed["ips"] == ["200.44.10.5"]
    assert listed["version"] == 1


def test_blocked_session_gets_no_more_decisions(make_client):
    judge = FakeJudge(decision="block", rationale="Atacante.")
    client, _ = make_client(judge)
    seed_owner_history(client)
    attack(client)
    calls = len(judge.calls)
    judge.decision = "alert"
    post_events(client, [make_event("a9", NOW + timedelta(seconds=5), route="/invoices", **ATTACKER)])
    assert len(judge.calls) == calls
    assert [d["decision"] for d in decisions(client)] == ["block"]


def test_one_judge_call_per_session_per_batch(make_client):
    judge = FakeJudge()
    client, _ = make_client(judge)
    seed_owner_history(client)
    events = [
        make_event("o1", NOW - timedelta(seconds=10), route="/invoices", session_id="s_trip", **NEW_NETWORK),
        make_event("o2", NOW, route="/invoices/{id}", session_id="s_trip", **NEW_NETWORK),
    ]
    post_events(client, events)
    assert len(judge.calls) == 1
    assert judge.calls[0][0]["path"] == ["/invoices", "/invoices/{id}"]


def test_judge_from_env_depends_on_api_key(monkeypatch):
    from veridian_engine.ai import judge as judge_module

    monkeypatch.setenv("VERIDIAN_TOKEN", "dev")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert Settings.from_env().judge is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert Settings.from_env().judge is judge_module.judge


def test_ai_decision_starts_open(make_client):
    judge = FakeJudge(decision="alert")
    client, _ = make_client(judge)
    seed_owner_history(client)
    post_events(client, [make_event("o1", NOW, route="/invoices/{id}", **NEW_NETWORK)])
    [decision] = decisions(client)
    assert decision["status"] == "open"


def test_a_background_step_up_is_enforced_as_a_block(make_client):
    """Found in the behavior evaluation: a slow theft (new network and device, a few reads, then an
    export) got step_up from the AI 3 of 3 times, and nothing enforced it. The middleware has no
    second factor, so a step_up stops the session like a block, as POST /v1/check already does."""
    judge = FakeJudge(decision="step_up", rationale="Red y dispositivo nuevos que terminan exportando.")
    client, _ = make_client(judge)
    seed_owner_history(client)
    thief = {"ip": "203.0.113.70", "device_id": "d_thief", "session_id": "s_slow"}

    post_events(client, [make_event("t1", NOW, route="/invoices/{id}", **thief)])

    [decision] = decisions(client)
    assert decision["decision"] == "block"
    assert decision["rationale"] == judge.rationale
    listed = blocklist(client).json()
    assert listed["sessions"] == ["s_slow"] and listed["ips"] == ["203.0.113.70"]


def test_a_background_alert_is_still_only_an_alert(make_client):
    judge = FakeJudge(decision="alert")
    client, _ = make_client(judge)
    seed_owner_history(client)

    post_events(client, [make_event("o1", NOW, route="/invoices/{id}", **NEW_NETWORK)])

    assert [d["decision"] for d in decisions(client)] == ["alert"]
    assert blocklist(client).json()["sessions"] == []
