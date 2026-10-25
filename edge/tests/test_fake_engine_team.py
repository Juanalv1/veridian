"""Fake engine: members and invitations (contract 11), audit (contract 15), and audit of mark-normal.

Roles live at the organization level. The three domain rules of docs/DISENO-PRODUCTO.md are tested here:
there is always an owner, nobody changes their own role or removes themselves, and an admin does not
manage owners.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from fake_engine import create_app
from tests.test_fake_engine_accounts import (FUTURE, OWNER, PANEL, PAST, SDK, SDK_B, acting, bearer, login, org_of,
                                             project_of)

SEED = [{"user_id": "u_42", "ips": ["190.202.14.7"], "routes": ["GET /invoices"]}]
ROUTES = {"POST /payouts/bank-account": "critical"}


@pytest.fixture
def engine():
    return create_app(tokens={SDK, SDK_B}, seed=SEED, routes=ROUTES, learn_delay=0, panel_tokens={PANEL},
                      bootstrap_owners={OWNER})


@pytest.fixture
def client(engine):
    return TestClient(engine)


@pytest.fixture
def oid(engine):
    return org_of(engine, SDK)


@pytest.fixture
def pid(engine):
    return project_of(engine, SDK)


def person_in(client, engine, role, sub, token=SDK):
    email = f"{sub}@example.com"
    engine.state.orgs[org_of(engine, token)].invite(email, role, FUTURE)
    return login(client, sub=sub, email=email, name=sub.capitalize())


@pytest.fixture
def owner(client):
    return login(client, sub="s-own", email=OWNER, name="Olga")


@pytest.fixture
def admin(client, engine, owner):
    return person_in(client, engine, "admin", "adm")


@pytest.fixture
def viewer(client, engine, owner):
    return person_in(client, engine, "viewer", "vwr")


def pid_of(person):
    return person["person"]["id"]


def members_of(client, person, oid):
    r = client.get(f"/v1/orgs/{oid}/members", headers=acting(person))
    assert r.status_code == 200, r.text
    return r.json()["members"]


def roles(client, person, oid):
    return {m["email"]: m["role"] for m in members_of(client, person, oid)}


def invite(client, person, oid, email="new@example.com", role="viewer"):
    return client.post(f"/v1/orgs/{oid}/invitations", json={"email": email, "role": role}, headers=acting(person))


def invitations_of(client, person, oid):
    r = client.get(f"/v1/orgs/{oid}/invitations", headers=acting(person))
    assert r.status_code == 200, r.text
    return r.json()["invitations"]


def audit_of(client, person, oid, **params):
    r = client.get(f"/v1/orgs/{oid}/audit", params=params, headers=acting(person))
    assert r.status_code == 200, r.text
    return r.json()["entries"]


# --- GET /v1/orgs/{id}/members -----------------------------------------------------------------


def test_any_member_lists_the_org_members_with_role_owners_first(client, oid, owner, admin, viewer):
    rows = members_of(client, viewer, oid)
    assert [(m["name"], m["role"]) for m in rows] == [("Olga", "owner"), ("Adm", "admin"), ("Vwr", "viewer")]
    first = rows[0]
    assert set(first) == {"person_id", "email", "name", "role", "joined_at"}
    assert first["person_id"] == pid_of(owner) and first["email"] == OWNER


def test_a_non_member_sees_neither_members_nor_invitations_nor_audit(client, oid, owner):
    stranger = login(client, sub="s-x", email="x@example.com")
    for path in ("members", "invitations", "audit"):
        r = client.get(f"/v1/orgs/{oid}/{path}", headers=acting(stranger))
        assert r.status_code == 403 and r.json() == {"error": "forbidden"}


def test_members_of_one_org_are_not_listed_in_another(client, engine, oid, owner, viewer):
    other = org_of(engine, SDK_B)
    assert [m["email"] for m in members_of(client, owner, other)] == [OWNER]
    assert client.get(f"/v1/orgs/{other}/members", headers=acting(viewer)).status_code == 403


def test_an_sdk_key_cannot_use_the_team_endpoints(client, oid):
    assert client.get(f"/v1/orgs/{oid}/members", headers=bearer(SDK)).status_code == 403


# --- PATCH /v1/orgs/{id}/members/{person_id} ----------------------------------------------------


def patch_role(client, actor, oid, target, role):
    return client.patch(f"/v1/orgs/{oid}/members/{pid_of(target)}", json={"role": role}, headers=acting(actor))


def test_an_owner_changes_a_role_and_the_member_sees_it(client, oid, owner, viewer):
    r = patch_role(client, owner, oid, viewer, "admin")
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["person_id"] == pid_of(viewer)
    assert roles(client, owner, oid)["vwr@example.com"] == "admin"
    assert {o["id"]: o["role"] for o in client.get("/v1/me/orgs", headers=acting(viewer)).json()["orgs"]}[oid] == "admin"


def test_an_admin_switches_roles_between_viewer_and_admin(client, oid, admin, viewer):
    assert patch_role(client, admin, oid, viewer, "admin").status_code == 200
    assert patch_role(client, admin, oid, viewer, "viewer").status_code == 200


def test_an_admin_cannot_make_anyone_an_owner(client, oid, admin, viewer):
    r = patch_role(client, admin, oid, viewer, "owner")
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    assert roles(client, admin, oid)["vwr@example.com"] == "viewer"


def test_an_admin_cannot_demote_an_owner(client, oid, owner, admin):
    r = patch_role(client, admin, oid, owner, "viewer")
    assert r.status_code == 403
    assert roles(client, admin, oid)[OWNER] == "owner"


def test_an_owner_promotes_to_owner_and_demotes_another_owner(client, oid, owner, admin):
    assert patch_role(client, owner, oid, admin, "owner").status_code == 200
    assert patch_role(client, owner, oid, admin, "viewer").status_code == 200  # the first owner remains


def test_nobody_changes_their_own_role(client, oid, owner, admin):
    for actor in (owner, admin):
        r = patch_role(client, actor, oid, actor, "viewer")
        assert r.status_code == 409 and r.json() == {"error": "cannot_change_self"}
    assert roles(client, owner, oid)[OWNER] == "owner"


def test_a_viewer_cannot_change_roles(client, oid, admin, viewer):
    assert patch_role(client, viewer, oid, admin, "viewer").status_code == 403


def test_an_unknown_member_is_404_and_a_bad_role_is_422(client, oid, owner, viewer):
    ghost = client.patch(f"/v1/orgs/{oid}/members/per_nobody", json={"role": "admin"}, headers=acting(owner))
    assert ghost.status_code == 404 and ghost.json() == {"error": "not_found"}
    assert patch_role(client, owner, oid, viewer, "emperor").status_code == 422


def test_a_member_of_another_org_cannot_be_changed_through_this_one(client, engine, oid, owner):
    elsewhere = person_in(client, engine, "viewer", "far", token=SDK_B)
    r = patch_role(client, owner, oid, elsewhere, "admin")
    assert r.status_code == 404


# --- DELETE /v1/orgs/{id}/members/{person_id} --------------------------------------------------------


def remove(client, actor, oid, target):
    return client.delete(f"/v1/orgs/{oid}/members/{pid_of(target)}", headers=acting(actor))


def test_removing_a_member_takes_the_org_away_from_them(client, oid, owner, viewer):
    assert remove(client, owner, oid, viewer).status_code == 204
    assert "vwr@example.com" not in roles(client, owner, oid)
    assert client.get("/v1/me/orgs", headers=acting(viewer)).json() == {"orgs": []}
    assert client.get(f"/v1/orgs/{oid}", headers=acting(viewer)).status_code == 403


def test_an_admin_removes_viewers_and_admins_but_not_owners(client, engine, oid, owner, admin, viewer):
    second = person_in(client, engine, "admin", "adm2")
    assert remove(client, admin, oid, viewer).status_code == 204
    assert remove(client, admin, oid, second).status_code == 204
    assert remove(client, admin, oid, owner).status_code == 403
    assert OWNER in roles(client, admin, oid)


def test_nobody_removes_themselves(client, oid, owner, admin):
    for actor in (owner, admin):
        r = remove(client, actor, oid, actor)
        assert r.status_code == 409 and r.json() == {"error": "cannot_change_self"}
    assert set(roles(client, owner, oid)) == {OWNER, "adm@example.com"}


def test_a_viewer_cannot_remove_anyone(client, oid, admin, viewer):
    assert remove(client, viewer, oid, admin).status_code == 403


def test_removing_an_unknown_member_is_404(client, oid, owner):
    r = client.delete(f"/v1/orgs/{oid}/members/per_nobody", headers=acting(owner))
    assert r.status_code == 404


def test_an_owner_removes_another_owner_while_one_remains(client, oid, owner, admin):
    patch_role(client, owner, oid, admin, "owner")
    assert remove(client, owner, oid, admin).status_code == 204
    assert roles(client, owner, oid) == {OWNER: "owner"}


# --- invitations ------------------------------------------------------------------------------------


def test_an_admin_invites_and_the_invitation_is_listed_as_pending(client, oid, owner, admin):
    r = invite(client, admin, oid, "New@Example.com", "viewer")
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@example.com" and body["role"] == "viewer" and body["id"].startswith("inv_")
    assert body["invited_by"] == pid_of(admin) and body["expired"] is False
    assert invitations_of(client, owner, oid) == [body]


def test_an_invitation_expires_after_14_days_by_default(client, oid, owner):
    body = invite(client, owner, oid).json()
    left = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")) - datetime.now(timezone.utc)
    assert timedelta(days=13, hours=23) < left <= timedelta(days=14)


def test_the_invitation_lifetime_is_a_setting(monkeypatch):
    engine = create_app(tokens={SDK}, panel_tokens={PANEL}, bootstrap_owners={OWNER}, invitation_days=3)
    c = TestClient(engine)
    me = login(c, email=OWNER)
    body = invite(c, me, org_of(engine, SDK)).json()
    left = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")) - datetime.now(timezone.utc)
    assert timedelta(days=2, hours=23) < left <= timedelta(days=3)
    monkeypatch.setenv("VERIDIAN_INVITATION_DAYS", "5")
    monkeypatch.setenv("VERIDIAN_TOKENS", SDK)
    env_engine = create_app(panel_tokens={PANEL}, bootstrap_owners={OWNER})
    ec = TestClient(env_engine)
    env_body = invite(ec, login(ec, email=OWNER), org_of(env_engine, SDK)).json()
    env_left = datetime.fromisoformat(env_body["expires_at"].replace("Z", "+00:00")) - datetime.now(timezone.utc)
    assert timedelta(days=4, hours=23) < env_left <= timedelta(days=5)


def test_a_viewer_reads_the_pending_invitations_but_cannot_create_or_cancel(client, oid, owner, viewer):
    inv = invite(client, owner, oid).json()
    assert [i["id"] for i in invitations_of(client, viewer, oid)] == [inv["id"]]
    assert invite(client, viewer, oid, "x@example.com").status_code == 403
    assert client.delete(f"/v1/orgs/{oid}/invitations/{inv['id']}", headers=acting(viewer)).status_code == 403


def test_only_an_owner_invites_an_owner(client, oid, owner, admin):
    r = invite(client, admin, oid, "boss@example.com", "owner")
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    assert invitations_of(client, owner, oid) == []
    assert invite(client, owner, oid, "boss@example.com", "owner").status_code == 201


@pytest.mark.parametrize("body", [{"email": "nope", "role": "viewer"}, {"email": "a@b.c", "role": "king"},
                                  {"role": "viewer"}, {"email": "a@b.c"}])
def test_invitation_validation(client, oid, owner, body):
    r = client.post(f"/v1/orgs/{oid}/invitations", json=body, headers=acting(owner))
    assert r.status_code == 422


def test_inviting_someone_who_is_already_a_member_is_a_409(client, oid, owner, viewer):
    r = invite(client, owner, oid, "VWR@example.com", "admin")
    assert r.status_code == 409 and r.json() == {"error": "already_member"}
    assert invitations_of(client, owner, oid) == []


def test_the_invited_person_joins_on_login_and_the_invitation_stops_being_pending(client, oid, owner):
    invite(client, owner, oid, "marta@example.com", "admin")
    marta = login(client, sub="s-marta", email="marta@example.com")
    assert [o["role"] for o in marta["orgs"]] == ["admin"]
    assert invitations_of(client, owner, oid) == []
    assert roles(client, owner, oid)["marta@example.com"] == "admin"


def test_cancelling_an_invitation_means_login_no_longer_grants_it(client, oid, owner):
    inv = invite(client, owner, oid, "marta@example.com").json()
    assert client.delete(f"/v1/orgs/{oid}/invitations/{inv['id']}", headers=acting(owner)).status_code == 204
    assert invitations_of(client, owner, oid) == []
    assert login(client, sub="s-marta", email="marta@example.com")["orgs"] == []


def test_an_admin_cannot_cancel_an_invitation_to_owner(client, oid, owner, admin):
    inv = invite(client, owner, oid, "boss@example.com", "owner").json()
    r = client.delete(f"/v1/orgs/{oid}/invitations/{inv['id']}", headers=acting(admin))
    assert r.status_code == 403
    assert len(invitations_of(client, owner, oid)) == 1


def test_cancelling_an_unknown_or_foreign_invitation_is_404(client, engine, oid, owner):
    other = org_of(engine, SDK_B)
    foreign = invite(client, owner, other).json()
    for inv_id in ("inv_nope", foreign["id"]):
        r = client.delete(f"/v1/orgs/{oid}/invitations/{inv_id}", headers=acting(owner))
        assert r.status_code == 404
    assert len(invitations_of(client, owner, other)) == 1


def test_expired_invitations_are_listed_as_expired_and_inviting_again_renews_them(client, engine, oid, owner):
    engine.state.orgs[oid].invite("pedro@example.com", "viewer", PAST)
    [old] = invitations_of(client, owner, oid)
    assert old["expired"] is True
    renewed = invite(client, owner, oid, "pedro@example.com", "viewer")
    assert renewed.status_code == 201
    [only] = invitations_of(client, owner, oid)
    assert only["id"] == renewed.json()["id"] != old["id"] and only["expired"] is False


def test_inviting_an_email_with_a_pending_invitation_replaces_it_with_the_new_role(client, oid, owner):
    invite(client, owner, oid, "pedro@example.com", "viewer")
    invite(client, owner, oid, "pedro@example.com", "admin")
    assert [(i["email"], i["role"]) for i in invitations_of(client, owner, oid)] == [("pedro@example.com", "admin")]


# --- audit: written by every member and invitation operation -------------------------------------------


def test_member_and_invitation_operations_write_audit_entries(client, oid, owner, viewer):
    inv = invite(client, owner, oid, "marta@example.com", "admin").json()
    client.delete(f"/v1/orgs/{oid}/invitations/{inv['id']}", headers=acting(owner))
    patch_role(client, owner, oid, viewer, "admin")
    remove(client, owner, oid, viewer)
    entries = audit_of(client, owner, oid)
    assert [e["action"] for e in entries] == ["member.removed", "member.role_changed", "invitation.revoked",
                                              "invitation.created"]
    removed, changed, revoked, created = entries
    assert changed["meta"] == {"email": "vwr@example.com", "name": "Vwr", "from": "viewer", "to": "admin"}
    assert (changed["target_type"], changed["target_id"]) == ("person", pid_of(viewer))
    assert changed["actor_person_id"] == pid_of(owner) and changed["actor_name"] == "Olga"
    assert changed["actor_kind"] == "person" and changed["org_id"] == oid and changed["project_id"] is None
    assert removed["meta"]["email"] == "vwr@example.com" and removed["meta"]["role"] == "admin"
    assert (created["target_type"], created["target_id"]) == ("invitation", inv["id"])
    assert created["meta"] == {"email": "marta@example.com", "role": "admin"} == revoked["meta"]
    assert all(e["ts"].endswith("Z") and isinstance(e["seq"], int) for e in entries)


def test_a_refused_operation_writes_nothing(client, oid, owner, admin):
    before = audit_of(client, owner, oid)
    patch_role(client, admin, oid, owner, "viewer")  # 403
    remove(client, owner, oid, owner)  # 409
    invite(client, admin, oid, "boss@example.com", "owner")  # 403
    invite(client, owner, oid, "bad", "viewer")  # 422
    assert audit_of(client, owner, oid) == before


def test_a_resent_invitation_is_audited_as_resent(client, oid, owner):
    invite(client, owner, oid, "pedro@example.com")
    invite(client, owner, oid, "pedro@example.com")
    assert [e["action"] for e in audit_of(client, owner, oid)] == ["invitation.resent", "invitation.created"]


# --- GET /v1/orgs/{id}/audit ---------------------------------------------------------------------------


def test_a_viewer_cannot_read_the_audit_but_an_admin_can(client, oid, owner, admin, viewer):
    r = client.get(f"/v1/orgs/{oid}/audit", headers=acting(viewer))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    assert client.get(f"/v1/orgs/{oid}/audit", headers=acting(admin)).status_code == 200


def test_the_audit_is_per_org(client, engine, oid, owner):
    invite(client, owner, org_of(engine, SDK_B), "z@example.com")
    assert audit_of(client, owner, oid) == []


def test_the_audit_is_read_only(client, oid, owner):
    for method in ("post", "put", "patch", "delete"):
        r = getattr(client, method)(f"/v1/orgs/{oid}/audit", headers=acting(owner))
        assert r.status_code == 405
    assert client.delete(f"/v1/orgs/{oid}/audit/1", headers=acting(owner)).status_code == 404


def test_audit_is_newest_first_with_limit_and_before(client, oid, owner):
    for n in range(5):
        invite(client, owner, oid, f"p{n}@example.com")
    first = audit_of(client, owner, oid, limit=2)
    assert [e["meta"]["email"] for e in first] == ["p4@example.com", "p3@example.com"]
    older = audit_of(client, owner, oid, limit=2, before=first[-1]["seq"])
    assert [e["meta"]["email"] for e in older] == ["p2@example.com", "p1@example.com"]
    rest = audit_of(client, owner, oid, limit=10, before=older[-1]["seq"])
    assert [e["meta"]["email"] for e in rest] == ["p0@example.com"]


def test_audit_limit_defaults_to_50_and_is_validated(client, oid, owner):
    for n in range(55):
        invite(client, owner, oid, f"p{n}@example.com")
    assert len(audit_of(client, owner, oid)) == 50
    for bad in ({"limit": 0}, {"limit": 201}, {"before": 0}):
        assert client.get(f"/v1/orgs/{oid}/audit", params=bad, headers=acting(owner)).status_code == 422


def test_the_project_filter_keeps_only_entries_that_reference_that_project(client, oid, pid, owner):
    invite(client, owner, oid, "m@example.com")  # org-level: no project
    block = block_a_user(client)
    client.post(f"/v1/decisions/{block}/normal", headers=acting(owner, pid))
    assert [e["action"] for e in audit_of(client, owner, oid)] == ["decision.marked_normal", "invitation.created"]
    only = audit_of(client, owner, oid, project=pid)
    assert [e["action"] for e in only] == ["decision.marked_normal"]
    assert only[0]["project_id"] == pid
    assert audit_of(client, owner, oid, project="p_nope") == []


# --- mark-normal: admin or owner, audited ----------------------------------------------------------------


def block_a_user(client, token=SDK):
    """Trigger the engine's first rule (critical route never used, from a new /24): returns the decision id."""
    event = {"id": "e_atk", "ts": "2026-10-26T10:14:03Z", "user_id": "u_42", "session_id": "s_atk", "ip": "203.0.113.9",
             "method": "POST", "route": "/payouts/bank-account", "status": 200, "bytes": 1}
    r = client.post("/v1/check", json={"event": event}, headers=bearer(token))
    assert r.json()["verdict"] == "block", r.text
    return r.json()["decision_id"]


