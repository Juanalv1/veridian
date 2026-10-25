"""Panel principal, org-level roles, contract 10 (people, orgs, projects), contract 12 (API keys)
and mark-normal with role and audit. Wire shapes follow the fake engine (edge/fake_engine)."""

import json
import re
import sqlite3
from datetime import timedelta

import pytest
from conftest import (
    AUTH_A,
    AUTH_B,
    NOW,
    PANEL_AUTH,
    PANEL_TOKEN,
    TEST_OWNER_EMAIL,
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

CREATOR = "creator@example.com"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path, clock):
    settings = make_settings(tmp_path, clock, org_creators=frozenset({CREATOR}))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def owner(client):
    return login(client, "sub-test-owner", TEST_OWNER_EMAIL)["person"]["id"]


def member_as(client, role, sub="sub-m", email="m@example.com"):
    """A person with `role` in org Demo (which holds client_a and client_b), through an invitation."""
    owner_id = owner(client)
    with db.connect(client.app.state.settings.db_path) as conn:
        repository.create_invitation(conn, actor_id=owner_id, org_id=DEMO_ORG_ID, email=email, role=role, now=NOW)
    return login(client, sub, email)["person"]["id"]


def other_org_project(client):
    """A project in an org of its own (created by an allowlisted creator). Returns (creator id, project id)."""
    creator = login(client, "sub-creator", CREATOR)["person"]["id"]
    org = client.post("/v1/orgs", json={"name": "Acme"}, headers=acting(creator)).json()
    project = client.post(f"/v1/orgs/{org['id']}/projects", json={"name": "Shop"}, headers=acting(creator)).json()
    return creator, project["id"]


