"""Fake engine: panel principal, org-level roles and contract 10 (people, orgs, projects). See docs/DISENO-PRODUCTO.md."""

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
                      bootstrap_owners={OWNER})


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


def org_of(engine, token):
    return engine.state.org_id_for(token)


def member_as(client, engine, role, sub="sub-m", email="m@example.com", token=SDK):
    """A person who is a member, with `role`, of the organization that owns the project of `token`
    (through an invitation). Returns the person and that project's id."""
    engine.state.orgs[org_of(engine, token)].invite(email, role, FUTURE)
    return login(client, sub=sub, email=email), project_of(engine, token)


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
    event = {"id": "e_1", "ts": "2026-10-26T10:14:03Z", "user_id": "u_1", "session_id": "s_1", "ip": "1.1.1.1", "method": "GET",
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


def test_login_creates_a_person_with_no_orgs(client):
    body = login(client, sub="s1", email="Ana@Example.com", name="Ana")
    assert body["person"]["email"] == "ana@example.com" and body["person"]["name"] == "Ana"
    assert body["person"]["id"].startswith("per_") and body["orgs"] == []
    assert "google_sub" not in body["person"] and "projects" not in body


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


def test_bootstrap_owner_takes_the_orphan_orgs_on_login(client, engine):
    body = login(client, email=OWNER)
    assert {o["id"] for o in body["orgs"]} == {org_of(engine, SDK), org_of(engine, SDK_B)}
    assert {o["role"] for o in body["orgs"]} == {"owner"}


def test_login_lists_the_projects_of_each_org(client, engine):
    body = login(client, email=OWNER)
    by_org = {o["id"]: o["projects"] for o in body["orgs"]}
    for token in (SDK, SDK_B):
        pid = project_of(engine, token)
        assert by_org[org_of(engine, token)] == [{"id": pid, "name": engine.state.projects[pid].name}]


def test_the_seeded_demo_is_an_org_with_its_project(engine, client):
    body = login(client, email=OWNER)
    names = sorted((o["name"], p["name"]) for o in body["orgs"] for p in o["projects"])
    assert names == [("Demo 1", "Demo 1"), ("Demo 2", "Demo 2")]
    single = TestClient(create_app(tokens={SDK}, panel_tokens={PANEL}, bootstrap_owners={OWNER}))
    only = login(single, email=OWNER)["orgs"]
    assert [(o["name"], [p["name"] for p in o["projects"]]) for o in only] == [("Demo", ["Demo"])]


def test_bootstrap_only_acts_on_orgs_without_an_owner(client, engine):
    login(client, sub="s-first", email=OWNER)
    engine2 = create_app(tokens={SDK}, learn_delay=0, panel_tokens={PANEL}, bootstrap_owners={OWNER, "b@example.com"})
    c2 = TestClient(engine2)
    login(c2, sub="s1", email=OWNER)
    second = login(c2, sub="s2", email="b@example.com")
    assert second["orgs"] == []  # the org already has an owner


def test_a_person_who_is_not_a_bootstrap_owner_gets_nothing(client):
    assert login(client, email="ana@example.com")["orgs"] == []


def test_login_accepts_pending_invitations_to_an_org_by_email(client, engine):
    oid, pid = org_of(engine, SDK), project_of(engine, SDK)
    engine.state.orgs[oid].invite("Ana@Example.com", "viewer", FUTURE)
    body = login(client, email="ana@example.com")
    assert body["orgs"] == [{"id": oid, "name": engine.state.orgs[oid].name, "role": "viewer",
                             "projects": [{"id": pid, "name": engine.state.projects[pid].name}]}]
    assert engine.state.orgs[oid].invitations[0]["accepted_at"] is not None


def test_login_ignores_expired_and_already_accepted_invitations(client, engine):
    org = engine.state.orgs[org_of(engine, SDK)]
    org.invite("ana@example.com", "admin", PAST)
    assert login(client, email="ana@example.com")["orgs"] == []
    org.invite("bea@example.com", "admin", FUTURE)
    bea = login(client, sub="s-bea", email="bea@example.com")
    org.members.pop(bea["person"]["id"])
    assert login(client, sub="s-bea", email="bea@example.com")["orgs"] == []  # accepted once, not again


def test_an_invitation_never_downgrades_an_existing_role(client, engine):
    owner = login(client, email=OWNER)
    oid = org_of(engine, SDK)
    engine.state.orgs[oid].invite(OWNER, "viewer", FUTURE)
    again = login(client, email=OWNER)
    assert {o["id"]: o["role"] for o in again["orgs"]}[oid] == "owner"
    assert again["person"]["id"] == owner["person"]["id"]


def test_one_person_can_belong_to_several_orgs_with_a_role_in_each(client, engine):
    engine.state.orgs[org_of(engine, SDK)].invite("ana@example.com", "admin", FUTURE)
    engine.state.orgs[org_of(engine, SDK_B)].invite("ana@example.com", "viewer", FUTURE)
    body = login(client, email="ana@example.com")
    assert {o["id"]: o["role"] for o in body["orgs"]} == {org_of(engine, SDK): "admin", org_of(engine, SDK_B): "viewer"}


# --- GET /v1/me/orgs ------------------------------------------------------------------------------


def test_me_orgs_lists_my_orgs_with_my_role_and_their_projects(client, engine):
    person, pid = member_as(client, engine, "admin")
    oid = org_of(engine, SDK)
    r = client.get("/v1/me/orgs", headers=acting(person))
    assert r.status_code == 200
    assert r.json() == {"orgs": [{"id": oid, "name": engine.state.orgs[oid].name, "role": "admin",
                                  "projects": [{"id": pid, "name": engine.state.projects[pid].name}]}]}


def test_me_orgs_needs_a_known_actor(client):
    assert client.get("/v1/me/orgs", headers=bearer(PANEL)).status_code == 403
    assert client.get("/v1/me/orgs", headers=bearer(SDK)).status_code == 403
    assert client.get("/v1/me/orgs").status_code == 401


def test_the_project_level_endpoints_of_the_previous_design_are_gone(client):
    person = login(client, sub="s-c", email=CREATOR)
    assert client.get("/v1/me/projects", headers=acting(person)).status_code == 404
    assert client.post("/v1/projects", json={"name": "Shop"}, headers=acting(person)).status_code in (404, 405)


# --- POST /v1/orgs ----------------------------------------------------------------------------------


def test_any_signed_in_person_creates_an_org_without_an_invitation(client):
    person = login(client, email="ana@example.com")
    r = client.post("/v1/orgs", json={"name": "Mine"}, headers=acting(person))
    assert r.status_code == 201 and r.json()["role"] == "owner"
    empty = TestClient(create_app(tokens={SDK}, panel_tokens={PANEL}))
    p = login(empty, email="new@example.com")
    assert empty.post("/v1/orgs", json={"name": "Mine"}, headers=acting(p)).status_code == 201


def test_a_stranger_cannot_create_an_org_without_signing_in(client):
    r = client.post("/v1/orgs", json={"name": "Mine"}, headers=bearer(PANEL))
    assert r.status_code == 403


def test_the_same_verified_email_through_another_provider_is_the_same_person(client):
    google = login(client, sub="1234", email="Ana@Example.com")
    github = login(client, sub="github:99", email="ana@example.com")
    assert github["person"]["id"] == google["person"]["id"]
    oid = client.post("/v1/orgs", json={"name": "Acme"}, headers=acting(google)).json()["id"]
    assert [o["id"] for o in login(client, sub="github:99", email="ana@example.com")["orgs"]] == [oid]
    # each provider id keeps resolving to that person, whatever email it reports later
    assert login(client, sub="1234", email="ana@example.com")["person"]["id"] == google["person"]["id"]


def test_different_emails_are_different_people(client):
    a = login(client, sub="github:1", email="a@example.com")
    b = login(client, sub="github:2", email="b@example.com")
    assert a["person"]["id"] != b["person"]["id"]


def test_an_allowlisted_person_creates_an_org_and_owns_it(client):
    person = login(client, sub="s-c", email=CREATOR)
    r = client.post("/v1/orgs", json={"name": "Acme"}, headers=acting(person))
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Acme" and body["role"] == "owner" and body["id"].startswith("org_")
    assert body["projects"] == []
    mine = client.get("/v1/me/orgs", headers=acting(person)).json()["orgs"]
    assert {"id": body["id"], "name": "Acme", "role": "owner", "projects": []} in mine


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "x" * 81}])
def test_org_creation_validates_the_name(client, body):
    person = login(client, sub="s-c", email=CREATOR)
    assert client.post("/v1/orgs", json=body, headers=acting(person)).status_code == 422


