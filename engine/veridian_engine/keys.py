"""API keys: generation, hashing, constant-time resolution and a failed-attempt limiter.

Pure module: no database and no HTTP. Storage is reached through a `find_by_hash`
callable and time through an injected clock, so the API layer decides wiring.
See docs/DISENO-PRODUCTO.md, sections "1. Claves de API" and "2. Comparacion en
tiempo constante y limite de fallos".
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

KEY_SCHEME = "vrd"
PREFIX_LENGTH = 8
SECRET_BYTES = 32
LEGACY_PREFIX = "legacy"
LEGACY_CREATED_BY = "migration"

_KEY_PATTERN = re.compile(rf"^{KEY_SCHEME}_([0-9a-f]{{{PREFIX_LENGTH}}})_[A-Za-z0-9_-]+$")


def hash_key(key: str) -> str:
    """Return the hex SHA-256 of the full key.

    Unsalted fast SHA-256 is enough: generated keys carry 256 bits of random secret,
    so they cannot be brute-forced the way a human-chosen password can, and a slow
    hash would only add latency to every SDK request. Being deterministic is what
    allows the lookup by a unique index on `key_hash`.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# Compared against when no row exists, so unknown keys do the same work as known ones.
_DECOY_HASH = hash_key("veridian-decoy-key")


@dataclass(frozen=True)
class GeneratedKey:
    key: str  # shown to the user exactly once, never stored
    prefix: str
    key_hash: str


def generate_key() -> GeneratedKey:
    """Create `vrd_<8 hex prefix>_<32 random bytes in base64url>`."""
    prefix = secrets.token_hex(PREFIX_LENGTH // 2)
    key = f"{KEY_SCHEME}_{prefix}_{secrets.token_urlsafe(SECRET_BYTES)}"
    return GeneratedKey(key=key, prefix=prefix, key_hash=hash_key(key))


def key_prefix(key: str) -> str | None:
    """Return the visible prefix of a well-formed key, else None (e.g. legacy tokens)."""
    match = _KEY_PATTERN.match(key)
    return match.group(1) if match else None


@dataclass(frozen=True)
class StoredKey:
    """What the store returns for a hash; only the fields resolution needs."""

    id: str
    project_id: str
    key_hash: str
    revoked_at: str | None = None
    project_deleted_at: str | None = None


@dataclass(frozen=True)
class KeyResolution:
    ok: bool
    key_id: str | None = None
    project_id: str | None = None


# The single failure value: unknown, revoked, deleted-project and mismatched keys all
# return this same object, so callers cannot (and must not) tell them apart.
REJECTED = KeyResolution(ok=False)


def resolve_key(key: str, find_by_hash: Callable[[str], StoredKey | None]) -> KeyResolution:
    """Resolve a presented key to its project, or return REJECTED.

    Every path hashes the key, queries the store once and runs exactly one
    `hmac.compare_digest` between two 64-char hex digests (against a decoy when no
    row exists), so the outcome does not leak through an early return.
    """
    presented = hash_key(key)
    row = find_by_hash(presented)
    stored = row.key_hash if row is not None else _DECOY_HASH
    matches = hmac.compare_digest(presented, stored)
    if row is None or not matches or row.revoked_at is not None or row.project_deleted_at is not None:
        return REJECTED
    return KeyResolution(ok=True, key_id=row.id, project_id=row.project_id)


@dataclass(frozen=True)
class LimitDecision:
    blocked: bool
    retry_after: float = 0.0  # seconds; feed `Retry-After` when blocked


class FailureLimiter:
    """Sliding-window counter of authentication failures per source.

    A source is any string the caller chooses, e.g. "ip:<addr>" or "prefix:<prefix>".
    In memory, so it is only valid with a single engine process. Successes are not
    tracked and never clear failures. Thresholds are configuration, not measured.
    """

    def __init__(
        self,
        max_failures: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_failures < 1:
            raise ValueError("max_failures must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, source: str, now: float) -> deque[float] | None:
        times = self._failures.get(source)
        if times is None:
            return None
        while times and times[0] <= now - self._window:
            times.popleft()
        if not times:
            del self._failures[source]
            return None
        return times

    def check(self, source: str) -> LimitDecision:
        """Call before doing any key work; a blocked decision maps to `429`."""
        with self._lock:
            now = self._clock()
            times = self._prune(source, now)
            if times is None or len(times) < self._max:
                return LimitDecision(blocked=False)
            # Unblocks once enough old failures leave the window to drop below max.
            oldest_relevant = times[len(times) - self._max]
            return LimitDecision(blocked=True, retry_after=max(0.0, oldest_relevant + self._window - now))

    def record_failure(self, source: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune(source, now)
            self._failures.setdefault(source, deque()).append(now)

    def failure_count(self, source: str) -> int:
        with self._lock:
            times = self._prune(source, self._clock())
            return 0 if times is None else len(times)

    def tracked_sources(self) -> int:
        with self._lock:
            return len(self._failures)


@dataclass(frozen=True)
class LegacyKeyRow:
    project_id: str
    key_hash: str
    prefix: str = LEGACY_PREFIX
    created_by: str = LEGACY_CREATED_BY


def legacy_import(
    tokens: Mapping[str, str],
    existing_hashes: Collection[str] = (),
) -> list[LegacyKeyRow]:
    """Turn `Settings.tokens` ({token: client_id}) into api_keys rows.

    Only the hash is kept, so the demo SDK keeps authenticating with its old token.
    Idempotent by hash: hashes already stored, or repeated in the input, are skipped,
    and output order follows the input so reruns yield the same rows.
    """
    seen = set(existing_hashes)
    rows: list[LegacyKeyRow] = []
    for token, client_id in tokens.items():
        key_hash = hash_key(token.strip())
        if key_hash in seen:
            continue
        seen.add(key_hash)
        rows.append(LegacyKeyRow(project_id=client_id.strip(), key_hash=key_hash))
    return rows
