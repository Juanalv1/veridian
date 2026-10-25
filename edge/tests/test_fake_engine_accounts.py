"""Fake engine: panel principal, roles and contract 10 (people, projects). See docs/DISENO-PRODUCTO.md."""

import pytest
from fastapi.testclient import TestClient

from fake_engine import create_app

SDK = "tok-a"
SDK_B = "tok-b"
PANEL = "panel-secret"
OWNER = "owner@example.com"
CREATOR = "creator@example.com"
FUTURE = "2999-01-01T00:00:00Z"
PAST = "2000-01-01T00:00:00Z"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def engine():
    return create_app(tokens={SDK, SDK_B}, seed=[], learn_delay=0, panel_tokens={PANEL},
                      bootstrap_owners={OWNER}, project_creators={CREATOR})


@pytest.fixture
def client(engine):
    return TestClient(engine)


def login(client, sub="sub-1", email="ana@example.com", name="Ana"):
    r = client.post("/v1/people/login", json={"google_sub": sub, "email": email, "name": name}, headers=bearer(PANEL))
    assert r.status_code == 200, r.text
    return r.json()


def acting(person, project=None, token=PANEL):
    headers = {**bearer(token), "X-Veridian-Actor": person["person"]["id"]}
    if project:
        headers["X-Veridian-Project"] = project
    return headers


def project_of(engine, token):
    return engine.state.project_id_for(token)


def member_as(client, engine, role, sub="sub-m", email="m@example.com", token=SDK):
    """A person who is a member of the project of `token` with `role` (through an invitation)."""
    pid = project_of(engine, token)
    engine.state.projects[pid].invite(email, role, FUTURE)
    return login(client, sub=sub, email=email), pid


# --- who may call what ------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/v1/decisions", "/v1/activity", "/v1/summary", "/v1/users", "/v1/users/u_1/profile"])
def test_an_sdk_key_cannot_read_panel_data(client, path):
    r = client.get(path, headers=bearer(SDK))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}


def test_an_sdk_key_cannot_mark_a_decision_normal(client):
    r = client.post("/v1/decisions/d_1/normal", headers=bearer(SDK))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}


@pytest.mark.parametrize("method,path", [("post", "/v1/events"), ("post", "/v1/check")])
def test_the_panel_credential_does_not_work_on_sdk_only_endpoints(client, method, path):
    person = login(client, email=OWNER)
    r = getattr(client, method)(path, json={}, headers=acting(person, project_of(client.app, SDK)))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}


def test_unknown_or_missing_credentials_are_401_on_panel_endpoints(client):
    for headers in ({}, bearer("nope")):
        r = client.get("/v1/decisions", headers=headers)
        assert r.status_code == 401 and r.json() == {"error": "unauthorized"}


def test_the_panel_credential_needs_a_known_actor(client, engine):
    pid = project_of(engine, SDK)
    for extra in ({}, {"X-Veridian-Actor": "per_nope"}):
        r = client.get("/v1/decisions", headers={**bearer(PANEL), "X-Veridian-Project": pid, **extra})
        assert r.status_code == 403 and r.json() == {"error": "forbidden"}


def test_data_reads_need_a_project_header(client):
    person = login(client, email=OWNER)
    r = client.get("/v1/decisions", headers=acting(person))
    assert r.status_code == 400 and r.json() == {"error": "project_required"}


def test_a_non_member_gets_403_and_no_data(client, engine):
    login(client, email=OWNER)  # takes the orphan projects
    stranger = login(client, sub="sub-x", email="x@example.com")
    pid = project_of(engine, SDK)
    for path in ("/v1/decisions", "/v1/activity", "/v1/summary", "/v1/users", "/v1/blocklist", "/v1/routes"):
        r = client.get(path, headers=acting(stranger, pid))
        assert r.status_code == 403 and r.json() == {"error": "forbidden"}, path
    assert client.post("/v1/decisions/d_1/normal", headers=acting(stranger, pid)).status_code == 403