# --- GET / PATCH /v1/orgs/{id} ----------------------------------------------------------------------


def test_a_member_reads_the_org_with_their_role(client, engine):
    person, pid = member_as(client, engine, "viewer")
    oid = org_of(engine, SDK)
    r = client.get(f"/v1/orgs/{oid}", headers=acting(person))
    assert r.status_code == 200
    assert r.json() == {"id": oid, "name": engine.state.orgs[oid].name, "role": "viewer",
                        "created_at": engine.state.orgs[oid].created_at,
                        "projects": [{"id": pid, "name": engine.state.projects[pid].name}]}


def test_a_non_member_cannot_read_or_rename_an_org(client, engine):
    login(client, email=OWNER)
    stranger = login(client, sub="s-x", email="x@example.com")
    oid = org_of(engine, SDK)
    assert client.get(f"/v1/orgs/{oid}", headers=acting(stranger)).status_code == 403
    assert client.patch(f"/v1/orgs/{oid}", json={"name": "Hax"}, headers=acting(stranger)).status_code == 403
    assert client.get("/v1/orgs/org_nope", headers=acting(stranger)).status_code == 403


def test_only_an_admin_renames_the_org(client, engine):
    viewer, _ = member_as(client, engine, "viewer", sub="s-v", email="v@example.com")
    admin, _ = member_as(client, engine, "admin", sub="s-a", email="a@example.com")
    oid = org_of(engine, SDK)
    assert client.patch(f"/v1/orgs/{oid}", json={"name": "Hax"}, headers=acting(viewer)).status_code == 403
    r = client.patch(f"/v1/orgs/{oid}", json={"name": " Renamed "}, headers=acting(admin))
    assert r.status_code == 200 and r.json()["name"] == "Renamed" and r.json()["role"] == "admin"
    assert client.patch(f"/v1/orgs/{oid}", json={"name": ""}, headers=acting(admin)).status_code == 422


