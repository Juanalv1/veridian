"""Signed webhook for block decisions (docs/DISENO-PRODUCTO.md, "Notificaciones de bloqueo").

Pure building blocks; persistence of the `notifications` outbox and the background worker
belong to the integrator:

- `build_block_payload` + `encode_payload`: the documented JSON body. It never carries the IP.
- `sign` / `build_headers` / `verify`: `X-Veridian-Signature: t=<unix>,v1=<hex>` with
  `v1 = HMAC-SHA256(secret, "<t>.<raw body>")`, and the receiver recipe.
- `check_url`: SSRF guard, re-run on every attempt.
- `deliver`: one attempt through an injected transport and clock; never raises.
- `Notification` + `record_attempt` / `process_due`: outbox state and retry schedule.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

SIGNATURE_HEADER = "X-Veridian-Signature"
EVENT_HEADER = "X-Veridian-Event"
DELIVERY_HEADER = "X-Veridian-Delivery"
BLOCK_EVENT = "decision.block"
TEST_EVENT = "webhook.test"

DEFAULT_TOLERANCE_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 5.0
# Wait before each retry: 1 min, 5 min, 30 min, 2 h, 6 h. A configuration starting point, not measured.
RETRY_DELAYS: tuple[int, ...] = (60, 300, 1800, 7200, 21600)
MAX_ERROR_LENGTH = 300

PENDING = "pending"
DELIVERED = "delivered"
FAILED = "failed"

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], list[str]]
Clock = Callable[[], datetime]


# --- Payload -----------------------------------------------------------------------------


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_block_payload(
    *,
    notification_id: str,
    created_at: datetime,
    project_id: str,
    project_name: str,
    decision: Mapping,
    route: str | None,
    panel_base_url: str,
    enforced: bool = True,
) -> dict | None:
    """Documented body for a `block` decision row; None for any other decision.

    The IP is read only to say whether it was blocked; it is never copied into the body,
    and neither are `signals` (minimal exposure, same rule as the e-mail). `enforced` is False
    when the project was in `observe`: the block was decided and recorded, but not applied.
    """
    if decision.get("decision") != "block":
        return None
    decision_id = str(decision["id"])
    return {
        "id": notification_id,
        "type": BLOCK_EVENT,
        "created_at": _iso_utc(created_at),
        "project": {"id": project_id, "name": project_name},
        "decision": {
            "id": decision_id,
            "ts": decision["ts"],
            "user_id": decision["user_id"],
            "session_id": decision["session_id"],
            "route": route,
            "rationale": decision["rationale"],
            "blocked": {"session": True, "ip": bool(decision.get("ip"))},
            "enforced": enforced,
        },
        "panel_url": f"{panel_base_url.rstrip('/')}/decisiones/{quote(decision_id, safe='')}",
    }


def build_test_payload(*, notification_id: str, created_at: datetime, project_id: str, project_name: str) -> dict:
    """Body of `POST .../webhook/test`: same envelope and signature as a block, no decision."""
    return {
        "id": notification_id,
        "type": TEST_EVENT,
        "created_at": _iso_utc(created_at),
        "project": {"id": project_id, "name": project_name},
    }


def encode_payload(payload: Mapping) -> bytes:
    """Raw UTF-8 JSON body. The signature covers exactly these bytes."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# --- Signature ---------------------------------------------------------------------------


def _digest(secret: str, body: bytes, timestamp: int) -> str:
    message = f"{timestamp}.".encode("ascii") + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign(secret: str, body: bytes, timestamp: int) -> str:
    """Value of `X-Veridian-Signature`."""
    return f"t={timestamp},v1={_digest(secret, body, timestamp)}"


def build_headers(
    secret: str, body: bytes, delivery_id: str, timestamp: int, event: str = BLOCK_EVENT
) -> dict[str, str]:
    return {
        "Content-Type": "application/json; charset=utf-8",
        SIGNATURE_HEADER: sign(secret, body, timestamp),
        EVENT_HEADER: event,
        DELIVERY_HEADER: delivery_id,
    }


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _parse_signature(value: str) -> tuple[int, list[str]] | None:
    timestamp: int | None = None
    signatures: list[str] = []
    for part in value.split(","):
        key, sep, item = part.strip().partition("=")
        if not sep:
            return None
        if key == "t":
            try:
                timestamp = int(item)
            except ValueError:
                return None
        elif key == "v1":
            signatures.append(item)
    if timestamp is None or not signatures:
        return None
    return timestamp, signatures


