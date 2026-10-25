import hashlib
import threading

import pytest

from veridian_engine import keys
from veridian_engine.keys import (
    REJECTED,
    FailureLimiter,
    StoredKey,
    generate_key,
    hash_key,
    key_prefix,
    legacy_import,
    resolve_key,
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def store(*rows: StoredKey):
    by_hash = {row.key_hash: row for row in rows}
    return by_hash.get


# --- generation and hashing -------------------------------------------------


def test_generated_key_has_documented_format():
    generated = generate_key()
    scheme, prefix, secret = generated.key.split("_", 2)
    assert scheme == "vrd"
    assert len(prefix) == 8
    assert generated.prefix == prefix
    # 32 random bytes in base64url without padding is 43 characters.
    assert len(secret) == 43
    assert generated.key_hash == hash_key(generated.key)


def test_generated_keys_are_unique():
    assert len({generate_key().key for _ in range(50)}) == 50


def test_hash_is_stable_and_not_the_key():
    key = "vrd_abcd1234_secretsecret"
    assert hash_key(key) == hash_key(key)
    assert hash_key(key) == hashlib.sha256(key.encode()).hexdigest()
    assert key not in hash_key(key)
    assert hash_key(key) != hash_key(key + "x")


def test_key_prefix_parses_only_well_formed_keys():
    assert key_prefix("vrd_abcd1234_s3cr_et-") == "abcd1234"
    assert key_prefix("tok_a") is None
    assert key_prefix("vrd_short_x") is None
    assert key_prefix("") is None


# --- resolution -------------------------------------------------------------


def test_valid_key_resolves_to_its_project():
    generated = generate_key()
    row = StoredKey(id="k1", project_id="p1", key_hash=generated.key_hash)
    result = resolve_key(generated.key, store(row))
    assert result.ok
    assert result.key_id == "k1"
    assert result.project_id == "p1"


def test_unknown_revoked_and_deleted_project_give_identical_results():
    revoked = generate_key()
    orphan = generate_key()
    find = store(
        StoredKey(id="k1", project_id="p1", key_hash=revoked.key_hash, revoked_at="2026-10-01T00:00:00Z"),
        StoredKey(id="k2", project_id="p2", key_hash=orphan.key_hash, project_deleted_at="2026-10-01T00:00:00Z"),
    )
    results = [
        resolve_key(generate_key().key, find),
        resolve_key(revoked.key, find),
        resolve_key(orphan.key, find),
        resolve_key("", find),
        resolve_key("not-a-key", find),
    ]
    assert all(r is REJECTED for r in results)
    assert not REJECTED.ok and REJECTED.key_id is None and REJECTED.project_id is None


def test_row_whose_hash_does_not_match_is_rejected():
    # A buggy store returning the wrong row must not grant access.
    generated = generate_key()
    other = StoredKey(id="k9", project_id="p9", key_hash=hash_key("something else"))
    assert resolve_key(generated.key, lambda _h: other) is REJECTED


@pytest.mark.parametrize("case", ["unknown", "revoked", "valid"])
def test_every_path_runs_one_constant_time_comparison(monkeypatch, case):
    calls = []
    real = keys.hmac.compare_digest

    def spy(a, b):
        calls.append((len(a), len(b)))
        return real(a, b)

    monkeypatch.setattr(keys.hmac, "compare_digest", spy)
    generated = generate_key()
    revoked_at = "2026-10-01T00:00:00Z" if case == "revoked" else None
    rows = [] if case == "unknown" else [
        StoredKey(id="k1", project_id="p1", key_hash=generated.key_hash, revoked_at=revoked_at)
    ]
    resolve_key(generated.key, store(*rows))
    # Exactly one comparison, always between two equal-length hex digests.
    assert calls == [(64, 64)]


def test_store_is_queried_with_the_hash_never_the_key():
    seen = []
    generated = generate_key()
    resolve_key(generated.key, lambda h: seen.append(h))
    assert seen == [generated.key_hash]


# --- failure limiter --------------------------------------------------------


def test_limiter_blocks_after_threshold_with_retry_after():
    clock = FakeClock()
    limiter = FailureLimiter(max_failures=3, window_seconds=60, clock=clock)
    for _ in range(3):
        assert not limiter.check("ip:1.1.1.1").blocked
        limiter.record_failure("ip:1.1.1.1")
        clock.advance(1)
    decision = limiter.check("ip:1.1.1.1")
    assert decision.blocked
    # Oldest failure was at t=1000; it leaves the window at t=1060; now is 1003.
    assert decision.retry_after == pytest.approx(57)


def test_limiter_sources_are_independent():
    clock = FakeClock()
    limiter = FailureLimiter(max_failures=2, window_seconds=60, clock=clock)
    limiter.record_failure("ip:a")
    limiter.record_failure("ip:a")
    assert limiter.check("ip:a").blocked
    assert not limiter.check("ip:b").blocked


def test_limiter_unblocks_when_window_expires():
    clock = FakeClock()
    limiter = FailureLimiter(max_failures=2, window_seconds=60, clock=clock)
    limiter.record_failure("ip:a")
    clock.advance(30)
    limiter.record_failure("ip:a")
    assert limiter.check("ip:a").blocked
    clock.advance(30)  # first failure leaves the sliding window
    assert not limiter.check("ip:a").blocked
    limiter.record_failure("ip:a")
    assert limiter.check("ip:a").blocked


def test_limiter_has_no_success_reset():
    # Successes are not limited here and must not clear other failures.
    assert not hasattr(FailureLimiter, "record_success")
    assert not hasattr(FailureLimiter, "reset")


def test_limiter_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        FailureLimiter(max_failures=0, window_seconds=60)
    with pytest.raises(ValueError):
        FailureLimiter(max_failures=1, window_seconds=0)


def test_limiter_is_thread_safe():
    clock = FakeClock()
    limiter = FailureLimiter(max_failures=10_000, window_seconds=60, clock=clock)

    def hammer():
        for _ in range(500):
            limiter.record_failure("ip:a")

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert limiter.failure_count("ip:a") == 4000


def test_limiter_forgets_expired_sources():
    clock = FakeClock()
    limiter = FailureLimiter(max_failures=2, window_seconds=60, clock=clock)
    limiter.record_failure("ip:a")
    clock.advance(61)
    limiter.check("ip:a")
    assert limiter.failure_count("ip:a") == 0
    assert limiter.tracked_sources() == 0


# --- legacy import ----------------------------------------------------------


def test_legacy_import_produces_rows_that_resolve():
    rows = legacy_import({"tok_a": "client_a", "tok_b": "client_b"})
    assert {(r.project_id, r.key_hash) for r in rows} == {
        ("client_a", hash_key("tok_a")),
        ("client_b", hash_key("tok_b")),
    }
    assert all(r.prefix == "legacy" and r.created_by == "migration" for r in rows)
    assert all("tok_" not in repr(r) for r in rows)
    find = store(*(StoredKey(id=str(i), project_id=r.project_id, key_hash=r.key_hash) for i, r in enumerate(rows)))
    assert resolve_key("tok_a", find).project_id == "client_a"


def test_legacy_import_is_idempotent_by_hash():
    tokens = {"tok_a": "client_a", "tok_b": "client_b"}
    first = legacy_import(tokens)
    assert legacy_import(tokens) == first
    assert legacy_import(tokens, existing_hashes={r.key_hash for r in first}) == []
    partial = legacy_import(tokens, existing_hashes={hash_key("tok_a")})
    assert [r.project_id for r in partial] == ["client_b"]


def test_legacy_import_strips_whitespace_like_settings_parser():
    rows = legacy_import({" tok_a ": " client_a "})
    assert rows[0].key_hash == hash_key("tok_a")
    assert rows[0].project_id == "client_a"
