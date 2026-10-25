"""Contract 9: POST /v1/check decides critical routes before the app executes them."""

import threading
import time
from datetime import timedelta

import pytest
from conftest import AUTH_A, AUTH_B, NOW, make_event, make_settings, post_events
from fastapi.testclient import TestClient
from test_ai_wiring import FakeJudge, raising_judge
from test_decisions import ATTACKER, blocklist, decisions, seed_owner_history

from veridian_engine import check as check_module
from veridian_engine.api import create_app

CRITICAL = {"method": "POST", "route": "/payouts/bank-account"}
# Known network (same IP as the owner's history), new device, own session.
GRAY = {"session_id": "s_gray", "device_id": "d_new", **CRITICAL}


@pytest.fixture
def check_client(tmp_path, clock):
    clients = []

    def factory(judge, **overrides):
        settings = make_settings(tmp_path, clock, judge=judge, **overrides)
        test_client = TestClient(create_app(settings))
        clients.append(test_client)
        return test_client

    yield factory
    for test_client in clients:
        test_client.close()


def check(client, event, headers=AUTH_A):
    return client.post("/v1/check", json={"event": event}, headers=headers)


def test_owner_on_known_network_and_device_is_allowed_without_ai(check_client):
    judge = FakeJudge()
    client = check_client(judge)
    seed_owner_history(client, **CRITICAL)

    response = check(client, make_event("c1", NOW, **CRITICAL))

    assert response.status_code == 200
    assert response.json() == {"verdict": "allow", "decision_id": None}
    assert judge.calls == []
    assert decisions(client) == []


def test_attacker_is_blocked_by_rule_and_ai_explains_afterwards(check_client):
    judge = FakeJudge(decision="block", rationale="La IA vio un atacante.")
    client = check_client(judge)
    seed_owner_history(client)

    response = check(client, make_event("c1", NOW, **ATTACKER, **CRITICAL))

    body = response.json()
    assert body["verdict"] == "block"
    [decision] = decisions(client)
    assert body["decision_id"] == decision["id"]
    assert decision["decision"] == "block"
    assert blocklist(client).json() == {
        "version": 1,
        "users": [],
        "sessions": ["s_evil"],
        "ips": ["203.0.113.9"],
    }
    # The background judge only replaced the rationale.
    assert len(judge.calls) == 1
    assert decision["rationale"] == "La IA vio un atacante."


@pytest.mark.parametrize("ai_decision", ["block", "step_up"])
def test_gray_case_blocked_by_ai(check_client, ai_decision):
    judge = FakeJudge(decision=ai_decision, rationale="Dispositivo nuevo cambiando la cuenta.")
    client = check_client(judge)
    seed_owner_history(client)

    response = check(client, make_event("c1", NOW, **GRAY))

    body = response.json()
    assert body["verdict"] == "block"
    assert len(judge.calls) == 1
    [decision] = decisions(client)
    assert body["decision_id"] == decision["id"]
    # step_up is recorded as a block: the request really was stopped.
    assert decision["decision"] == "block"
    assert decision["rationale"] == "Dispositivo nuevo cambiando la cuenta."
    # Known network: the owner shares the IP, so only the session is blocked.
    assert blocklist(client).json() == {"version": 1, "users": [], "sessions": ["s_gray"], "ips": []}


def test_gray_case_on_route_the_owner_used_is_judged(check_client):
    judge = FakeJudge(decision="block")
    client = check_client(judge)
    seed_owner_history(client, **CRITICAL)

    response = check(client, make_event("c1", NOW, **GRAY))

    assert response.json()["verdict"] == "block"
    assert judge.calls[0][0]["route_never_used"] is False
    assert judge.calls[0][0]["new_device"] is True


def test_gray_case_alert_allows_with_decision_id(check_client):
    judge = FakeJudge(decision="alert")
    client = check_client(judge)
    seed_owner_history(client)

    body = check(client, make_event("c1", NOW, **GRAY)).json()

    [decision] = decisions(client)
    assert body == {"verdict": "allow", "decision_id": decision["id"]}
    assert decision["decision"] == "alert"
    assert blocklist(client).json()["version"] == 0


def test_gray_case_allow_allows(check_client):
    judge = FakeJudge(decision="allow")
    client = check_client(judge)
    seed_owner_history(client)

    body = check(client, make_event("c1", NOW, **GRAY)).json()

    assert body["verdict"] == "allow"
    assert blocklist(client).json()["sessions"] == []


def test_slow_judge_falls_back_within_the_budget(check_client):
    release = threading.Event()
    judge = FakeJudge(decision="block", on_call=lambda signals, profile: release.wait(5))
    client = check_client(judge, sync_judge_timeout_s=0.1)
    seed_owner_history(client)

    started = time.monotonic()
    body = check(client, make_event("c1", NOW, **GRAY)).json()
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 2
    # The judge's deterministic fallback for a non-critical-rule deviation is alert.
    [decision] = decisions(client)
    assert body == {"verdict": "allow", "decision_id": decision["id"]}
    assert decision["decision"] == "alert"
    assert "La IA no estuvo disponible" in decision["rationale"]
    assert blocklist(client).json()["sessions"] == []


