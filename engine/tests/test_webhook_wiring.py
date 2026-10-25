"""U12 wiring through the app: block decisions on both paths reach the outbox, the sender runs
with the app, and a failing receiver never affects ingestion or POST /v1/check."""

import pytest
from conftest import AUTH_A, NOW, make_event, make_settings
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_ai_wiring import FakeJudge
from test_check import CRITICAL, GRAY, check
from test_decisions import ATTACKER, attack, decisions, seed_owner_history

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.notifications.outbox import list_notifications
from veridian_engine.notifications.secret_box import KEY_ENV
from veridian_engine.notifications.webhook import DELIVERED, PENDING
from veridian_engine.settings import Settings
from veridian_engine.webhook_store import set_webhook_endpoint

PROJECT_A = "client_a"


def public(host, port):
    return ["93.184.216.34"]


class FakeTransport:
    def __init__(self, error: Exception | None = None, status: int = 200):
        self.error = error
        self.status = status
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.status


@pytest.fixture
def app_factory(tmp_path, clock, monkeypatch):
    monkeypatch.setenv(KEY_ENV, Fernet.generate_key().decode())
    clients = []

    def factory(transport, **overrides):
        fields = {
            "webhooks": True,
            "webhook_transport": transport,
            "webhook_resolver": public,
            # The test drives the sender by hand; the thread just idles.
            "webhook_poll_s": 3600.0,
        }
        fields.update(overrides)
        settings = make_settings(tmp_path, clock, **fields)
        test_client = TestClient(create_app(settings))
        test_client.__enter__()
        clients.append(test_client)
        with db.connect(settings.db_path) as conn:
            set_webhook_endpoint(conn, PROJECT_A, "https://hooks.example.com/v", now=clock(), resolver=public)
        return test_client, settings

    yield factory
    for test_client in clients:
        test_client.__exit__(None, None, None)


def outbox(settings):
    with db.connect(settings.db_path) as conn:
        return list_notifications(conn, PROJECT_A, limit=50)


def test_rule_block_on_ingestion_is_enqueued_and_delivered(app_factory):
    transport = FakeTransport()
    client, settings = app_factory(transport)
    seed_owner_history(client)

    attack(client)

    [decision] = decisions(client)
    [notification] = outbox(settings)
    assert notification["decision_id"] == decision["id"] and notification["status"] == PENDING
    client.app.state.webhook_sender.run_once()
    assert outbox(settings)[0]["status"] == DELIVERED
    assert len(transport.requests) == 1


def test_block_on_check_is_enqueued_once(app_factory):
    client, settings = app_factory(FakeTransport())
    seed_owner_history(client)

    response = check(client, make_event("c1", NOW, **{**ATTACKER, **CRITICAL}))
    assert response.json()["verdict"] == "block"
    # The same session blocked again does not create a second decision or notification.
    check(client, make_event("c2", NOW, **{**ATTACKER, **CRITICAL}))
    assert len(outbox(settings)) == 1


def test_failing_receiver_never_affects_ingestion_or_check(app_factory):
    transport = FakeTransport(error=ConnectionError("receiver down"))
    client, settings = app_factory(transport)
    seed_owner_history(client)
    assert attack(client).json() == {"accepted": 2}

    client.app.state.webhook_sender.run_once()

    assert attack(client, prefix="b", session_id="s_other").status_code == 202
    response = check(client, make_event("c9", NOW, **{**ATTACKER, **CRITICAL}))
    assert response.status_code == 200
    first = outbox(settings)[-1]  # newest first: the earliest block is last
    assert first["attempts"] == 1 and first["status"] == PENDING


def test_ai_block_on_check_is_enqueued(app_factory):
    client, settings = app_factory(FakeTransport(), judge=FakeJudge(decision="block", rationale="IA: atacante."))
    seed_owner_history(client)

    response = check(client, make_event("g1", NOW, **GRAY))

    assert response.json()["verdict"] == "block"
    [decision] = decisions(client)
    [notification] = outbox(settings)
    assert notification["decision_id"] == decision["id"]


def test_ai_non_block_decision_is_not_enqueued(app_factory):
    client, settings = app_factory(FakeTransport(), judge=FakeJudge(decision="alert", rationale="IA: raro."))
    seed_owner_history(client)

    check(client, make_event("g1", NOW, **GRAY))

    assert [d["decision"] for d in decisions(client)] == ["alert"]
    assert outbox(settings) == []


def test_sender_thread_lives_with_the_app(tmp_path, clock):
    settings = make_settings(
        tmp_path, clock, webhooks=True, webhook_transport=FakeTransport(), webhook_poll_s=3600.0
    )
    with TestClient(create_app(settings)) as client:
        worker = client.app.state.webhook_sender
        assert worker.is_alive()
    assert not worker.is_alive()


def test_webhooks_off_starts_no_sender(tmp_path, clock):
    settings = make_settings(tmp_path, clock, webhooks=False)
    with TestClient(create_app(settings)) as client:
        assert client.app.state.webhook_sender is None
        assert client.get("/v1/decisions", headers=AUTH_A).status_code == 200


def test_from_env_turns_webhooks_on_by_default_and_off_on_request(monkeypatch):
    monkeypatch.setenv("VERIDIAN_TOKEN", "tok")
    monkeypatch.delenv("VERIDIAN_WEBHOOKS", raising=False)
    monkeypatch.setenv("VERIDIAN_PANEL_URL", "https://panel.example.com")
    on = Settings.from_env()
    assert on.webhooks is True and on.panel_base_url == "https://panel.example.com"
    monkeypatch.setenv("VERIDIAN_WEBHOOKS", "off")
    assert Settings.from_env().webhooks is False
