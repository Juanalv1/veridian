"""U12 wiring: a project's webhook endpoint (set/clear) and the block-only outbox enqueue."""

from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from veridian_engine import db
from veridian_engine.decisions import create_decision
from veridian_engine.notifications.outbox import list_notifications
from veridian_engine.notifications.secret_box import KEY_ENV, SecretKeyMissing, decrypt_secret
from veridian_engine.notifications.webhook import PENDING, UnsafeUrlError
from veridian_engine.webhook_store import (
    clear_webhook_endpoint,
    get_webhook_endpoint,
    set_webhook_endpoint,
    set_webhook_enabled,
)

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
URL = "https://hooks.example.com/veridian"
PROJECT = "client_a"


def public(host, port):
    return ["93.184.216.34"]


def private(host, port):
    return ["10.0.0.7"]


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "veridian.db")
    db.init_db(path)
    return path


@pytest.fixture
def secret_key(monkeypatch):
    monkeypatch.setenv(KEY_ENV, Fernet.generate_key().decode())


def configure(db_path, url=URL, resolver=public, project_id=PROJECT):
    with db.connect(db_path) as conn:
        return set_webhook_endpoint(conn, project_id, url, now=NOW, resolver=resolver)


def decide(db_path, decision="block", project_id=PROJECT, session_id="s_evil"):
    with db.connect(db_path) as conn:
        return create_decision(
            conn,
            project_id,
            ts=NOW,
            user_id="u_42",
            session_id=session_id,
            ip="203.0.113.9",
            decision=decision,
            signals={},
            rationale="test",
            event_ids=[],
        )


def outbox(db_path, project_id=PROJECT):
    with db.connect(db_path) as conn:
        return list_notifications(conn, project_id, limit=50)


# --- endpoint configuration ---


def test_set_returns_the_secret_once_and_stores_it_encrypted(db_path, secret_key):
    secret = configure(db_path)

    assert secret.startswith("whsec_")
    with db.connect(db_path) as conn:
        row = conn.execute("SELECT * FROM webhook_endpoint WHERE project_id = ?", (PROJECT,)).fetchone()
        dump = "\n".join(conn.iterdump())
    assert row["url"] == URL and row["enabled"] == 1
    assert secret not in row["secret_enc"]
    assert secret not in dump
    assert decrypt_secret(row["secret_enc"]) == secret


def test_get_never_exposes_the_secret(db_path, secret_key):
    configure(db_path)
    with db.connect(db_path) as conn:
        endpoint = get_webhook_endpoint(conn, PROJECT)
    assert endpoint == {"url": URL, "enabled": True, "created_at": "2026-10-26T12:00:00Z"}


def test_setting_again_rotates_the_secret(db_path, secret_key):
    first = configure(db_path)
    second = configure(db_path, url="https://hooks.example.com/other")
    assert first != second
    with db.connect(db_path) as conn:
        rows = conn.execute("SELECT url, secret_enc FROM webhook_endpoint").fetchall()
    assert len(rows) == 1
    assert rows[0]["url"] == "https://hooks.example.com/other"
    assert decrypt_secret(rows[0]["secret_enc"]) == second


@pytest.mark.parametrize(
    "url, resolver",
    [
        ("http://hooks.example.com/x", public),
        ("https://hooks.example.com/x", private),
        ("https://169.254.169.254/latest", public),
        ("https://user:pw@hooks.example.com/x", public),
    ],
)
def test_ssrf_unsafe_urls_are_rejected_and_nothing_is_stored(db_path, secret_key, url, resolver):
    with pytest.raises(UnsafeUrlError):
        configure(db_path, url=url, resolver=resolver)
    with db.connect(db_path) as conn:
        assert get_webhook_endpoint(conn, PROJECT) is None


def test_without_a_server_key_webhooks_cannot_be_configured(db_path, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(SecretKeyMissing):
        configure(db_path)
    with db.connect(db_path) as conn:
        assert get_webhook_endpoint(conn, PROJECT) is None


def test_clear_removes_the_endpoint(db_path, secret_key):
    configure(db_path)
    with db.connect(db_path) as conn:
        assert clear_webhook_endpoint(conn, PROJECT) is True
        assert clear_webhook_endpoint(conn, PROJECT) is False
        assert get_webhook_endpoint(conn, PROJECT) is None


# --- outbox enqueue at the decision choke point ---


def test_block_with_an_endpoint_enqueues_exactly_one_notification(db_path, secret_key):
    configure(db_path)
    decision_id = decide(db_path)

    [notification] = outbox(db_path)
    assert notification["decision_id"] == decision_id
    assert notification["kind"] == "webhook"
    assert notification["status"] == PENDING
    assert notification["attempts"] == 0
    assert notification["next_attempt_at"] == "2026-10-26T12:00:00Z"
    assert notification["id"].startswith("ntf_")


def test_block_without_an_endpoint_enqueues_nothing(db_path):
    decide(db_path)
    assert outbox(db_path) == []


def test_block_with_a_disabled_endpoint_enqueues_nothing(db_path, secret_key):
    configure(db_path)
    with db.connect(db_path) as conn:
        set_webhook_enabled(conn, PROJECT, False)
    decide(db_path)
    assert outbox(db_path) == []


@pytest.mark.parametrize("decision", ["alert", "allow", "review"])
def test_non_block_decisions_never_enqueue(db_path, secret_key, decision):
    configure(db_path)
    decide(db_path, decision=decision)
    assert outbox(db_path) == []


def test_other_projects_endpoint_does_not_notify(db_path, secret_key):
    configure(db_path, project_id="client_b")
    decide(db_path, project_id=PROJECT)
    assert outbox(db_path) == [] and outbox(db_path, "client_b") == []


def test_enqueue_shares_the_decision_transaction(db_path, secret_key):
    configure(db_path)
    with pytest.raises(RuntimeError):
        with db.connect(db_path) as conn:
            create_decision(
                conn, PROJECT, ts=NOW, user_id="u", session_id="s", ip="", decision="block",
                signals={}, rationale="r", event_ids=[],
            )
            raise RuntimeError("rollback")
    assert outbox(db_path) == []
    with db.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
