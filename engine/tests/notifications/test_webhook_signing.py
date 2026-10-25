import hashlib
import hmac
import json
from datetime import datetime, timezone

from veridian_engine.notifications.webhook import (
    BLOCK_EVENT,
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    build_block_payload,
    build_headers,
    encode_payload,
    sign,
    verify,
)

SECRET = "whsec_test_secret"
T = 1_793_000_000
CREATED = datetime(2026, 10, 26, 10, 14, 5, tzinfo=timezone.utc)

BLOCK_ROW = {
    "id": "d_311",
    "ts": "2026-10-26T10:14:05Z",
    "user_id": "u_42",
    "session_id": "s_8f1",
    "ip": "203.0.113.9",
    "decision": "block",
    "signals": '["new_ip"]',
    "rationale": "Unknown network and device.",
}


def _payload(row=BLOCK_ROW):
    return build_block_payload(
        notification_id="ntf_1",
        created_at=CREATED,
        project_id="prj_x7k2",
        project_name="My app",
        decision=row,
        route="POST /payouts/bank-account",
        panel_base_url="https://panel.example.com/",
    )


def test_block_payload_matches_documented_shape():
    assert _payload() == {
        "id": "ntf_1",
        "type": "decision.block",
        "created_at": "2026-10-26T10:14:05Z",
        "project": {"id": "prj_x7k2", "name": "My app"},
        "decision": {
            "id": "d_311",
            "ts": "2026-10-26T10:14:05Z",
            "user_id": "u_42",
            "session_id": "s_8f1",
            "route": "POST /payouts/bank-account",
            "rationale": "Unknown network and device.",
            "blocked": {"session": True, "ip": True},
            "enforced": True,
        },
        "panel_url": "https://panel.example.com/decisiones/d_311",
    }


def test_payload_never_carries_the_ip_or_signals():
    body = encode_payload(_payload()).decode("utf-8")
    assert "203.0.113.9" not in body
    assert "new_ip" not in body


def test_session_only_block_reports_ip_not_blocked():
    payload = _payload({**BLOCK_ROW, "ip": ""})
    assert payload["decision"]["blocked"] == {"session": True, "ip": False}


def test_an_observed_block_says_it_was_not_enforced():
    payload = build_block_payload(
        notification_id="ntf_1", created_at=CREATED, project_id="prj_x7k2", project_name="My app",
        decision=BLOCK_ROW, route=None, panel_base_url="", enforced=False,
    )
    assert payload["decision"]["enforced"] is False


def test_only_block_decisions_notify():
    assert _payload({**BLOCK_ROW, "decision": "alert"}) is None
    assert _payload({**BLOCK_ROW, "decision": "allow"}) is None


def test_signature_follows_documented_recipe():
    body = encode_payload(_payload())
    expected = hmac.new(SECRET.encode(), f"{T}.".encode() + body, hashlib.sha256).hexdigest()
    assert sign(SECRET, body, T) == f"t={T},v1={expected}"


def test_headers_carry_event_and_delivery_id():
    body = encode_payload(_payload())
    headers = build_headers(SECRET, body, delivery_id="ntf_1", timestamp=T)
    assert headers[SIGNATURE_HEADER] == sign(SECRET, body, T)
    assert headers[EVENT_HEADER] == BLOCK_EVENT
    assert headers[DELIVERY_HEADER] == "ntf_1"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert SECRET not in json.dumps(headers)


def test_verify_accepts_a_fresh_signature_with_any_header_case():
    body = encode_payload(_payload())
    headers = {k.lower(): v for k, v in build_headers(SECRET, body, "ntf_1", T).items()}
    assert verify(SECRET, headers, body, now=T + 10)


def test_verify_accepts_datetime_now():
    body = b'{"a":1}'
    headers = build_headers(SECRET, body, "ntf_1", T)
    assert verify(SECRET, headers, body, now=datetime.fromtimestamp(T + 1, tz=timezone.utc))


def test_verify_rejects_tampered_body():
    body = encode_payload(_payload())
    headers = build_headers(SECRET, body, "ntf_1", T)
    assert not verify(SECRET, headers, body.replace(b"u_42", b"u_43"), now=T)


def test_verify_rejects_wrong_secret():
    body = b'{"a":1}'
    headers = build_headers(SECRET, body, "ntf_1", T)
    assert not verify("whsec_other", headers, body, now=T)


def test_verify_rejects_old_and_future_timestamps():
    body = b'{"a":1}'
    headers = build_headers(SECRET, body, "ntf_1", T)
    assert not verify(SECRET, headers, body, now=T + 301)
    assert not verify(SECRET, headers, body, now=T - 301)
    assert verify(SECRET, headers, body, now=T + 300)


def test_verify_rejects_missing_or_malformed_header():
    body = b'{"a":1}'
    assert not verify(SECRET, {}, body, now=T)
    assert not verify(SECRET, {SIGNATURE_HEADER: "garbage"}, body, now=T)
    assert not verify(SECRET, {SIGNATURE_HEADER: "t=abc,v1=00"}, body, now=T)
    assert not verify(SECRET, {SIGNATURE_HEADER: f"t={T}"}, body, now=T)


def test_verify_rejects_signature_moved_to_another_timestamp():
    body = b'{"a":1}'
    v1 = sign(SECRET, body, T).split("v1=")[1]
    replayed = {SIGNATURE_HEADER: f"t={T + 100},v1={v1}"}
    assert not verify(SECRET, replayed, body, now=T + 100)