def test_a_member_of_one_project_cannot_use_another_projects_header(client, engine):
    person, _ = member_as(client, engine, "admin", token=SDK)
    other = project_of(engine, SDK_B)
    assert client.get("/v1/decisions", headers=acting(person, other)).status_code == 403


def test_an_unknown_project_is_403_like_a_foreign_one(client):
    person = login(client, email=OWNER)
    assert client.get("/v1/decisions", headers=acting(person, "p_nope")).status_code == 403


def test_a_viewer_reads_but_cannot_mark_normal(client, engine):
    person, pid = member_as(client, engine, "viewer")
    assert client.get("/v1/decisions", headers=acting(person, pid)).status_code == 200
    assert client.get("/v1/blocklist", headers=acting(person, pid)).status_code == 200
    assert client.get("/v1/routes", headers=acting(person, pid)).status_code == 200
    r = client.post("/v1/decisions/d_1/normal", headers=acting(person, pid))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}


def test_an_admin_can_mark_normal(client, engine):
    person, pid = member_as(client, engine, "admin")
    # role is enough: a missing decision is a 404, not a 403
    assert client.post("/v1/decisions/d_nope/normal", headers=acting(person, pid)).status_code == 404


def test_panel_reads_are_scoped_to_the_project_header(client, engine):
    owner = login(client, email=OWNER)
    event = {"id": "e_1", "ts": "2026-10-26T10:14:03Z", "user_id": "u_1", "ip": "1.1.1.1", "method": "GET",
             "route": "/invoices", "status": 200, "bytes": 1}
    client.post("/v1/events", json={"events": [event]}, headers=bearer(SDK))
    a = client.get("/v1/activity", headers=acting(owner, project_of(engine, SDK))).json()["activity"]
    b = client.get("/v1/activity", headers=acting(owner, project_of(engine, SDK_B))).json()["activity"]
    assert [x["event_id"] for x in a] == ["e_1"] and b == []


def test_two_panel_credentials_are_accepted_to_rotate_without_a_cut(monkeypatch):
    monkeypatch.setenv("VERIDIAN_PANEL_TOKEN", "new-one")
    monkeypatch.setenv("VERIDIAN_PANEL_TOKENS", "old-one, new-one")
    monkeypatch.setenv("VERIDIAN_TOKENS", SDK)
    c = TestClient(create_app())
    for token in ("new-one", "old-one"):
        body = {"google_sub": "s", "email": "a@example.com"}
        assert c.post("/v1/people/login", json=body, headers=bearer(token)).status_code == 200
    assert c.post("/v1/people/login", json=body, headers=bearer(SDK)).status_code == 403


# --- POST /v1/people/login ------------------------------------------------------------------


def test_login_creates_a_person_with_no_projects(client):
    body = login(client, sub="s1", email="Ana@Example.com", name="Ana")
    assert body["person"]["email"] == "ana@example.com" and body["person"]["name"] == "Ana"
    assert body["person"]["id"].startswith("per_") and body["projects"] == []
    assert "google_sub" not in body["person"]


def test_login_is_an_upsert_by_google_sub_and_updates_the_email(client):
    first = login(client, sub="s1", email="old@example.com")
    again = login(client, sub="s1", email="new@example.com", name="Renamed")
    assert again["person"]["id"] == first["person"]["id"]
    assert again["person"]["email"] == "new@example.com" and again["person"]["name"] == "Renamed"
    assert login(client, sub="s2", email="old@example.com")["person"]["id"] != first["person"]["id"]


def test_login_requires_the_panel_credential(client):
    body = {"google_sub": "s", "email": "a@example.com"}
    assert client.post("/v1/people/login", json=body).status_code == 401
    assert client.post("/v1/people/login", json=body, headers=bearer("nope")).status_code == 401
    assert client.post("/v1/people/login", json=body, headers=bearer(SDK)).status_code == 403