def audit(client, action):
    with db.connect(client.app.state.settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM audit_log WHERE action = ? ORDER BY seq", (action,)).fetchall()
    return [dict(row) for row in rows]


def block_decision(client, headers=AUTH_A):
    """Learn the owner, then an attacker hits a critical route from a new network: one open block."""
    post_events(client, [make_event(f"h{i}", days_ago(i)) for i in range(1, 4)], headers=headers)
    attack = make_event("x1", NOW, ip="203.0.113.9", session_id="s_att", method="POST", route="/payouts/bank-account")
    post_events(client, [attack], headers=headers)
    [decision] = client.get("/v1/decisions", headers=as_panel(client, headers)).json()["decisions"]
    return decision


# --- who may call what ---


@pytest.mark.parametrize("path", ["/v1/decisions", "/v1/activity", "/v1/summary", "/v1/users", "/v1/users/u_1/profile"])
def test_an_sdk_key_cannot_read_panel_data(client, path):
    response = client.get(path, headers=AUTH_A)
    assert response.status_code == 403 and response.json() == {"error": "forbidden"}


def test_an_sdk_key_cannot_mark_a_decision_normal(client):
    response = client.post("/v1/decisions/d_1/normal", headers=AUTH_A)
    assert response.status_code == 403 and response.json() == {"error": "forbidden"}


def test_an_sdk_key_still_reaches_the_sdk_endpoints(client):
    assert post_events(client, [make_event("e1", NOW)]).status_code == 202
    assert client.post("/v1/check", json={"event": make_event("e2", NOW)}, headers=AUTH_A).status_code == 200
    assert client.get("/v1/blocklist", headers=AUTH_A).status_code == 200
    assert client.get("/v1/routes", headers=AUTH_A).status_code == 200


@pytest.mark.parametrize("method,path", [("post", "/v1/events"), ("post", "/v1/check")])
def test_the_panel_credential_does_not_work_on_sdk_only_endpoints(client, method, path):
    response = getattr(client, method)(path, json={}, headers=as_panel(client))
    assert response.status_code == 403 and response.json() == {"error": "forbidden"}


def test_unknown_or_missing_credentials_are_401_on_panel_endpoints(client):
    for headers in ({}, bearer("nope")):
        response = client.get("/v1/decisions", headers=headers)
        assert response.status_code == 401 and response.json() == {"error": "unauthorized"}


def test_the_panel_credential_needs_a_known_actor(client):
    for extra in ({}, {"X-Veridian-Actor": "per_nope"}):
        response = client.get("/v1/decisions", headers={**PANEL_AUTH, "X-Veridian-Project": "client_a", **extra})
        assert response.status_code == 403 and response.json() == {"error": "forbidden"}


def test_data_reads_need_a_project_header(client):
    response = client.get("/v1/decisions", headers=acting(owner(client)))
    assert response.status_code == 400 and response.json() == {"error": "project_required"}


def test_a_non_member_gets_403_and_no_data(client):
    owner(client)  # takes the orphan org
    stranger = login(client, "sub-x", "x@example.com")["person"]["id"]
    for path in ("/v1/decisions", "/v1/activity", "/v1/summary", "/v1/users", "/v1/users/u_42/profile",
                 "/v1/blocklist", "/v1/routes"):
        response = client.get(path, headers=acting(stranger, "client_a"))
        assert response.status_code == 403 and response.json() == {"error": "forbidden"}, path
    assert client.post("/v1/decisions/d_1/normal", headers=acting(stranger, "client_a")).status_code == 403


def test_an_unknown_project_is_403_like_a_foreign_one(client):
    assert client.get("/v1/decisions", headers=acting(owner(client), "p_nope")).status_code == 403


def test_two_panel_credentials_are_accepted_to_rotate_without_a_cut(tmp_path, clock):
    settings = make_settings(tmp_path, clock, panel_tokens=("new-one", "old-one"))
    with TestClient(create_app(settings)) as c:
        body = {"google_sub": "s", "email": "a@example.com"}
        for token in ("new-one", "old-one"):
            assert c.post("/v1/people/login", json=body, headers=bearer(token)).status_code == 200
        assert c.post("/v1/people/login", json=body, headers=bearer(PANEL_TOKEN)).status_code == 401
        assert c.post("/v1/people/login", json=body, headers=AUTH_A).status_code == 403


def test_no_panel_credential_configured_means_no_panel(tmp_path, clock):
    with TestClient(create_app(make_settings(tmp_path, clock, panel_tokens=()))) as c:
        body = {"google_sub": "s", "email": "a@example.com"}
        assert c.post("/v1/people/login", json=body, headers=PANEL_AUTH).status_code == 401
        assert c.post("/v1/people/login", json=body, headers=bearer("")).status_code == 401


# --- roles on data endpoints ---


def test_a_viewer_reads_but_cannot_mark_normal(client):
    viewer = member_as(client, "viewer")
    for path in ("/v1/decisions", "/v1/activity", "/v1/summary", "/v1/users", "/v1/users/u_42/profile",
                 "/v1/blocklist", "/v1/routes"):
        assert client.get(path, headers=acting(viewer, "client_a")).status_code == 200, path
    response = client.post("/v1/decisions/d_1/normal", headers=acting(viewer, "client_a"))
    assert response.status_code == 403 and response.json() == {"error": "forbidden"}


def test_an_admin_can_mark_normal(client):
    admin = member_as(client, "admin")
    # role is enough: a missing decision is a 404, not a 403
    assert client.post("/v1/decisions/d_nope/normal", headers=acting(admin, "client_a")).status_code == 404


def test_an_org_role_applies_to_every_project_of_that_org_and_to_no_other(client):
    viewer = member_as(client, "viewer", sub="s-v", email="v@example.com")
    _, foreign = other_org_project(client)
    for project_id in ("client_a", "client_b"):
        assert client.get("/v1/decisions", headers=acting(viewer, project_id)).status_code == 200
    assert client.get("/v1/decisions", headers=acting(viewer, foreign)).status_code == 403


def test_panel_reads_are_scoped_to_the_project_header(client):
    post_events(client, [make_event("e1", NOW)])
    person = owner(client)
    a = client.get("/v1/activity", headers=acting(person, "client_a")).json()["activity"]
    b = client.get("/v1/activity", headers=acting(person, "client_b")).json()["activity"]
    assert [item["event_id"] for item in a] == ["e1"] and b == []


def test_mark_normal_is_audited_with_the_actor(client):
    decision = block_decision(client)
    admin = member_as(client, "admin")

    response = client.post(f"/v1/decisions/{decision['id']}/normal", headers=acting(admin, "client_a"))

    assert response.status_code == 200
    [row] = audit(client, "decision.mark_normal")
    assert (row["org_id"], row["project_id"], row["actor_person_id"], row["actor_kind"]) == (
        DEMO_ORG_ID, "client_a", admin, "person")
    assert (row["target_type"], row["target_id"]) == ("decision", decision["id"])
    assert client.get("/v1/blocklist", headers=AUTH_A).json()["sessions"] == []


def test_a_denied_or_missing_mark_normal_writes_no_audit(client):
    decision = block_decision(client)
    viewer = member_as(client, "viewer")
    assert client.post(f"/v1/decisions/{decision['id']}/normal", headers=acting(viewer, "client_a")).status_code == 403
    assert client.post("/v1/decisions/d_nope/normal", headers=as_panel(client)).status_code == 404
    assert audit(client, "decision.mark_normal") == []
    assert client.get("/v1/blocklist", headers=AUTH_A).json()["sessions"] == ["s_att"]


# --- POST /v1/people/login ---


def test_login_creates_a_person_with_no_orgs(client):
    body = login(client, "s1", "Ana@Example.com", "Ana")
    assert body["person"]["email"] == "ana@example.com" and body["person"]["name"] == "Ana"
    assert body["person"]["id"].startswith("per_") and body["orgs"] == []
    assert set(body) == {"person", "orgs"} and set(body["person"]) == {"id", "email", "name"}


def test_login_is_an_upsert_by_google_sub(client):
    first = login(client, "s1", "old@example.com", "Old")
    again = login(client, "s1", "new@example.com", None)
    assert again["person"]["id"] == first["person"]["id"]
    assert again["person"]["email"] == "new@example.com" and again["person"]["name"] == "Old"


def test_login_requires_the_panel_credential(client):
    body = {"google_sub": "s", "email": "a@example.com"}
    assert client.post("/v1/people/login", json=body).status_code == 401
    assert client.post("/v1/people/login", json=body, headers=bearer("nope")).status_code == 401
    assert client.post("/v1/people/login", json=body, headers=AUTH_A).status_code == 403


def test_login_validates_the_body(client):
    assert client.post("/v1/people/login", json={"email": "a@example.com"}, headers=PANEL_AUTH).status_code == 422
    body = {"google_sub": "", "email": "a@example.com"}
    assert client.post("/v1/people/login", json=body, headers=PANEL_AUTH).status_code == 422


def test_bootstrap_owner_takes_org_demo_with_the_legacy_projects(client):
    body = login(client, "sub-test-owner", TEST_OWNER_EMAIL)
    assert body["orgs"] == [{"id": DEMO_ORG_ID, "name": "Demo", "role": "owner",
                             "projects": [{"id": "client_a", "name": "client_a"}, {"id": "client_b", "name": "client_b"}]}]
    [row] = audit(client, "org.bootstrap_owner")
    assert row["org_id"] == DEMO_ORG_ID and row["target_id"] == body["person"]["id"]


def test_bootstrap_only_acts_on_orgs_without_an_owner(tmp_path, clock):
    owners = frozenset({TEST_OWNER_EMAIL, "b@example.com"})
    with TestClient(create_app(make_settings(tmp_path, clock, bootstrap_owners=owners))) as c:
        login(c, "s1", TEST_OWNER_EMAIL)
        assert login(c, "s2", "b@example.com")["orgs"] == []


def test_a_person_who_is_not_a_bootstrap_owner_gets_nothing(client):
    assert login(client, "s1", "ana@example.com")["orgs"] == []


def test_login_accepts_pending_invitations_to_an_org(client):
    owner_id = owner(client)
    with db.connect(client.app.state.settings.db_path) as conn:
        repository.create_invitation(conn, actor_id=owner_id, org_id=DEMO_ORG_ID, email="Ana@Example.com",
                                     role="viewer", now=NOW)
    body = login(client, "s-ana", "ana@example.com")
    assert [(o["id"], o["role"]) for o in body["orgs"]] == [(DEMO_ORG_ID, "viewer")]
    assert login(client, "s-ana", "ana@example.com")["orgs"] == body["orgs"]


# --- GET /v1/me/orgs ---


def test_me_orgs_lists_my_orgs_with_my_role_and_their_projects(client):
    admin = member_as(client, "admin")
    response = client.get("/v1/me/orgs", headers=acting(admin))
    assert response.status_code == 200
    assert response.json() == {"orgs": [{"id": DEMO_ORG_ID, "name": "Demo", "role": "admin",
                                         "projects": [{"id": "client_a", "name": "client_a"},
                                                      {"id": "client_b", "name": "client_b"}]}]}


def test_me_orgs_needs_a_known_actor(client):
    assert client.get("/v1/me/orgs", headers=PANEL_AUTH).status_code == 403
    assert client.get("/v1/me/orgs", headers=AUTH_A).status_code == 403
    assert client.get("/v1/me/orgs").status_code == 401


# --- POST /v1/orgs (allowlist) ---


def test_nobody_creates_orgs_unless_allowlisted(client, tmp_path, clock):
    person = login(client, "s1", "ana@example.com")["person"]["id"]
    response = client.post("/v1/orgs", json={"name": "Mine"}, headers=acting(person))
    assert response.status_code == 403 and response.json() == {"error": "forbidden"}
    with TestClient(create_app(make_settings(tmp_path / "empty", clock, db_path=str(tmp_path / "e.db")))) as empty:
        creator = login(empty, "s-c", CREATOR)["person"]["id"]
        assert empty.post("/v1/orgs", json={"name": "Mine"}, headers=acting(creator)).status_code == 403


def test_an_allowlisted_person_creates_an_org_and_owns_it(client):
    creator = login(client, "s-c", CREATOR)["person"]["id"]
    response = client.post("/v1/orgs", json={"name": "Acme"}, headers=acting(creator))
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme" and body["role"] == "owner" and body["id"].startswith("org_")
    assert body["projects"] == []
    assert {"id": body["id"], "name": "Acme", "role": "owner", "projects": []} in client.get(
        "/v1/me/orgs", headers=acting(creator)).json()["orgs"]
    assert audit(client, "org.create")[-1]["org_id"] == body["id"]


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "x" * 81}])
def test_org_creation_validates_the_name(client, body):
    creator = login(client, "s-c", CREATOR)["person"]["id"]
    assert client.post("/v1/orgs", json=body, headers=acting(creator)).status_code == 422


