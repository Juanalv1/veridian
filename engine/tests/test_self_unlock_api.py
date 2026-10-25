"""T4: the endpoints behind the panel's public confirmation page. The token is the user's proof;
the caller is the panel's service credential. Reading never changes anything; only POST unlocks."""

import json
from datetime import timedelta

import pytest
from conftest import AUTH_A, PANEL_AUTH, acting, as_panel
from test_self_unlock_email import (  # noqa: F401 - app_factory is a fixture
    USER_EMAIL,
    app_factory,
    blocked,
    configure,
    link_token,
    owner,
    run_sender,
    sent,
)

from veridian_engine import db

HEADER = "X-Veridian-Unlock-Token"


@pytest.fixture
def unlock(app_factory):
    """An engine with one blocked user whose email was sent: (client, token)."""
    client = app_factory()
    configure(client)
    blocked(client)
    run_sender(client)
    return client, link_token(sent(client)[0])


def get_info(client, token):
    return client.get("/v1/self-unlock", headers={**PANEL_AUTH, HEADER: token})


def post_unlock(client, token):
    return client.post("/v1/self-unlock", headers={**PANEL_AUTH, HEADER: token})


def blocklist(client):
    return client.get("/v1/blocklist", headers=AUTH_A).json()


def decision_status(client):
    [decision] = client.get("/v1/decisions", headers=as_panel(client)).json()["decisions"]
    return decision["status"]


