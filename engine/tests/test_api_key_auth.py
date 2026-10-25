import sqlite3
from datetime import timedelta

import pytest
from conftest import AUTH_A, AUTH_B, TOKEN_A, make_event, make_settings, post_events
from fastapi.testclient import TestClient

from veridian_engine.api import create_app
from veridian_engine.keys import hash_key

SDK_GET = "/v1/blocklist"


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_client(tmp_path, clock):
    clients = []

    def make(**overrides) -> TestClient:
        client = TestClient(create_app(make_settings(tmp_path, clock, **overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def execute(tmp_path, sql, params=()):
    conn = sqlite3.connect(tmp_path / "veridian.db")
    try:
        result = conn.execute(sql, params).fetchall()
        conn.commit()
        return result
    finally:
        conn.close()


def test_legacy_token_authenticates_as_its_project(make_client, tmp_path, clock):
    client = make_client()

    response = post_events(client, [make_event("e1", clock())], headers=AUTH_A)

    assert response.status_code == 202
    assert execute(tmp_path, "SELECT project_id FROM events") == [("client_a",)]
    assert client.get(SDK_GET, headers=AUTH_B).status_code == 200


@pytest.mark.parametrize("case", ["unknown", "revoked", "deleted_project", "missing", "not_bearer", "well_formed"])
def test_every_rejected_key_gets_the_same_401(make_client, tmp_path, case):
    client = make_client()
    headers = AUTH_A
    if case == "unknown":
        headers = bearer("tok_nobody")
    elif case == "revoked":
        execute(tmp_path, "UPDATE api_key SET revoked_at = '2026-10-26T12:00:00Z' WHERE key_hash = ?", (hash_key(TOKEN_A),))
    elif case == "deleted_project":
        execute(tmp_path, "UPDATE project SET deleted_at = '2026-10-26T12:00:00Z' WHERE id = 'client_a'")
    elif case == "missing":
        headers = {}
    elif case == "not_bearer":
        headers = {"Authorization": f"Basic {TOKEN_A}"}
    elif case == "well_formed":
        headers = bearer("vrd_0123abcd_" + "x" * 43)

    response = client.get(SDK_GET, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert "retry-after" not in response.headers


def test_revocation_applies_to_the_next_request(make_client, tmp_path):
    client = make_client()
    assert client.get(SDK_GET, headers=AUTH_A).status_code == 200

    execute(tmp_path, "UPDATE api_key SET revoked_at = '2026-10-26T12:00:00Z' WHERE key_hash = ?", (hash_key(TOKEN_A),))

    assert client.get(SDK_GET, headers=AUTH_A).status_code == 401
    assert client.get(SDK_GET, headers=AUTH_B).status_code == 200


def test_failures_from_one_ip_get_429_with_retry_after(make_client, clock):
    client = make_client(auth_max_failures_per_ip=3, auth_failure_window_s=60)
    for _ in range(3):
        assert client.get(SDK_GET, headers=bearer("tok_wrong")).status_code == 401

    blocked = client.get(SDK_GET, headers=bearer("tok_wrong"))
    # The limit applies before any key work, so even a valid key waits.
    valid_while_blocked = client.get(SDK_GET, headers=AUTH_A)

    assert blocked.status_code == 429
    assert blocked.json() == {"error": "rate_limited"}
    assert blocked.headers["retry-after"] == "60"
    assert valid_while_blocked.status_code == 429


def test_rate_limit_recovers_after_the_window(make_client, clock):
    client = make_client(auth_max_failures_per_ip=2, auth_failure_window_s=60)
    for _ in range(2):
        client.get(SDK_GET, headers=bearer("tok_wrong"))
    clock.now += timedelta(seconds=30)
    assert client.get(SDK_GET, headers=AUTH_A).headers["retry-after"] == "30"

    clock.now += timedelta(seconds=31)

    assert client.get(SDK_GET, headers=AUTH_A).status_code == 200


def test_failures_on_one_key_prefix_get_429(make_client):
    client = make_client(auth_max_failures_per_ip=100, auth_max_failures_per_prefix=2, auth_failure_window_s=60)
    guess = "vrd_0123abcd_"
    for attempt in range(2):
        assert client.get(SDK_GET, headers=bearer(f"{guess}{attempt}" + "x" * 42)).status_code == 401

    same_prefix = client.get(SDK_GET, headers=bearer(f"{guess}9" + "x" * 42))
    other_prefix = client.get(SDK_GET, headers=bearer("vrd_ffff0000_" + "x" * 43))
    legacy_token = client.get(SDK_GET, headers=AUTH_A)

    assert same_prefix.status_code == 429
    assert other_prefix.status_code == 401
    assert legacy_token.status_code == 200


def test_successes_are_not_counted_as_failures(make_client):
    client = make_client(auth_max_failures_per_ip=2, auth_failure_window_s=60)

    statuses = [client.get(SDK_GET, headers=AUTH_A).status_code for _ in range(5)]

    assert statuses == [200] * 5


def test_last_used_at_is_recorded_at_most_once_a_minute(make_client, tmp_path, clock):
    client = make_client()

    def last_used():
        return execute(tmp_path, "SELECT last_used_at FROM api_key WHERE key_hash = ?", (hash_key(TOKEN_A),))[0][0]

    assert last_used() is None
    client.get(SDK_GET, headers=AUTH_A)
    first = last_used()
    clock.now += timedelta(seconds=30)
    client.get(SDK_GET, headers=AUTH_A)
    unchanged = last_used()
    clock.now += timedelta(seconds=31)
    client.get(SDK_GET, headers=AUTH_A)

    assert first == "2026-10-26T12:00:00Z"
    assert unchanged == first
    assert last_used() == "2026-10-26T12:01:01Z"


def test_keys_of_two_projects_stay_isolated(make_client, clock):
    client = make_client()
    post_events(client, [make_event("e1", clock(), user_id="u_only_a")], headers=AUTH_A)

    users_b = client.get("/v1/users", headers=AUTH_B).json()

    assert "u_only_a" not in str(users_b)


def test_behind_trusted_proxies_failures_count_per_real_client(make_client):
    # Every request arrives from the same proxy; the client IP comes from X-Forwarded-For.
    client = make_client(auth_max_failures_per_ip=3, auth_failure_window_s=60, trusted_proxies=1)
    attacker = {**bearer("tok_wrong"), "X-Forwarded-For": "203.0.113.9"}
    for _ in range(4):
        client.get(SDK_GET, headers=attacker)

    assert client.get(SDK_GET, headers=attacker).status_code == 429
    sdk = {**AUTH_A, "X-Forwarded-For": "190.202.14.7"}
    assert client.get(SDK_GET, headers=sdk).status_code == 200


def test_a_spoofed_left_entry_does_not_escape_the_limit(make_client):
    client = make_client(auth_max_failures_per_ip=3, auth_failure_window_s=60, trusted_proxies=1)
    for n in range(4):
        spoofed = {**bearer("tok_wrong"), "X-Forwarded-For": f"10.0.0.{n}, 203.0.113.9"}
        client.get(SDK_GET, headers=spoofed)

    again = {**bearer("tok_wrong"), "X-Forwarded-For": "10.0.0.99, 203.0.113.9"}
    assert client.get(SDK_GET, headers=again).status_code == 429