# --- GET / PATCH /v1/orgs/{id} ---


def test_a_member_reads_the_org_with_their_role(client):
    viewer = member_as(client, "viewer")
    response = client.get(f"/v1/orgs/{DEMO_ORG_ID}", headers=acting(viewer))
    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["name"], body["role"]) == (DEMO_ORG_ID, "Demo", "viewer")
    assert set(body) == {"id", "name", "role", "projects", "created_at"}


def test_a_non_member_cannot_read_or_rename_an_org(client):
    owner(client)
    stranger = login(client, "s-x", "x@example.com")["person"]["id"]
    assert client.get(f"/v1/orgs/{DEMO_ORG_ID}", headers=acting(stranger)).status_code == 403
    assert client.patch(f"/v1/orgs/{DEMO_ORG_ID}", json={"name": "Hax"}, headers=acting(stranger)).status_code == 403
    assert client.get("/v1/orgs/org_nope", headers=acting(stranger)).status_code == 403


def test_only_an_admin_renames_the_org(client):
    viewer = member_as(client, "viewer", sub="s-v", email="v@example.com")
    admin = member_as(client, "admin", sub="s-a", email="a@example.com")
    assert client.patch(f"/v1/orgs/{DEMO_ORG_ID}", json={"name": "Hax"}, headers=acting(viewer)).status_code == 403
    response = client.patch(f"/v1/orgs/{DEMO_ORG_ID}", json={"name": " Renamed "}, headers=acting(admin))
    assert response.status_code == 200 and response.json()["name"] == "Renamed" and response.json()["role"] == "admin"
    assert client.patch(f"/v1/orgs/{DEMO_ORG_ID}", json={"name": ""}, headers=acting(admin)).status_code == 422
    assert audit(client, "org.update")[-1]["actor_person_id"] == admin