def test_login_validates_the_body(client):
    assert client.post("/v1/people/login", json={"email": "a@example.com"}, headers=bearer(PANEL)).status_code == 422
    assert client.post("/v1/people/login", json={"google_sub": "", "email": "a@example.com"},
                       headers=bearer(PANEL)).status_code == 422


def test_bootstrap_owner_takes_the_orphan_projects_on_login(client, engine):
    body = login(client, email=OWNER)
    assert {p["id"] for p in body["projects"]} == {project_of(engine, SDK), project_of(engine, SDK_B)}
    assert {p["role"] for p in body["projects"]} == {"owner"}


def test_bootstrap_only_acts_on_projects_without_an_owner(client, engine):
    login(client, sub="s-first", email=OWNER)
    engine2 = create_app(tokens={SDK}, learn_delay=0, panel_tokens={PANEL}, bootstrap_owners={OWNER, "b@example.com"})
    c2 = TestClient(engine2)
    login(c2, sub="s1", email=OWNER)
    second = login(c2, sub="s2", email="b@example.com")
    assert second["projects"] == []  # the project already has an owner


def test_a_person_who_is_not_a_bootstrap_owner_gets_nothing(client):
    assert login(client, email="ana@example.com")["projects"] == []


def test_login_accepts_pending_invitations_by_email(client, engine):
    pid = project_of(engine, SDK)
    engine.state.projects[pid].invite("Ana@Example.com", "viewer", FUTURE)
    body = login(client, email="ana@example.com")
    assert body["projects"] == [{"id": pid, "name": engine.state.projects[pid].name, "role": "viewer"}]
    assert engine.state.projects[pid].invitations[0]["accepted_at"] is not None


def test_login_ignores_expired_and_already_accepted_invitations(client, engine):
    pid = project_of(engine, SDK)
    engine.state.projects[pid].invite("ana@example.com", "admin", PAST)
    assert login(client, email="ana@example.com")["projects"] == []
    engine.state.projects[pid].invite("bea@example.com", "admin", FUTURE)
    bea = login(client, sub="s-bea", email="bea@example.com")
    engine.state.projects[pid].members.pop(bea["person"]["id"])
    assert login(client, sub="s-bea", email="bea@example.com")["projects"] == []  # accepted once, not again


def test_an_invitation_never_downgrades_an_existing_role(client, engine):
    owner = login(client, email=OWNER)
    pid = project_of(engine, SDK)
    engine.state.projects[pid].invite(OWNER, "viewer", FUTURE)
    again = login(client, email=OWNER)
    assert {p["id"]: p["role"] for p in again["projects"]}[pid] == "owner"
    assert again["person"]["id"] == owner["person"]["id"]


# --- GET /v1/me/projects ----------------------------------------------------------------------


def test_me_projects_lists_my_projects_with_my_role(client, engine):
    person, pid = member_as(client, engine, "admin")
    r = client.get("/v1/me/projects", headers=acting(person))
    assert r.status_code == 200
    assert r.json() == {"projects": [{"id": pid, "name": engine.state.projects[pid].name, "role": "admin"}]}


def test_me_projects_needs_a_known_actor(client):
    assert client.get("/v1/me/projects", headers=bearer(PANEL)).status_code == 403
    assert client.get("/v1/me/projects", headers=bearer(SDK)).status_code == 403
    assert client.get("/v1/me/projects").status_code == 401


# --- POST /v1/projects ------------------------------------------------------------------------


def test_nobody_creates_projects_unless_allowlisted(engine, client):
    person = login(client, email="ana@example.com")
    r = client.post("/v1/projects", json={"name": "Mine"}, headers=acting(person))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    empty = TestClient(create_app(tokens={SDK}, panel_tokens={PANEL}))
    p = login(empty, email=CREATOR)
    assert empty.post("/v1/projects", json={"name": "Mine"}, headers=acting(p)).status_code == 403


