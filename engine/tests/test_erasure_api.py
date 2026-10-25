"""U13: contract 14 endpoints (erase one end user, erase a project's data) and the purge worker."""

from datetime import timedelta

import pytest
from conftest import (
    AUTH_A,
    AUTH_B,
    NOW,
    TEST_OWNER_EMAIL,
    FakeClock,
    acting,
    as_panel,
    days_ago,
    login,
    make_event,
    make_settings,
    post_events,
)
from fastapi.testclient import TestClient

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.control_plane import repository
from veridian_engine.control_plane.legacy import DEMO_ORG_ID
from veridian_engine.retention_worker import RetentionWorker


@pytest.fixture
def client(tmp_path, clock):
    with TestClient(create_app(make_settings(tmp_path, clock))) as test_client:
        yield test_client


def owner(client):
    return login(client, "sub-test-owner", TEST_OWNER_EMAIL)["person"]["id"]


def member_as(client, role, sub="sub-m", email="m@example.com"):
    owner_id = owner(client)
    with db.connect(client.app.state.settings.db_path) as conn:
        repository.create_invitation(conn, actor_id=owner_id, org_id=DEMO_ORG_ID, email=email, role=role, now=NOW)
    return login(client, sub, email)["person"]["id"]


def attack(client, headers=AUTH_A, user_id="u_42"):
    post_events(client, [make_event(f"h{i}", days_ago(i), user_id=user_id) for i in range(1, 4)], headers=headers)
    post_events(client, [make_event("x1", NOW, user_id=user_id, ip="203.0.113.9", session_id="s_att", method="POST",
                                    route="/payouts/bank-account")], headers=headers)


def audit(client, action):
    with db.connect(client.app.state.settings.db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log WHERE action = ? ORDER BY seq", (action,))]


# --- DELETE /v1/projects/{id}/users/{user_id} ---


def test_an_admin_erases_one_end_user_and_lifts_their_block(client):
    admin = member_as(client, "admin")
    attack(client)
    attack(client, user_id="u_7")
    before = client.get("/v1/blocklist", headers=AUTH_A).json()["version"]
    response = client.delete("/v1/projects/client_a/users/u_42", headers=acting(admin))
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"]["events"] == 4 and body["deleted"]["decisions"] == 1 and body["version"] > before
    users = [u["user_id"] for u in client.get("/v1/users", headers=as_panel(client, AUTH_A)).json()["users"]]
    assert "u_42" not in users and "u_7" in users
    [row] = audit(client, "user_data.delete")
    assert row["actor_person_id"] == admin and row["target_id"] != "u_42" and "u_42" not in row["meta"]


def test_a_viewer_cannot_erase_a_user(client):
    viewer = member_as(client, "viewer")
    attack(client)
    assert client.delete("/v1/projects/client_a/users/u_42", headers=acting(viewer)).status_code == 403
    assert audit(client, "user_data.delete") == []


def test_erasing_a_user_never_touches_another_project(client):
    attack(client)
    attack(client, headers=AUTH_B)
    client.delete("/v1/projects/client_a/users/u_42", headers=acting(owner(client)))
    assert len(client.get("/v1/decisions", headers=as_panel(client, AUTH_B)).json()["decisions"]) == 1


def test_the_sdk_key_cannot_erase(client):
    assert client.delete("/v1/projects/client_a/users/u_42", headers=AUTH_A).status_code == 403
    assert client.delete("/v1/projects/client_a/data", headers=AUTH_A).status_code == 403


# --- DELETE /v1/projects/{id}/data ---


def test_only_an_owner_erases_the_project_data(client):
    admin = member_as(client, "admin")
    attack(client)
    assert client.delete("/v1/projects/client_a/data", headers=acting(admin)).status_code == 403
    response = client.delete("/v1/projects/client_a/data", headers=acting(owner(client)))
    assert response.status_code == 200
    assert response.json()["deleted"] == {"events": 4, "decisions": 1}
    blocklist = client.get("/v1/blocklist", headers=AUTH_A).json()
    assert blocklist["sessions"] == [] and blocklist["version"] == response.json()["version"]
    assert client.get("/v1/decisions", headers=as_panel(client, AUTH_A)).json()["decisions"] == []
    assert client.get("/v1/projects/client_a", headers=acting(owner(client))).status_code == 200
    assert len(audit(client, "project_data.delete")) == 1


def test_erasing_the_project_data_keeps_keys_and_routes(client):
    owner_id = owner(client)
    routes = client.get("/v1/routes", headers=AUTH_A).json()
    client.delete("/v1/projects/client_a/data", headers=acting(owner_id))
    assert client.get("/v1/routes", headers=AUTH_A).json() == routes
    assert post_events(client, [make_event("n1", NOW)]).status_code == 202


# --- purge worker ---


def worker_for(client, clock) -> RetentionWorker:
    return RetentionWorker(client.app.state.settings.db_path, clock=clock, interval_s=3600)


def test_the_worker_purges_expired_rows_with_each_project_retention(client, clock):
    owner_id = owner(client)
    client.patch("/v1/projects/client_b", json={"retention_days": 1}, headers=acting(owner_id))
    for headers in (AUTH_A, AUTH_B):
        post_events(client, [make_event("old", days_ago(5)), make_event("new", NOW)], headers=headers)
    worker_for(client, clock).run_once()
    with db.connect(client.app.state.settings.db_path) as conn:
        left = {(r[0], r[1]) for r in conn.execute("SELECT project_id, id FROM events")}
    assert ("client_a", "old") in left and ("client_b", "old") not in left and ("client_b", "new") in left


def test_the_worker_wipes_the_data_of_a_deleted_project_once(client, clock):
    owner_id = owner(client)
    attack(client)
    assert client.delete("/v1/projects/client_a", headers=acting(owner_id)).status_code == 204
    worker = worker_for(client, clock)
    worker.run_once()
    worker.run_once()
    with db.connect(client.app.state.settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE project_id = 'client_a'").fetchone()[0] == 0
    assert len(audit(client, "project_data.delete")) == 1


def test_the_worker_waits_one_interval_before_the_first_pass(tmp_path):
    clock = FakeClock(NOW)
    settings = make_settings(tmp_path, clock)
    worker = RetentionWorker(settings.db_path, clock=clock, interval_s=3600)
    db.init_db(settings.db_path)
    worker.start()
    try:
        assert worker.passes == 0
    finally:
        worker.stop()


def test_the_worker_runs_with_the_app_and_can_be_turned_off(tmp_path, clock):
    with TestClient(create_app(make_settings(tmp_path, clock))) as client:
        assert client.app.state.retention_worker.is_alive()
    assert not client.app.state.retention_worker.is_alive()
    with TestClient(create_app(make_settings(tmp_path / "off", clock, retention=False))) as client:
        assert client.app.state.retention_worker is None


def test_the_worker_survives_a_failing_pass(tmp_path):
    worker = RetentionWorker(str(tmp_path / "missing" / "nope.db"), clock=FakeClock(NOW), interval_s=3600)
    assert worker.run_once() == {}
