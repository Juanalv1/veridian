"""U12 wiring: the background sender drains the `notifications` outbox."""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from veridian_engine import db
from veridian_engine.decisions import create_decision
from veridian_engine.events import Event, insert_events
from veridian_engine.notifications.outbox import list_notifications
from veridian_engine.notifications.secret_box import KEY_ENV
from veridian_engine.notifications.sender import WebhookSender
from veridian_engine.notifications.webhook import DELIVERED, FAILED, PENDING, RETRY_DELAYS, verify
from veridian_engine.webhook_store import clear_webhook_endpoint, set_webhook_endpoint

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
URL = "https://hooks.example.com/veridian"
PROJECT = "client_a"


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def public(host, port):
    return ["93.184.216.34"]


class FakeTransport:
    """Returns the queued outcomes in order (an exception is raised); then `default`."""

    def __init__(self, outcomes=(), default=200):
        self.outcomes = list(outcomes)
        self.default = default
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else self.default
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY_ENV, Fernet.generate_key().decode())
    path = str(tmp_path / "veridian.db")
    db.init_db(path)
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO project (id, name, enforcement, created_at) VALUES (?, 'Mi app', 'enforce', ?)",
            (PROJECT, "2026-10-01T00:00:00Z"),
        )
    return path


def configure(db_path):
    with db.connect(db_path) as conn:
        return set_webhook_endpoint(conn, PROJECT, URL, now=NOW, resolver=public)


def block(db_path, ip="203.0.113.9"):
    event = Event(
        id="a2", ts=NOW, user_id="u_42", session_id="s_evil", ip="203.0.113.9", device_id="d_evil",
        method="post", route="/payouts/bank-account", status=200, bytes=10,
    )
    with db.connect(db_path) as conn:
        insert_events(conn, PROJECT, [event])
        return create_decision(
            conn, PROJECT, ts=NOW, user_id="u_42", session_id="s_evil", ip=ip, decision="block",
            signals={"new_network": True}, rationale="Cambio de cuenta desde una red nueva.", event_ids=["a2"],
        )


def sender(db_path, transport, clock, **overrides):
    fields = {
        "transport": transport,
        "clock": clock,
        "resolver": public,
        "panel_base_url": "https://panel.example.com",
    }
    fields.update(overrides)
    return WebhookSender(db_path, **fields)


def outbox(db_path):
    with db.connect(db_path) as conn:
        return list_notifications(conn, PROJECT, limit=50)


def test_delivers_a_signed_block_payload_and_marks_it_delivered(db_path, clock):
    secret = configure(db_path)
    decision_id = block(db_path)
    transport = FakeTransport([204])

    sender(db_path, transport, clock).run_once()

    [request] = transport.requests
    assert request.url == URL
    assert verify(secret, request.headers, request.body, clock())
    body = json.loads(request.body)
    [notification] = outbox(db_path)
    assert request.headers["X-Veridian-Delivery"] == notification["id"] == body["id"]
    assert body["type"] == "decision.block"
    assert body["project"] == {"id": PROJECT, "name": "Mi app"}
    assert body["decision"]["id"] == decision_id
    assert body["decision"]["route"] == "POST /payouts/bank-account"
    assert body["decision"]["blocked"] == {"session": True, "ip": True}
    assert body["panel_url"] == f"https://panel.example.com/decisiones/{decision_id}"
    assert "203.0.113.9" not in request.body.decode()
    assert notification["status"] == DELIVERED and notification["attempts"] == 1


def test_delivered_notifications_are_not_sent_again(db_path, clock):
    configure(db_path)
    block(db_path)
    transport = FakeTransport([200])
    worker = sender(db_path, transport, clock)
    worker.run_once()
    clock.advance(3600)
    worker.run_once()
    assert len(transport.requests) == 1


def test_failures_retry_on_the_schedule_and_then_fail(db_path, clock):
    configure(db_path)
    block(db_path)
    transport = FakeTransport(default=500)
    worker = sender(db_path, transport, clock)

    worker.run_once()
    [notification] = outbox(db_path)
    assert notification["status"] == PENDING and notification["attempts"] == 1
    assert notification["last_error"] == "HTTP 500"
    assert notification["next_attempt_at"] == "2026-10-26T12:01:00Z"

    # Not due yet: nothing is sent.
    clock.advance(RETRY_DELAYS[0] - 1)
    worker.run_once()
    assert len(transport.requests) == 1

    clock.advance(1)
    for delay in RETRY_DELAYS[1:]:
        worker.run_once()
        clock.advance(delay)
    worker.run_once()

    [notification] = outbox(db_path)
    assert len(transport.requests) == len(RETRY_DELAYS) + 1
    assert notification["status"] == FAILED and notification["next_attempt_at"] is None


def test_a_raising_transport_never_escapes_and_counts_as_a_failure(db_path, clock):
    secret = configure(db_path)
    block(db_path)
    transport = FakeTransport([ConnectionError(f"boom {secret}")])

    sender(db_path, transport, clock).run_once()

    [notification] = outbox(db_path)
    assert notification["status"] == PENDING and notification["attempts"] == 1
    assert "ConnectionError" in notification["last_error"]
    assert secret not in notification["last_error"]


def test_a_removed_endpoint_fails_the_notification_without_sending(db_path, clock):
    configure(db_path)
    block(db_path)
    with db.connect(db_path) as conn:
        clear_webhook_endpoint(conn, PROJECT)
    transport = FakeTransport()

    sender(db_path, transport, clock).run_once()

    [notification] = outbox(db_path)
    assert transport.requests == []
    assert notification["status"] == FAILED


def test_run_once_never_raises_even_if_the_database_is_broken(tmp_path, clock):
    worker = sender(str(tmp_path / "missing" / "nope.db"), FakeTransport(), clock)
    assert worker.run_once() == 0


def test_background_thread_starts_delivers_and_stops(db_path, clock):
    configure(db_path)
    block(db_path)
    transport = FakeTransport([200])
    worker = sender(db_path, transport, clock, poll_interval_s=0.01)

    worker.start()
    try:
        assert worker.is_alive()
        deadline = time.monotonic() + 5
        while not transport.requests and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        worker.stop()
    assert not worker.is_alive()
    assert len(transport.requests) == 1