def verify(
    secret: str,
    headers: Mapping[str, str],
    body: bytes,
    now: datetime | int | float,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> bool:
    """Receiver recipe: recompute over the raw body, compare in constant time, and reject
    a `t` further than `tolerance_seconds` from the receiver clock (replay protection)."""
    value = _header(headers, SIGNATURE_HEADER)
    parsed = _parse_signature(value) if value else None
    if parsed is None:
        return False
    timestamp, signatures = parsed
    now_seconds = now.timestamp() if isinstance(now, datetime) else float(now)
    if abs(now_seconds - timestamp) > tolerance_seconds:
        return False
    expected = _digest(secret, body, timestamp)
    # Check every candidate so the time spent does not depend on which one matched.
    matched = False
    for candidate in signatures:
        matched |= hmac.compare_digest(expected.encode("ascii"), candidate.encode("ascii", "replace"))
    return matched


# --- SSRF guard --------------------------------------------------------------------------


class UnsafeUrlError(ValueError):
    pass


def default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _is_public(ip: IpAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None and not _is_public(embedded):
            return False
    return ip.is_global and not ip.is_multicast


def check_url(url: str, resolver: Resolver = default_resolver) -> list[IpAddress]:
    """Require https, resolve the host and reject any non-public address.

    Every resolved address must be public: one private answer rejects the URL. Run it on
    every attempt, since DNS can change between attempts.
    """
    try:
        parts = urlsplit(url)
        port = parts.port or 443
    except ValueError as exc:
        raise UnsafeUrlError(f"unsafe webhook url: malformed ({exc})") from None
    if parts.scheme.lower() != "https":
        raise UnsafeUrlError("unsafe webhook url: https is required")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("unsafe webhook url: credentials in the url are not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("unsafe webhook url: missing host")

    try:
        raw_addresses = [host] if _is_ip_literal(host) else resolver(host, port)
    except OSError as exc:
        raise UnsafeUrlError(f"unsafe webhook url: cannot resolve host ({type(exc).__name__})") from None
    if not raw_addresses:
        raise UnsafeUrlError("unsafe webhook url: host resolves to no address")

    addresses: list[IpAddress] = []
    for raw in raw_addresses:
        try:
            ip = ipaddress.ip_address(str(raw).split("%", 1)[0])
        except ValueError:
            raise UnsafeUrlError("unsafe webhook url: unparseable address") from None
        if not _is_public(ip):
            raise UnsafeUrlError(f"unsafe webhook url: non-public address {ip}")
        addresses.append(ip)
    return addresses


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


# --- Delivery ----------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookRequest:
    url: str
    headers: dict[str, str]
    body: bytes
    timeout: float
    addresses: tuple[IpAddress, ...]


class Transport(Protocol):
    def send(self, request: WebhookRequest) -> int:
        """POST the request and return the HTTP status code. Must not follow redirects."""


class HttpxTransport:
    """Real transport. Redirects are never followed: a 3xx is returned as a failure."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._transport = transport

    def send(self, request: WebhookRequest) -> int:
        with httpx.Client(
            transport=self._transport, follow_redirects=False, timeout=request.timeout
        ) as client:
            response = client.post(request.url, content=request.body, headers=request.headers)
            return response.status_code


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    status_code: int | None = None
    error: str | None = None


def _describe(exc: BaseException, secret: str) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if secret:
        text = text.replace(secret, "[redacted]")
    return text[:MAX_ERROR_LENGTH]


def deliver(
    url: str,
    secret: str,
    payload: Mapping,
    *,
    delivery_id: str,
    transport: Transport,
    clock: Clock,
    resolver: Resolver = default_resolver,
    event: str = BLOCK_EVENT,
) -> DeliveryResult:
    """One signed attempt. Only a 2xx counts as delivered. Never raises."""
    try:
        addresses = check_url(url, resolver)
        body = encode_payload(payload)
        timestamp = int(clock().timestamp())
        request = WebhookRequest(
            url=url,
            headers=build_headers(secret, body, delivery_id, timestamp, event),
            body=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
            addresses=tuple(addresses),
        )
        status = transport.send(request)
    except Exception as exc:  # noqa: BLE001 - a failed delivery must never reach the caller
        return DeliveryResult(delivered=False, error=_describe(exc, secret))
    if 200 <= status < 300:
        return DeliveryResult(delivered=True, status_code=status)
    return DeliveryResult(delivered=False, status_code=status, error=f"HTTP {status}")


# --- Outbox ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Notification:
    """One row of the `notifications` outbox table."""

    id: str
    project_id: str
    decision_id: str
    kind: str = "webhook"
    status: str = PENDING
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    enforced: bool = True


def new_notification(
    notification_id: str,
    project_id: str,
    decision_id: str,
    now: datetime,
    kind: str = "webhook",
    enforced: bool = True,
) -> Notification:
    return Notification(
        id=notification_id,
        project_id=project_id,
        decision_id=decision_id,
        kind=kind,
        next_attempt_at=now,
        enforced=enforced,
    )


def next_attempt_at(
    attempts_done: int, now: datetime, delays: tuple[int, ...] = RETRY_DELAYS
) -> datetime | None:
    """When to try again after `attempts_done` failed attempts; None means give up."""
    if attempts_done < 1 or attempts_done > len(delays):
        return None
    return now + timedelta(seconds=delays[attempts_done - 1])


def is_due(notification: Notification, now: datetime) -> bool:
    return (
        notification.status == PENDING
        and notification.next_attempt_at is not None
        and notification.next_attempt_at <= now
    )


def record_attempt(
    notification: Notification,
    result: DeliveryResult,
    now: datetime,
    delays: tuple[int, ...] = RETRY_DELAYS,
) -> Notification:
    attempts = notification.attempts + 1
    if result.delivered:
        return replace(
            notification, status=DELIVERED, attempts=attempts, next_attempt_at=None, last_error=None
        )
    retry_at = next_attempt_at(attempts, now, delays)
    return replace(
        notification,
        status=PENDING if retry_at is not None else FAILED,
        attempts=attempts,
        next_attempt_at=retry_at,
        last_error=result.error,
    )


def process_due(
    notifications: Iterable[Notification],
    now: datetime,
    attempt: Callable[[Notification], DeliveryResult],
    delays: tuple[int, ...] = RETRY_DELAYS,
) -> list[Notification]:
    """Run `attempt` for each due notification and return every row with its new state.

    An exception inside `attempt` counts as a failed attempt; it never escapes.
    """
    updated: list[Notification] = []
    for notification in notifications:
        if not is_due(notification, now):
            updated.append(notification)
            continue
        try:
            result = attempt(notification)
        except Exception as exc:  # noqa: BLE001 - the worker loop must keep running
            result = DeliveryResult(delivered=False, error=_describe(exc, ""))
        updated.append(record_attempt(notification, result, now, delays))
    return updated