# --- GET / POST /v1/orgs/{id}/projects ---


def test_a_member_lists_the_projects_of_the_org(client):
    viewer = member_as(client, "viewer")
    response = client.get(f"/v1/orgs/{DEMO_ORG_ID}/projects", headers=acting(viewer))
    assert response.status_code == 200
    projects = response.json()["projects"]
    assert [p["id"] for p in projects] == ["client_a", "client_b"]
    assert projects[0] == {"id": "client_a", "org_id": DEMO_ORG_ID, "name": "client_a", "retention_days": 90,
                           "enforcement": "enforce", "ai_enabled": True, "created_at": "2026-10-26T12:00:00Z",
                           "role": "viewer"}


def test_a_viewer_cannot_create_a_project_but_an_admin_can(client):
    viewer = member_as(client, "viewer", sub="s-v", email="v@example.com")
    admin = member_as(client, "admin", sub="s-a", email="a@example.com")
    path = f"/v1/orgs/{DEMO_ORG_ID}/projects"
    assert client.post(path, json={"name": "Shop"}, headers=acting(viewer)).status_code == 403
    response = client.post(path, json={"name": "Shop"}, headers=acting(admin))
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Shop" and body["org_id"] == DEMO_ORG_ID and body["role"] == "admin"
    assert body["enforcement"] == "observe" and body["ai_enabled"] is True and body["retention_days"] == 90
    # The new project is its own empty tenant, readable by every member of the org.
    response = client.get("/v1/activity", headers=acting(viewer, body["id"]))
    assert response.status_code == 200 and response.json() == {"activity": []}


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "x" * 81}])
def test_project_creation_validates_the_name(client, body):
    admin = member_as(client, "admin")
    assert client.post(f"/v1/orgs/{DEMO_ORG_ID}/projects", json=body, headers=acting(admin)).status_code == 422


def test_a_non_member_cannot_list_or_create_projects_in_an_org(client):
    owner(client)
    stranger = login(client, "s-x", "x@example.com")["person"]["id"]
    assert client.get(f"/v1/orgs/{DEMO_ORG_ID}/projects", headers=acting(stranger)).status_code == 403
    assert client.post(f"/v1/orgs/{DEMO_ORG_ID}/projects", json={"name": "H"}, headers=acting(stranger)).status_code == 403


# --- GET / PATCH /v1/projects/{id} ---


def test_a_member_reads_the_project(client):
    viewer = member_as(client, "viewer")
    response = client.get("/v1/projects/client_a", headers=acting(viewer))
    assert response.status_code == 200
    assert response.json()["org_id"] == DEMO_ORG_ID and response.json()["role"] == "viewer"


def test_a_member_of_another_org_cannot_read_or_patch_a_project(client):
    creator, _ = other_org_project(client)
    assert client.get("/v1/projects/client_a", headers=acting(creator)).status_code == 403
    assert client.patch("/v1/projects/client_a", json={"name": "Hax"}, headers=acting(creator)).status_code == 403
    assert client.get("/v1/projects/p_nope", headers=acting(creator)).status_code == 403


