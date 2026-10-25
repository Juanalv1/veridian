from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH_A, AUTH_B, NOW, days_ago, make_event, make_settings, post_events
from veridian_engine.api import create_app


RESET_TOKEN = "rehearsal-secret"
RESET = {"X-Veridian-Reset-Token": RESET_TOKEN}
AUTH_A_RESET = {**AUTH_A, **RESET}


@pytest.fixture
def reset_client(tmp_path, clock) -> TestClient:
    with TestClient(create_app(make_settings(tmp_path, clock, reset_token=RESET_TOKEN))) as test_client:
        yield test_client


def seed_history_and_block(client, headers=AUTH_A, prefix=""):
    history = [make_event(f"{prefix}h{day}", days_ago(day, hour=9)) for day in range(1, 15)]
    attack = [
        make_event(f"{prefix}a1", NOW - timedelta(seconds=30), route="/login", session_id="s_evil", ip="203.0.113.9"),
        make_event(
            f"{prefix}a2", NOW, method="POST", route="/payouts/bank-account", session_id="s_evil", ip="203.0.113.9"
        ),
    ]
    assert post_events(client, history, headers=headers).status_code == 202
    assert post_events(client, attack, headers=headers).status_code == 202


def test_reset_is_invisible_when_disabled(client):
    response = client.post("/v1/admin/reset", headers=AUTH_A)
    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}
    # Not even an unauthenticated caller learns that the endpoint exists.
    assert client.post("/v1/admin/reset").status_code == 404


def test_reset_requires_the_bearer_token_when_enabled(reset_client):
    assert reset_client.post("/v1/admin/reset", headers=RESET).status_code == 401


@pytest.mark.parametrize("headers", [{}, {"X-Veridian-Reset-Token": "wrong"}, {"X-Veridian-Reset-Token": ""}])
def test_sdk_key_alone_cannot_reset(reset_client, headers):
    seed_history_and_block(reset_client, AUTH_A)
    response = reset_client.post("/v1/admin/reset", headers={**AUTH_A, **headers})
    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}
    # Nothing was wiped.
    assert reset_client.get("/v1/blocklist", headers=AUTH_A).json()["version"] == 1


def test_reset_header_without_bearer_does_not_reveal_the_endpoint(reset_client):
    assert reset_client.post("/v1/admin/reset", headers={"X-Veridian-Reset-Token": "wrong"}).status_code == 404


def test_reset_header_is_ignored_when_no_secret_is_configured(client):
    assert client.post("/v1/admin/reset", headers={**AUTH_A, **RESET}).status_code == 404
    assert client.post("/v1/admin/reset", headers={**AUTH_A, "X-Veridian-Reset-Token": ""}).status_code == 404


def test_reset_wipes_only_the_callers_client(reset_client):
    seed_history_and_block(reset_client, AUTH_A)
    seed_history_and_block(reset_client, AUTH_B)
    old_version = reset_client.get("/v1/blocklist", headers=AUTH_A).json()["version"]
    assert old_version == 1
    b_before = {
        "profile": reset_client.get("/v1/users/u_42/profile", headers=AUTH_B).json(),
        "decisions": reset_client.get("/v1/decisions", headers=AUTH_B).json(),
        "blocklist": reset_client.get("/v1/blocklist", headers=AUTH_B).json(),
    }

    response = reset_client.post("/v1/admin/reset", headers=AUTH_A_RESET)
    assert response.status_code == 200
    assert response.json() == {"reset": True, "version": old_version + 1}

    profile = reset_client.get("/v1/users/u_42/profile", headers=AUTH_A).json()
    assert profile == {
        "user_id": "u_42",
        "history_days": 0,
        "networks": [],
        "devices": [],
        "hours": [],
        "routes": [],
        "usual_max_rate_per_min": 0,
        "state": "normal",
    }
    assert reset_client.get("/v1/decisions", headers=AUTH_A).json() == {"decisions": []}
    assert reset_client.get("/v1/blocklist", headers=AUTH_A).json() == {
        "version": old_version + 1,
        "users": [],
        "sessions": [],
        "ips": [],
    }
    # A middleware polling with the old version gets the new empty list, never a stale 304.
    polled = reset_client.get("/v1/blocklist", headers=AUTH_A, params={"since": old_version})
    assert polled.status_code == 200
    assert polled.json()["sessions"] == []

    assert reset_client.get("/v1/users/u_42/profile", headers=AUTH_B).json() == b_before["profile"]
    assert reset_client.get("/v1/decisions", headers=AUTH_B).json() == b_before["decisions"]
    assert reset_client.get("/v1/blocklist", headers=AUTH_B).json() == b_before["blocklist"]


def test_reset_client_accepts_the_same_event_ids_again(reset_client):
    seed_history_and_block(reset_client, AUTH_A)
    assert reset_client.post("/v1/admin/reset", headers=AUTH_A_RESET).status_code == 200
    history = [make_event(f"h{day}", days_ago(day, hour=9)) for day in range(1, 15)]
    assert post_events(reset_client, history).json() == {"accepted": 14}