def test_an_admin_marks_normal_and_it_is_audited_with_the_project(client, oid, pid, owner, admin):
    decision = block_a_user(client)
    r = client.post(f"/v1/decisions/{decision}/normal", headers=acting(admin, pid))
    assert r.status_code == 200 and r.json()["status"] == "marked_normal"
    [entry] = audit_of(client, owner, oid)
    assert entry["action"] == "decision.marked_normal" and entry["actor_person_id"] == pid_of(admin)
    assert (entry["target_type"], entry["target_id"]) == ("decision", decision)
    assert entry["project_id"] == pid and entry["project_name"] == "Demo 1"
    assert "u_42" not in str(entry)  # the customer's user id stays out of the audit


def test_an_owner_marks_normal_too(client, pid, owner):
    decision = block_a_user(client)
    assert client.post(f"/v1/decisions/{decision}/normal", headers=acting(owner, pid)).status_code == 200


def test_a_viewer_cannot_mark_normal_and_nothing_is_written(client, oid, pid, owner, viewer):
    decision = block_a_user(client)
    r = client.post(f"/v1/decisions/{decision}/normal", headers=acting(viewer, pid))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    assert audit_of(client, owner, oid) == []
    assert client.get("/v1/decisions", headers=acting(owner, pid)).json()["decisions"][0]["status"] == "open"


def test_an_unknown_decision_or_a_repeat_writes_no_extra_entry(client, oid, pid, owner):
    assert client.post("/v1/decisions/d_nope/normal", headers=acting(owner, pid)).status_code == 404
    decision = block_a_user(client)
    client.post(f"/v1/decisions/{decision}/normal", headers=acting(owner, pid))
    client.post(f"/v1/decisions/{decision}/normal", headers=acting(owner, pid))
    assert len(audit_of(client, owner, oid)) == 1