def test_a_viewer_cannot_patch_but_an_admin_can(client):
    viewer = member_as(client, "viewer", sub="s-v", email="v@example.com")
    admin = member_as(client, "admin", sub="s-a", email="a@example.com")
    assert client.patch("/v1/projects/client_a", json={"name": "Hax"}, headers=acting(viewer)).status_code == 403
    response = client.patch("/v1/projects/client_a", headers=acting(admin),
                            json={"name": "Renamed", "retention_days": 30, "enforcement": "observe", "ai_enabled": False})
    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["retention_days"], body["enforcement"], body["ai_enabled"]) == ("Renamed", 30, "observe", False)
    assert audit(client, "project.update")[-1]["actor_person_id"] == admin


def test_patching_enforcement_changes_what_the_sdk_receives(client):
    block_decision(client)
    before = client.get("/v1/blocklist", headers=AUTH_A).json()
    client.patch("/v1/projects/client_a", json={"enforcement": "observe"}, headers=as_panel(client))
    after = client.get(f"/v1/blocklist?since={before['version']}", headers=AUTH_A)
    assert after.status_code == 200 and after.json()["sessions"] == []


def test_patch_changes_only_the_given_fields(client):
    admin = member_as(client, "admin")
    before = client.get("/v1/projects/client_a", headers=acting(admin)).json()
    after = client.patch("/v1/projects/client_a", json={"name": "Only name"}, headers=acting(admin)).json()
    assert after == {**before, "name": "Only name"}


@pytest.mark.parametrize("body", [{"name": ""}, {"retention_days": 0}, {"enforcement": "maybe"}, {"ai_enabled": "x"}])
def test_patch_validates_fields(client, body):
    admin = member_as(client, "admin")
    assert client.patch("/v1/projects/client_a", json=body, headers=acting(admin)).status_code == 422


# --- contract 12: API keys ---


def test_an_admin_creates_a_key_shown_once_that_authenticates_the_sdk(client):
    admin = member_as(client, "admin")
    response = client.post("/v1/projects/client_a/api-keys", json={"name": "prod"}, headers=acting(admin))
    assert response.status_code == 201
    created = response.json()
    assert set(created) == {"id", "name", "prefix", "key"}
    assert re.fullmatch(r"vrd_[0-9a-f]{8}_[A-Za-z0-9_-]+", created["key"])
    assert created["key"].startswith(f"vrd_{created['prefix']}_")
    assert post_events(client, [make_event("k1", NOW)], headers=bearer(created["key"])).status_code == 202
    assert client.get("/v1/activity", headers=as_panel(client)).json()["activity"][0]["event_id"] == "k1"

    listed = client.get("/v1/projects/client_a/api-keys", headers=acting(admin)).json()["api_keys"]
    mine = next(k for k in listed if k["id"] == created["id"])
    assert mine["prefix"] == created["prefix"] and mine["name"] == "prod" and mine["revoked_at"] is None
    assert mine["last_used_at"] is not None
    assert created["key"] not in json.dumps(listed)
    assert all(set(k) == {"id", "name", "prefix", "created_by", "created_at", "last_used_at", "revoked_at"} for k in listed)
    assert audit(client, "api_key.create")[-1]["actor_person_id"] == admin


def test_revoking_a_key_stops_it_at_once(client):
    admin = member_as(client, "admin")
    created = client.post("/v1/projects/client_a/api-keys", json={"name": "prod"}, headers=acting(admin)).json()
    response = client.post(f"/v1/projects/client_a/api-keys/{created['id']}/revoke", headers=acting(admin))
    assert response.status_code == 200 and response.json()["revoked_at"] is not None
    assert post_events(client, [make_event("k1", NOW)], headers=bearer(created["key"])).status_code == 401
    again = client.post(f"/v1/projects/client_a/api-keys/{created['id']}/revoke", headers=acting(admin))
    assert again.status_code == 404 and again.json() == {"error": "not_found"}


def test_the_legacy_key_is_listed_and_can_be_revoked(client):
    admin = member_as(client, "admin")
    [legacy] = client.get("/v1/projects/client_a/api-keys", headers=acting(admin)).json()["api_keys"]
    assert legacy["prefix"] == "legacy"
    client.post(f"/v1/projects/client_a/api-keys/{legacy['id']}/revoke", headers=acting(admin))
    assert post_events(client, [make_event("e1", NOW)]).status_code == 401
    assert post_events(client, [make_event("e1", NOW)], headers=AUTH_B).status_code == 202


def test_a_viewer_cannot_manage_keys(client):
    viewer = member_as(client, "viewer")
    assert client.get("/v1/projects/client_a/api-keys", headers=acting(viewer)).status_code == 403
    assert client.post("/v1/projects/client_a/api-keys", json={"name": "k"}, headers=acting(viewer)).status_code == 403