# --- GET / POST /v1/orgs/{id}/projects --------------------------------------------------------------


def test_a_member_lists_the_projects_of_the_org(client, engine):
    person, pid = member_as(client, engine, "viewer")
    r = client.get(f"/v1/orgs/{org_of(engine, SDK)}/projects", headers=acting(person))
    assert r.status_code == 200
    assert [p["id"] for p in r.json()["projects"]] == [pid]
    assert r.json()["projects"][0]["org_id"] == org_of(engine, SDK)


def test_a_non_member_cannot_list_or_create_projects_in_an_org(client, engine):
    login(client, email=OWNER)
    stranger = login(client, sub="s-x", email="x@example.com")
    oid = org_of(engine, SDK)
    assert client.get(f"/v1/orgs/{oid}/projects", headers=acting(stranger)).status_code == 403
    assert client.post(f"/v1/orgs/{oid}/projects", json={"name": "Hax"}, headers=acting(stranger)).status_code == 403


def test_a_viewer_cannot_create_a_project_but_an_admin_can(client, engine):
    viewer, _ = member_as(client, engine, "viewer", sub="s-v", email="v@example.com")
    admin, _ = member_as(client, engine, "admin", sub="s-a", email="a@example.com")
    oid = org_of(engine, SDK)
    assert client.post(f"/v1/orgs/{oid}/projects", json={"name": "Shop"}, headers=acting(viewer)).status_code == 403
    r = client.post(f"/v1/orgs/{oid}/projects", json={"name": "Shop"}, headers=acting(admin))
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Shop" and body["org_id"] == oid and body["id"].startswith("p_")
    assert body["role"] == "admin"
    assert body["enforcement"] == "observe" and body["ai_enabled"] is True and body["retention_days"] == 90
    assert body["id"] in [p["id"] for p in client.get(f"/v1/orgs/{oid}/projects", headers=acting(viewer)).json()["projects"]]


