"""Bearer authentication with hashed API keys (docs/DISENO-PRODUCTO.md, gaps 1 and 2).

Every presented key is resolved through the `api_key` table with `keys.resolve_key`
(hash lookup plus one constant-time compare, decoy when unknown). Unknown, revoked,
mismatched and deleted-project keys all raise the same `Unauthorized`. Failures are
counted per source IP and per visible key prefix; past the threshold the caller gets
`RateLimited` before any key work. Legacy `VERIDIAN_TOKENS` tokens have no visible
prefix, so they are only limited by IP.

Every authenticated caller is an `sdk` principal. The design restricts `sdk` keys to
the SDK endpoints, but the panel still reads with the same token until the `panel`
principal (U6) exists, so panel endpoints keep accepting `sdk` keys for now.
"""

import math
import sqlite3
import threading
from datetime import datetime

from fastapi import Request

from . import db
from .control_plane.key_store import find_by_hash, touch_last_used
from .control_plane.repository import format_ts
from .keys import FailureLimiter, key_prefix, resolve_key
from .settings import Settings

LAST_USED_INTERVAL_S = 60.0  # write last_used_at at most this often per key


class Unauthorized(Exception):
    """Raised for any rejected credential; always rendered as the same 401."""


class RateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__()
        self.retry_after = retry_after

    @property
    def retry_after_header(self) -> str:
        return str(max(1, math.ceil(self.retry_after)))


class ApiKeyAuth:
    def __init__(self, settings: Settings):
        self._settings = settings
        clock = lambda: settings.clock().timestamp()  # noqa: E731 - one clock for limits and timestamps
        window = settings.auth_failure_window_s
        self._by_ip = FailureLimiter(settings.auth_max_failures_per_ip, window, clock)
        self._by_prefix = FailureLimiter(settings.auth_max_failures_per_prefix, window, clock)
        self._last_written: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def project_id(self, request: Request) -> str:
        scheme, _, raw = request.headers.get("Authorization", "").partition(" ")
        token = raw.strip() if scheme.lower() == "bearer" else ""
        # Behind a proxy this is the proxy's address unless the server is told to trust
        # X-Forwarded-For; how many hops to trust on Render is not measured yet.
        ip = f"ip:{request.client.host if request.client else 'unknown'}"
        prefix = key_prefix(token)
        limits = [(self._by_ip, ip)] + ([(self._by_prefix, f"prefix:{prefix}")] if prefix else [])

        for limiter, source in limits:
            decision = limiter.check(source)
            if decision.blocked:
                raise RateLimited(decision.retry_after)

        resolution = self._resolve(token) if token else None
        if resolution is None or not resolution.ok:
            for limiter, source in limits:
                limiter.record_failure(source)
            raise Unauthorized()
        self._touch(resolution.key_id)
        return resolution.project_id

    def _resolve(self, token: str):
        with db.connect(self._settings.db_path) as conn:
            return resolve_key(token, lambda key_hash: find_by_hash(conn, key_hash))

    def _touch(self, key_id: str) -> None:
        now = self._settings.clock()
        with self._lock:
            last = self._last_written.get(key_id)
            if last is not None and (now - last).total_seconds() < LAST_USED_INTERVAL_S:
                return
            self._last_written[key_id] = now
        try:
            with db.connect(self._settings.db_path) as conn:
                touch_last_used(conn, key_id, format_ts(now))
        except sqlite3.OperationalError:
            pass  # bookkeeping only: a busy database must not fail an authenticated request


def project_id_from_request(request: Request) -> str:
    return request.app.state.auth.project_id(request)