def test_keys_are_scoped_to_the_project_in_the_path(client):
    admin = member_as(client, "admin")
    created = client.post("/v1/projects/client_a/api-keys", json={"name": "prod"}, headers=acting(admin)).json()
    assert created["id"] not in [k["id"] for k in client.get(
        "/v1/projects/client_b/api-keys", headers=acting(admin)).json()["api_keys"]]
    assert client.post(f"/v1/projects/client_b/api-keys/{created['id']}/revoke", headers=acting(admin)).status_code == 404
    _, foreign = other_org_project(client)
    assert client.get(f"/v1/projects/{foreign}/api-keys", headers=acting(admin)).status_code == 403


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "x" * 81}])
def test_key_creation_validates_the_name(client, body):
    admin = member_as(client, "admin")
    assert client.post("/v1/projects/client_a/api-keys", json=body, headers=acting(admin)).status_code == 422


def test_the_sdk_key_cannot_manage_keys_or_projects(client):
    assert client.get("/v1/projects/client_a/api-keys", headers=AUTH_A).status_code == 403
    assert client.get("/v1/projects/client_a", headers=AUTH_A).status_code == 403
    assert client.get("/v1/me/orgs", headers=AUTH_A).status_code == 403


def test_the_panel_credential_is_compared_and_rate_limited_like_any_credential(tmp_path, clock):
    settings = make_settings(tmp_path, clock, auth_max_failures_per_ip=2)
    with TestClient(create_app(settings)) as c:
        for _ in range(2):
            assert c.get("/v1/me/orgs", headers=bearer("panel-guess")).status_code == 401
        assert c.get("/v1/me/orgs", headers=PANEL_AUTH).status_code == 429


def test_sqlite_row_of_the_panel_credential_is_never_stored(client):
    owner(client)
    conn = sqlite3.connect(client.app.state.settings.db_path)
    try:
        dumped = "\n".join(conn.iterdump())
    finally:
        conn.close()
    assert PANEL_TOKEN not in dumped


# --- contract 10: DELETE /v1/projects/{id} ---


def test_only_an_owner_deletes_a_project(client):
    viewer = member_as(client, "viewer", sub="s-v", email="v@example.com")
    admin = member_as(client, "admin", sub="s-a", email="a@example.com")
    creator, _ = other_org_project(client)
    for actor in (viewer, admin, creator):
        assert client.delete("/v1/projects/client_a", headers=acting(actor)).status_code == 403
    assert audit(client, "project.delete") == []
    response = client.delete("/v1/projects/client_a", headers=acting(owner(client)))
    assert response.status_code == 204 and response.content == b""


def test_a_deleted_project_disappears_from_reads_and_is_audited(client):
    owner_id = owner(client)
    assert client.delete("/v1/projects/client_a", headers=acting(owner_id)).status_code == 204
    assert client.get("/v1/projects/client_a", headers=acting(owner_id)).status_code == 403
    assert client.delete("/v1/projects/client_a", headers=acting(owner_id)).status_code == 403
    listed = client.get(f"/v1/orgs/{DEMO_ORG_ID}/projects", headers=acting(owner_id)).json()["projects"]
    assert "client_a" not in [p["id"] for p in listed]
    [row] = audit(client, "project.delete")
    assert (row["actor_person_id"], row["target_id"], row["project_id"]) == (owner_id, "client_a", "client_a")


def test_a_deleted_project_rejects_its_sdk_keys(client):
    assert client.delete("/v1/projects/client_a", headers=acting(owner(client))).status_code == 204
    assert client.get("/v1/blocklist", headers=AUTH_A).status_code == 401
    assert client.get("/v1/blocklist", headers=AUTH_B).status_code == 200


def test_a_deleted_project_leaves_the_org_listings(client):
    creator, project_id = other_org_project(client)
    assert client.delete(f"/v1/projects/{project_id}", headers=acting(creator)).status_code == 204
    [org] = client.get("/v1/me/orgs", headers=acting(creator)).json()["orgs"]
    assert org["projects"] == []


# --- contract 11: members and invitations of the organization (shapes: docs/DISENO-PRODUCTO.md) ---


def members_url(org_id=DEMO_ORG_ID):
    return f"/v1/orgs/{org_id}/members"


def invitations_url(org_id=DEMO_ORG_ID):
    return f"/v1/orgs/{org_id}/invitations"


def invite(client, actor, email, role="viewer"):
    return client.post(invitations_url(), json={"email": email, "role": role}, headers=acting(actor))