def test_an_allowlisted_person_creates_a_project_and_owns_it(client):
    person = login(client, sub="s-c", email=CREATOR)
    r = client.post("/v1/projects", json={"name": "Shop"}, headers=acting(person))
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Shop" and body["role"] == "owner" and body["id"].startswith("p_")
    assert body["enforcement"] == "observe" and body["ai_enabled"] is True and body["retention_days"] == 90
    mine = client.get("/v1/me/projects", headers=acting(person)).json()["projects"]
    assert {"id": body["id"], "name": "Shop", "role": "owner"} in mine
    # the new project is its own empty tenant
    r = client.get("/v1/activity", headers=acting(person, body["id"]))
    assert r.status_code == 200 and r.json() == {"activity": []}


def test_project_creators_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("VERIDIAN_PROJECT_CREATORS", "Creator@Example.com, other@example.com")
    c = TestClient(create_app(tokens={SDK}, panel_tokens={PANEL}))
    p = login(c, email=CREATOR)
    assert c.post("/v1/projects", json={"name": "Shop"}, headers=acting(p)).status_code == 201


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "x" * 81}])
def test_project_creation_validates_the_name(client, body):
    person = login(client, sub="s-c", email=CREATOR)
    assert client.post("/v1/projects", json=body, headers=acting(person)).status_code == 422


# --- GET / PATCH /v1/projects/{id} -------------------------------------------------------------


def test_a_member_reads_the_project(client, engine):
    person, pid = member_as(client, engine, "viewer")
    r = client.get(f"/v1/projects/{pid}", headers=acting(person))
    assert r.status_code == 200
    assert r.json() == {"id": pid, "name": engine.state.projects[pid].name, "retention_days": 90,
                        "enforcement": "enforce", "ai_enabled": True,
                        "created_at": engine.state.projects[pid].created_at, "role": "viewer"}


def test_a_non_member_cannot_read_or_patch_a_project(client, engine):
    login(client, email=OWNER)
    stranger = login(client, sub="s-x", email="x@example.com")
    pid = project_of(engine, SDK)
    assert client.get(f"/v1/projects/{pid}", headers=acting(stranger)).status_code == 403
    assert client.patch(f"/v1/projects/{pid}", json={"name": "Hax"}, headers=acting(stranger)).status_code == 403
    assert client.get("/v1/projects/p_nope", headers=acting(stranger)).status_code == 403


def test_a_viewer_cannot_patch_but_an_admin_can(client, engine):
    viewer, pid = member_as(client, engine, "viewer", sub="s-v", email="v@example.com")
    admin, _ = member_as(client, engine, "admin", sub="s-a", email="a@example.com")
    assert client.patch(f"/v1/projects/{pid}", json={"name": "Hax"}, headers=acting(viewer)).status_code == 403
    r = client.patch(f"/v1/projects/{pid}", headers=acting(admin),
                     json={"name": "Renamed", "retention_days": 30, "enforcement": "observe", "ai_enabled": False})
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["retention_days"], r.json()["enforcement"], r.json()["ai_enabled"]) == (
        "Renamed", 30, "observe", False)
    assert client.get(f"/v1/projects/{pid}", headers=acting(viewer)).json()["name"] == "Renamed"


def test_patch_changes_only_the_given_fields(client, engine):
    admin, pid = member_as(client, engine, "admin")
    before = client.get(f"/v1/projects/{pid}", headers=acting(admin)).json()
    after = client.patch(f"/v1/projects/{pid}", json={"name": "Only name"}, headers=acting(admin)).json()
    assert after == {**before, "name": "Only name"}


@pytest.mark.parametrize("body", [{"name": ""}, {"retention_days": 0}, {"enforcement": "maybe"}, {"ai_enabled": "x"}])
def test_patch_validates_fields(client, engine, body):
    admin, pid = member_as(client, engine, "admin")
    assert client.patch(f"/v1/projects/{pid}", json=body, headers=acting(admin)).status_code == 422