def test_an_allowlisted_creator_adds_a_project_to_their_new_org(client):
    person = login(client, sub="s-c", email=CREATOR)
    org = client.post("/v1/orgs", json={"name": "Acme"}, headers=acting(person)).json()
    project = client.post(f"/v1/orgs/{org['id']}/projects", json={"name": "Shop"}, headers=acting(person)).json()
    assert project["role"] == "owner"
    # the new project is its own empty tenant
    r = client.get("/v1/activity", headers=acting(person, project["id"]))
    assert r.status_code == 200 and r.json() == {"activity": []}
    assert client.get("/v1/me/orgs", headers=acting(person)).json()["orgs"][-1]["projects"] == [
        {"id": project["id"], "name": "Shop"}]


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "x" * 81}])
def test_project_creation_validates_the_name(client, engine, body):
    admin, _ = member_as(client, engine, "admin")
    assert client.post(f"/v1/orgs/{org_of(engine, SDK)}/projects", json=body, headers=acting(admin)).status_code == 422


def test_an_org_role_applies_to_every_project_of_that_org_and_to_no_other(client, engine):
    viewer, first = member_as(client, engine, "viewer", sub="s-v", email="v@example.com")
    admin, _ = member_as(client, engine, "admin", sub="s-a", email="a@example.com")
    oid = org_of(engine, SDK)
    second = client.post(f"/v1/orgs/{oid}/projects", json={"name": "Second"}, headers=acting(admin)).json()["id"]
    for pid in (first, second):
        assert client.get("/v1/decisions", headers=acting(viewer, pid)).status_code == 200
        assert client.get("/v1/blocklist", headers=acting(viewer, pid)).status_code == 200
        assert client.post("/v1/decisions/d_nope/normal", headers=acting(viewer, pid)).status_code == 403
        assert client.post("/v1/decisions/d_nope/normal", headers=acting(admin, pid)).status_code == 404
    assert client.get("/v1/decisions", headers=acting(admin, project_of(engine, SDK_B))).status_code == 403


# --- GET / PATCH /v1/projects/{id} -------------------------------------------------------------


def test_a_member_reads_the_project(client, engine):
    person, pid = member_as(client, engine, "viewer")
    r = client.get(f"/v1/projects/{pid}", headers=acting(person))
    assert r.status_code == 200
    assert r.json() == {"id": pid, "org_id": org_of(engine, SDK), "name": engine.state.projects[pid].name,
                        "retention_days": 90, "enforcement": "enforce", "ai_enabled": True,
                        "created_at": engine.state.projects[pid].created_at, "role": "viewer",
                        "brand_name": None, "brand_logo_url": None, "brand_color": None,
                        "brand_support_url": None, "brand_support_email": None, "self_unlock_enabled": False}