def test_any_member_lists_the_members_owners_first(client):
    owner_id = owner(client)
    viewer = member_as(client, "viewer")
    response = client.get(members_url(), headers=acting(viewer))
    assert response.status_code == 200
    members = response.json()["members"]
    assert [m["person_id"] for m in members] == [owner_id, viewer]
    assert (members[0]["role"], members[0]["email"]) == ("owner", TEST_OWNER_EMAIL)
    assert set(members[1]) == {"person_id", "email", "name", "role", "joined_at"}


def test_a_non_member_cannot_see_or_manage_members_or_invitations(client):
    owner_id = owner(client)
    creator, _ = other_org_project(client)
    for method, url, body in (
        ("GET", members_url(), None),
        ("PATCH", f"{members_url()}/{owner_id}", {"role": "viewer"}),
        ("DELETE", f"{members_url()}/{owner_id}", None),
        ("GET", invitations_url(), None),
        ("POST", invitations_url(), {"email": "x@example.com", "role": "viewer"}),
        ("DELETE", f"{invitations_url()}/inv_nope", None),
    ):
        assert client.request(method, url, json=body, headers=acting(creator)).status_code == 403
    assert client.get(members_url("org_nope"), headers=acting(creator)).status_code == 403


def test_a_viewer_lists_invitations_but_cannot_invite_or_manage(client):
    owner_id = owner(client)
    viewer = member_as(client, "viewer")
    invitation = invite(client, owner_id, "x@example.com").json()
    assert [i["id"] for i in client.get(invitations_url(), headers=acting(viewer)).json()["invitations"]] == [invitation["id"]]
    assert invite(client, viewer, "y@example.com").status_code == 403
    assert client.delete(f"{invitations_url()}/{invitation['id']}", headers=acting(viewer)).status_code == 403
    assert client.patch(f"{members_url()}/{owner_id}", json={"role": "admin"}, headers=acting(viewer)).status_code == 403


def test_an_admin_invites_viewers_and_admins_but_not_owners(client):
    admin = member_as(client, "admin")
    response = invite(client, admin, " New@Example.com ", "admin")
    assert response.status_code == 201
    invitation = response.json()
    assert set(invitation) == {"id", "email", "role", "invited_by", "created_at", "expires_at", "expired"}
    assert (invitation["email"], invitation["role"], invitation["invited_by"], invitation["expired"]) == (
        "new@example.com", "admin", admin, False)
    assert invitation["id"].startswith("inv_")
    assert invite(client, admin, "o@example.com", "owner").status_code == 403
    assert audit(client, "invitation.create")[-1]["target_id"] == invitation["id"]


def test_an_invitation_lasts_fourteen_days_by_default(client):
    invitation = invite(client, owner(client), "x@example.com").json()
    assert invitation["created_at"] == repository.format_ts(NOW)
    assert invitation["expires_at"] == repository.format_ts(NOW + timedelta(days=14))


def test_the_invitation_lifetime_is_a_setting(tmp_path, clock):
    with TestClient(create_app(make_settings(tmp_path, clock, invitation_days=5))) as app_client:
        invitation = invite(app_client, owner(app_client), "x@example.com").json()
    assert invitation["expires_at"] == repository.format_ts(NOW + timedelta(days=5))


def test_inviting_the_same_email_again_replaces_the_pending_invitation(client):
    owner_id = owner(client)
    first = invite(client, owner_id, "x@example.com").json()
    second = invite(client, owner_id, "X@example.com", "admin").json()
    listed = client.get(invitations_url(), headers=acting(owner_id)).json()["invitations"]
    assert [(i["id"], i["role"]) for i in listed] == [(second["id"], "admin")]
    assert first["id"] != second["id"]


def test_an_admin_cannot_replace_an_owner_invitation(client):
    owner_id = owner(client)
    admin = member_as(client, "admin")
    invite(client, owner_id, "o@example.com", "owner")
    assert invite(client, admin, "o@example.com", "viewer").status_code == 403


def test_inviting_a_member_is_409(client):
    owner(client)
    admin = member_as(client, "admin", email="a@example.com")
    response = invite(client, admin, "A@example.com")
    assert response.status_code == 409 and response.json() == {"error": "already_member"}


def test_the_invitation_list_keeps_expired_ones_and_drops_used_ones(client, clock):
    owner_id = owner(client)
    pending = invite(client, owner_id, "p@example.com").json()
    revoked = invite(client, owner_id, "r@example.com").json()
    invite(client, owner_id, "a@example.com")
    assert client.delete(f"{invitations_url()}/{revoked['id']}", headers=acting(owner_id)).status_code == 204
    login(client, "sub-a", "a@example.com")
    listed = client.get(invitations_url(), headers=acting(owner_id)).json()["invitations"]
    assert [(i["id"], i["expired"]) for i in listed] == [(pending["id"], False)]
    assert audit(client, "invitation.revoke")[-1]["target_id"] == revoked["id"]
    clock.now = NOW + timedelta(days=15)
    listed = client.get(invitations_url(), headers=acting(owner_id)).json()["invitations"]
    assert [(i["id"], i["expired"]) for i in listed] == [(pending["id"], True)]