def audit(client, action):
    with db.connect(client.app.state.settings.db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log WHERE action = ?", (action,))]


def test_get_shows_brand_and_access_detail_and_never_changes_state(unlock):
    client, token = unlock
    before = blocklist(client)

    for _ in range(3):  # mail scanners open links
        response = get_info(client, token)

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "valid"
    assert body["brand"] == {"name": "Acme Pay", "logo_url": "https://cdn.acme.example/logo.png",
                             "color": "#1F6FEB", "support_url": None, "support_email": "soporte@acme.example"}
    assert body["access"] == {"time": "26 de octubre de 2026, 12:00 UTC", "device": "Chrome en Windows"}
    assert body["expires_at"] == "2026-10-27T12:00:00Z"
    assert blocklist(client) == before and decision_status(client) == "open"
    assert audit(client, "decision.self_unlocked") == []
    with db.connect(client.app.state.settings.db_path) as conn:
        assert conn.execute("SELECT used_at FROM self_unlock_token").fetchone()[0] is None


def test_the_page_data_carries_no_full_ip_and_nothing_personal(unlock):
    client, token = unlock
    text = json.dumps(get_info(client, token).json()) + json.dumps(post_unlock(client, token).json())
    for forbidden in ("203.0.113.9", "190.202.14.7", "203.0.113", USER_EMAIL, "u_42"):
        assert forbidden not in text


def test_post_marks_the_decision_normal_lifts_the_block_and_bumps_the_version(unlock):
    client, token = unlock
    before = blocklist(client)
    assert before["sessions"] == ["s_evil"] and before["ips"] == ["203.0.113.9"]

    response = post_unlock(client, token)

    assert response.status_code == 200 and response.json()["unlocked"] is True
    after = blocklist(client)
    assert after["version"] == before["version"] + 1
    assert after["sessions"] == [] and after["ips"] == []
    assert decision_status(client) == "marked_normal"
    profile = client.get("/v1/users/u_42/profile", headers=as_panel(client)).json()
    assert profile["state"] != "blocked"


def test_the_unlock_is_audited_as_self_unlocked_not_as_an_admin_action(unlock):
    client, token = unlock
    post_unlock(client, token)

    [entry] = audit(client, "decision.self_unlocked")
    assert entry["actor_kind"] == "system" and entry["actor_person_id"] is None
    assert entry["project_id"] == "client_a" and entry["org_id"] and entry["target_type"] == "decision"
    assert entry["target_id"].startswith("d_")
    assert audit(client, "decision.marked_normal") == []
    listed = client.get(f"/v1/orgs/{entry['org_id']}/audit", headers=acting(owner(client))).json()["entries"]
    assert [e["action"] for e in listed if e["action"].startswith("decision.")] == ["decision.self_unlocked"]


def test_a_token_works_once(unlock):
    client, token = unlock
    assert post_unlock(client, token).status_code == 200
    version = blocklist(client)["version"]

    again = post_unlock(client, token)

    assert again.status_code == 409 and again.json() == {"error": "used"}
    assert get_info(client, token).json()["state"] == "used"
    assert blocklist(client)["version"] == version
    assert len(audit(client, "decision.self_unlocked")) == 1


def test_an_expired_token_is_an_error_and_unlocks_nothing(unlock, clock):
    client, token = unlock
    clock.now += timedelta(hours=24, seconds=1)

    assert get_info(client, token).json()["state"] == "expired"
    response = post_unlock(client, token)

    assert response.status_code == 409 and response.json() == {"error": "expired"}
    assert decision_status(client) == "open" and blocklist(client)["sessions"] == ["s_evil"]
    assert audit(client, "decision.self_unlocked") == []


def test_a_token_is_still_valid_just_before_it_expires(unlock, clock):
    client, token = unlock
    clock.now += timedelta(hours=23, minutes=59)
    assert post_unlock(client, token).status_code == 200


def test_a_decision_an_admin_already_marked_normal_closes_the_token(unlock):
    client, token = unlock
    [decision] = client.get("/v1/decisions", headers=as_panel(client)).json()["decisions"]
    assert client.post(f"/v1/decisions/{decision['id']}/normal", headers=as_panel(client)).status_code == 200
    version = blocklist(client)["version"]

    assert get_info(client, token).json()["state"] == "closed"
    response = post_unlock(client, token)

    assert response.status_code == 409 and response.json() == {"error": "closed"}
    assert audit(client, "decision.self_unlocked") == [] and blocklist(client)["version"] == version


def test_an_erased_decision_closes_the_token(unlock):
    client, token = unlock
    with db.connect(client.app.state.settings.db_path) as conn:
        conn.execute("DELETE FROM decisions")
    assert get_info(client, token).json()["state"] == "closed"
    assert post_unlock(client, token).status_code == 409


@pytest.mark.parametrize("token", ["nope", "x" * 43, ""])
def test_an_unknown_token_is_not_found(unlock, token):
    client, _ = unlock
    assert get_info(client, token).status_code == 404
    assert post_unlock(client, token).status_code == 404
    assert decision_status(client) == "open"


def test_a_missing_token_header_is_not_found(unlock):
    client, _ = unlock
    assert client.get("/v1/self-unlock", headers=PANEL_AUTH).status_code == 404
    assert client.post("/v1/self-unlock", headers=PANEL_AUTH).status_code == 404


def test_only_the_panel_credential_may_call(unlock):
    client, token = unlock
    assert client.get("/v1/self-unlock", headers={**AUTH_A, HEADER: token}).status_code == 403  # an SDK key
    assert client.post("/v1/self-unlock", headers={**AUTH_A, HEADER: token}).status_code == 403
    assert client.post("/v1/self-unlock", headers={HEADER: token}).status_code == 401
    assert client.post("/v1/self-unlock", headers={"Authorization": "Bearer wrong", HEADER: token}).status_code == 401
    assert decision_status(client) == "open"


def test_a_token_cannot_unlock_another_projects_decision(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    run_sender(client)
    token = link_token(sent(client)[0])
    # The token carries its own project: the project header the panel might send is ignored.
    response = client.post("/v1/self-unlock", headers={**PANEL_AUTH, HEADER: token, "X-Veridian-Project": "client_b"})
    assert response.status_code == 200
    with db.connect(client.app.state.settings.db_path) as conn:
        assert conn.execute("SELECT normal FROM decisions WHERE project_id = 'client_a'").fetchone()[0] == 1