def test_judge_raising_allows(check_client):
    client = check_client(raising_judge)
    seed_owner_history(client)

    response = check(client, make_event("c1", NOW, **GRAY))

    assert response.status_code == 200
    assert response.json()["verdict"] == "allow"


def test_internal_error_fails_open(check_client, monkeypatch):
    client = check_client(FakeJudge(decision="block"))
    seed_owner_history(client)

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(check_module, "evaluate_event", broken)
    response = check(client, make_event("c1", NOW, **ATTACKER, **CRITICAL))

    assert response.status_code == 200
    assert response.json() == {"verdict": "allow", "decision_id": None}


def test_no_judge_is_rule_only(check_client):
    client = check_client(None)
    seed_owner_history(client)

    assert check(client, make_event("c1", NOW, **GRAY)).json() == {"verdict": "allow", "decision_id": None}
    attacker = check(client, make_event("c2", NOW, **ATTACKER, **CRITICAL)).json()
    assert attacker["verdict"] == "block"
    assert attacker["decision_id"] is not None


def test_recheck_of_same_event_answers_from_state(check_client):
    judge = FakeJudge(decision="block")
    client = check_client(judge)
    seed_owner_history(client)
    event = make_event("c1", NOW, **GRAY)

    first = check(client, event).json()
    second = check(client, event).json()

    assert second == first
    assert len(judge.calls) == 1


def test_recheck_of_allowed_event_stays_allowed(check_client):
    judge = FakeJudge()
    client = check_client(judge)
    seed_owner_history(client, **CRITICAL)
    event = make_event("c1", NOW, **CRITICAL)

    assert check(client, event).json() == check(client, event).json() == {"verdict": "allow", "decision_id": None}


def test_batch_resend_does_not_judge_again(check_client):
    judge = FakeJudge(decision="alert")
    client = check_client(judge)
    seed_owner_history(client)
    event = make_event("c1", NOW, **GRAY)
    check(client, event)

    response = post_events(client, [event])

    assert response.json() == {"accepted": 0}
    assert len(judge.calls) == 1
    assert len(decisions(client)) == 1


def test_later_request_of_blocked_session_is_blocked(check_client):
    judge = FakeJudge(decision="block")
    client = check_client(judge)
    seed_owner_history(client)
    first = check(client, make_event("c1", NOW, **GRAY)).json()

    later = check(client, make_event("c2", NOW + timedelta(seconds=5), **GRAY)).json()

    assert later == first
    assert len(judge.calls) == 1


def test_missing_token_is_401(check_client):
    client = check_client(None)
    response = client.post("/v1/check", json={"event": make_event("c1", NOW)})
    assert response.status_code == 401


def test_invalid_body_is_422(check_client):
    client = check_client(None)
    assert client.post("/v1/check", json={"event": {"id": "x"}}, headers=AUTH_A).status_code == 422
    assert client.post("/v1/check", json={}, headers=AUTH_A).status_code == 422


def test_clients_are_isolated(check_client):
    client = check_client(None)
    seed_owner_history(client)
    assert check(client, make_event("c1", NOW, **ATTACKER, **CRITICAL)).json()["verdict"] == "block"

    # Same event id and session in client B: evaluated on B's own state, not A's block.
    benign = make_event("c1", NOW, method="GET", route="/invoices", session_id="s_evil")
    assert check(client, benign, headers=AUTH_B).json() == {"verdict": "allow", "decision_id": None}
    assert decisions(client, headers=AUTH_B) == []
    assert blocklist(client, headers=AUTH_B).json()["sessions"] == []


def test_gray_case_uses_the_sync_judge_when_configured(check_client):
    background = FakeJudge(decision="alert", rationale="Juez lento.")
    fast = FakeJudge(decision="block", rationale="Juez rápido.")
    client = check_client(background, sync_judge=fast)
    seed_owner_history(client, method="POST", route="/payouts/bank-account")

    response = check(client, make_event("c1", NOW, **GRAY))

    assert response.json()["verdict"] == "block"
    assert len(fast.calls) == 1
    assert background.calls == []


def test_sync_judge_from_env_uses_the_sync_model(monkeypatch):
    from veridian_engine.settings import Settings

    monkeypatch.setenv("VERIDIAN_TOKEN", "dev")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("VERIDIAN_SYNC_MODEL", raising=False)
    assert Settings.from_env().sync_judge.keywords["model"] == "claude-haiku-5-5"
    monkeypatch.setenv("VERIDIAN_SYNC_MODEL", "claude-sonnet-5-5")
    assert Settings.from_env().sync_judge.keywords["model"] == "claude-sonnet-5-5"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert Settings.from_env().sync_judge is None
