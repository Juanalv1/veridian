from datetime import datetime, timedelta, timezone

import httpx

from veridian_engine.notifications.webhook import (
    DELIVERED,
    FAILED,
    PENDING,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_DELAYS,
    SIGNATURE_HEADER,
    HttpxTransport,
    deliver,
    is_due,
    new_notification,
    next_attempt_at,
    process_due,
    record_attempt,
    verify,
)

SECRET = "whsec_test_secret"
URL = "https://hooks.example.com/veridian"
NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
PAYLOAD = {"id": "ntf_1", "type": "decision.block"}


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def public(host, port):
    return ["93.184.216.34"]


def private(host, port):
    return ["10.0.0.7"]


class RecordingTransport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _deliver(transport, clock=None, resolver=public, url=URL):
    return deliver(
        url,
        SECRET,
        PAYLOAD,
        delivery_id="ntf_1",
        transport=transport,
        clock=clock or FakeClock(NOW),
        resolver=resolver,
    )


def test_2xx_is_delivered_and_request_is_signed_verifiably():
    transport = RecordingTransport([204])
    result = _deliver(transport)
    assert result.delivered and result.status_code == 204 and result.error is None
    request = transport.requests[0]
    assert request.url == URL
    assert request.timeout == REQUEST_TIMEOUT_SECONDS == 5.0
    assert verify(SECRET, request.headers, request.body, now=NOW)


def test_non_2xx_including_redirect_is_not_delivered():
    for status in (301, 302, 400, 500):
        result = _deliver(RecordingTransport([status]))
        assert not result.delivered
        assert result.status_code == status
        assert str(status) in result.error


def test_transport_exception_never_raises():
    result = _deliver(RecordingTransport([httpx.ConnectError("boom")]))
    assert not result.delivered and result.status_code is None
    assert "ConnectError" in result.error


def test_unexpected_exception_never_raises():
    result = _deliver(RecordingTransport([RuntimeError("anything")]))
    assert not result.delivered


def test_unserializable_payload_never_raises():
    result = deliver(
        URL, SECRET, {"x": object()}, delivery_id="n", transport=RecordingTransport([200]),
        clock=FakeClock(NOW), resolver=public,
    )
    assert not result.delivered


def test_private_target_is_refused_without_sending():
    transport = RecordingTransport([200])
    result = _deliver(transport, resolver=private)
    assert not result.delivered
    assert transport.requests == []
    assert "unsafe" in result.error


def test_secret_never_appears_in_errors():
    result = _deliver(RecordingTransport([RuntimeError(f"leak {SECRET}")]))
    assert SECRET not in result.error


def test_httpx_transport_does_not_follow_redirects():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://169.254.169.254/latest"})

    transport = HttpxTransport(transport=httpx.MockTransport(handler))
    result = _deliver(transport)
    assert not result.delivered and result.status_code == 302
    assert calls == [URL]


def test_retry_schedule_uses_documented_backoff():
    assert RETRY_DELAYS == (60, 300, 1800, 7200, 21600)
    assert next_attempt_at(1, NOW) == NOW + timedelta(minutes=1)
    assert next_attempt_at(5, NOW) == NOW + timedelta(hours=6)
    assert next_attempt_at(6, NOW) is None


def test_outbox_retries_with_fake_clock_then_gives_up():
    clock = FakeClock(NOW)
    notification = new_notification("ntf_1", "prj_1", "d_1", clock())
    assert notification.status == PENDING and notification.attempts == 0
    assert is_due(notification, clock())

    attempts = []

    def failing(n):
        attempts.append(clock())
        return _deliver(RecordingTransport([500]), clock=clock)

    for delay in RETRY_DELAYS:
        [notification] = process_due([notification], clock(), failing)
        assert notification.status == PENDING
        assert not is_due(notification, clock())
        clock.advance(delay - 1)
        assert process_due([notification], clock(), failing) == [notification]
        clock.advance(1)
        assert is_due(notification, clock())

    [notification] = process_due([notification], clock(), failing)
    assert notification.status == FAILED
    assert notification.attempts == len(RETRY_DELAYS) + 1 == len(attempts)
    assert "500" in notification.last_error
    assert not is_due(notification, clock() + timedelta(days=30))


def test_outbox_marks_delivered_after_a_retry():
    clock = FakeClock(NOW)
    transport = RecordingTransport([503, 200])
    notification = new_notification("ntf_1", "prj_1", "d_1", clock())
    notification = record_attempt(notification, _deliver(transport, clock=clock), clock())
    assert notification.status == PENDING and notification.attempts == 1
    clock.advance(60)
    notification = record_attempt(notification, _deliver(transport, clock=clock), clock())
    assert notification.status == DELIVERED
    assert notification.last_error is None and notification.next_attempt_at is None


def test_process_due_survives_an_attempt_that_raises():
    notification = new_notification("ntf_1", "prj_1", "d_1", NOW)

    def explode(n):
        raise RuntimeError("bug in caller")

    [after] = process_due([notification], NOW, explode)
    assert after.status == PENDING and after.attempts == 1
    assert "RuntimeError" in after.last_error


def test_signature_header_name_is_documented():
    assert SIGNATURE_HEADER == "X-Veridian-Signature"