def test_revoking_an_unknown_or_already_revoked_invitation_is_404(client):
    owner_id = owner(client)
    assert client.delete(f"{invitations_url()}/inv_nope", headers=acting(owner_id)).status_code == 404
    invitation = invite(client, owner_id, "x@example.com").json()
    assert client.delete(f"{invitations_url()}/{invitation['id']}", headers=acting(owner_id)).status_code == 204
    assert client.delete(f"{invitations_url()}/{invitation['id']}", headers=acting(owner_id)).status_code == 404


def test_an_admin_cannot_revoke_an_owner_invitation(client):
    owner_id = owner(client)
    admin = member_as(client, "admin")
    invitation = invite(client, owner_id, "o@example.com", "owner").json()
    assert client.delete(f"{invitations_url()}/{invitation['id']}", headers=acting(admin)).status_code == 403


@pytest.mark.parametrize("body", [
    {"email": "x@example.com", "role": "god"}, {"email": "", "role": "viewer"},
    {"email": "not-an-email", "role": "viewer"}, {},
])
def test_invitation_creation_validates_the_body(client, body):
    assert client.post(invitations_url(), json=body, headers=acting(owner(client))).status_code == 422


def test_an_admin_changes_a_viewer_role_and_it_is_audited(client):
    owner(client)
    admin = member_as(client, "admin", sub="s-a", email="a@example.com")
    viewer = member_as(client, "viewer", sub="s-v", email="v@example.com")
    response = client.patch(f"{members_url()}/{viewer}", json={"role": "admin"}, headers=acting(admin))
    assert response.status_code == 200
    assert set(response.json()) == {"person_id", "email", "name", "role", "joined_at"}
    assert (response.json()["person_id"], response.json()["role"]) == (viewer, "admin")
    [row] = audit(client, "member.role")
    assert (row["actor_person_id"], row["target_id"]) == (admin, viewer)


def test_setting_the_same_role_writes_no_audit(client):
    owner_id = owner(client)
    viewer = member_as(client, "viewer")
    assert client.patch(f"{members_url()}/{viewer}", json={"role": "viewer"}, headers=acting(owner_id)).status_code == 200
    assert audit(client, "member.role") == []


def test_an_admin_does_not_manage_owners(client):
    owner_id = owner(client)
    admin = member_as(client, "admin", sub="s-a", email="a@example.com")
    viewer = member_as(client, "viewer", sub="s-v", email="v@example.com")
    assert client.patch(f"{members_url()}/{owner_id}", json={"role": "viewer"}, headers=acting(admin)).status_code == 403
    assert client.patch(f"{members_url()}/{viewer}", json={"role": "owner"}, headers=acting(admin)).status_code == 403
    assert client.delete(f"{members_url()}/{owner_id}", headers=acting(admin)).status_code == 403


def test_an_owner_promotes_and_removes_another_owner(client):
    owner_id = owner(client)
    admin = member_as(client, "admin")
    response = client.patch(f"{members_url()}/{admin}", json={"role": "owner"}, headers=acting(owner_id))
    assert response.status_code == 200 and response.json()["role"] == "owner"
    assert client.delete(f"{members_url()}/{owner_id}", headers=acting(admin)).status_code == 204


def test_nobody_changes_their_own_role_or_removes_themselves(client):
    owner_id = owner(client)
    member_as(client, "owner", sub="s-o", email="o2@example.com")
    admin = member_as(client, "admin")
    for actor, role in ((owner_id, "admin"), (admin, "viewer")):
        response = client.patch(f"{members_url()}/{actor}", json={"role": role}, headers=acting(actor))
        assert response.status_code == 409 and response.json() == {"error": "cannot_change_self"}
        response = client.delete(f"{members_url()}/{actor}", headers=acting(actor))
        assert response.status_code == 409 and response.json() == {"error": "cannot_change_self"}
    assert audit(client, "member.role") == [] and audit(client, "member.remove") == []


def test_removing_a_member_takes_away_their_access(client):
    owner_id = owner(client)
    viewer = member_as(client, "viewer")
    assert client.delete(f"{members_url()}/{viewer}", headers=acting(owner_id)).status_code == 204
    assert client.get("/v1/projects/client_a", headers=acting(viewer)).status_code == 403
    assert audit(client, "member.remove")[-1]["target_id"] == viewer
    assert client.delete(f"{members_url()}/{viewer}", headers=acting(owner_id)).status_code == 404
    assert client.patch(f"{members_url()}/{viewer}", json={"role": "admin"}, headers=acting(owner_id)).status_code == 404


def test_patching_a_member_validates_the_role(client):
    owner_id = owner(client)
    viewer = member_as(client, "viewer")
    assert client.patch(f"{members_url()}/{viewer}", json={"role": "god"}, headers=acting(owner_id)).status_code == 422