def test_a_non_member_cannot_read_or_patch_a_project(client, engine):
    login(client, email=OWNER)
    stranger = login(client, sub="s-x", email="x@example.com")
    pid = project_of(engine, SDK)
    assert client.get(f"/v1/projects/{pid}", headers=acting(stranger)).status_code == 403
    assert client.patch(f"/v1/projects/{pid}", json={"name": "Hax"}, headers=acting(stranger)).status_code == 403
    assert client.get("/v1/projects/p_nope", headers=acting(stranger)).status_code == 403


def test_a_member_of_another_org_cannot_read_or_patch_a_project(client, engine):
    admin, _ = member_as(client, engine, "admin", token=SDK)
    other = project_of(engine, SDK_B)
    assert client.get(f"/v1/projects/{other}", headers=acting(admin)).status_code == 403
    assert client.patch(f"/v1/projects/{other}", json={"name": "Hax"}, headers=acting(admin)).status_code == 403


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


# --- deleting an organization: owner only, and only when it has no projects left ---------------


def _new_org(client, sub="s-c", email=CREATOR, name="Acme"):
    person = login(client, sub=sub, email=email)
    org = client.post("/v1/orgs", json={"name": name}, headers=acting(person)).json()
    return person, org["id"]


def test_an_owner_deletes_an_empty_org(client):
    person, oid = _new_org(client)
    r = client.delete(f"/v1/orgs/{oid}", headers=acting(person))
    assert r.status_code == 204 and r.content == b""
    assert client.get(f"/v1/orgs/{oid}", headers=acting(person)).status_code == 403


def test_an_owner_cannot_delete_an_org_that_still_has_projects(client, engine):
    person, oid = _new_org(client)
    client.post(f"/v1/orgs/{oid}/projects", json={"name": "Web"}, headers=acting(person))
    r = client.delete(f"/v1/orgs/{oid}", headers=acting(person))
    assert r.status_code == 409 and r.json() == {"error": "org_not_empty"}
    assert client.get(f"/v1/orgs/{oid}", headers=acting(person)).status_code == 200


def test_an_org_whose_projects_are_all_deleted_can_be_deleted(client, engine):
    person, oid = _new_org(client)
    pid = client.post(f"/v1/orgs/{oid}/projects", json={"name": "Web"}, headers=acting(person)).json()["id"]
    engine.state.projects[pid].deleted_at = FUTURE  # stand-in for DELETE /v1/projects/{id}, not built yet
    assert client.delete(f"/v1/orgs/{oid}", headers=acting(person)).status_code == 204


@pytest.mark.parametrize("role", ["admin", "viewer"])
def test_an_admin_or_viewer_cannot_delete_the_org(client, engine, role):
    person, oid = _new_org(client)
    engine.state.orgs[oid].invite("m@example.com", role, FUTURE)
    member = login(client, sub="sub-m", email="m@example.com")
    r = client.delete(f"/v1/orgs/{oid}", headers=acting(member))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    assert client.get(f"/v1/orgs/{oid}", headers=acting(person)).status_code == 200


def test_a_non_member_cannot_delete_an_org(client, engine):
    _, oid = _new_org(client)
    stranger = login(client, sub="s-x", email="x@example.com")
    assert client.delete(f"/v1/orgs/{oid}", headers=acting(stranger)).status_code == 403
    assert client.delete("/v1/orgs/org_nope", headers=acting(stranger)).status_code == 403


def test_a_deleted_org_disappears_from_me_orgs_and_login(client):
    person, oid = _new_org(client)
    assert client.delete(f"/v1/orgs/{oid}", headers=acting(person)).status_code == 204
    assert oid not in [o["id"] for o in client.get("/v1/me/orgs", headers=acting(person)).json()["orgs"]]
    assert oid not in [o["id"] for o in login(client, sub="s-c", email=CREATOR)["orgs"]]
    assert client.patch(f"/v1/orgs/{oid}", json={"name": "x"}, headers=acting(person)).status_code == 403
    assert client.post(f"/v1/orgs/{oid}/projects", json={"name": "x"}, headers=acting(person)).status_code == 403
