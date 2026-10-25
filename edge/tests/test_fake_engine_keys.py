"""Fake engine: per-project API keys (contract 12). Created keys authenticate as SDK keys at once, revoked
keys answer 401 like unknown ones, the plaintext is shown once, and every operation is audited."""

import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from fake_engine import create_app
from tests.test_fake_engine_accounts import FUTURE, OWNER, PANEL, SDK, SDK_B, acting, bearer, login, org_of, project_of

KEY_FORMAT = re.compile(r"^vrd_[a-z0-9]{8}_[A-Za-z0-9_-]{43}$")
EVENT = {"id": "e_1", "ts": "2026-10-26T10:14:03Z", "user_id": "u_1", "session_id": "s_1", "ip": "1.1.1.1", "method": "GET",
         "route": "/invoices", "status": 200, "bytes": 1}


@pytest.fixture
def engine():
    return create_app(tokens={SDK, SDK_B}, seed=[], learn_delay=0, panel_tokens={PANEL}, bootstrap_owners={OWNER})


@pytest.fixture
def client(engine):
    return TestClient(engine)


@pytest.fixture
def pid(engine):
    return project_of(engine, SDK)


@pytest.fixture
def owner(client):
    return login(client, sub="s-own", email=OWNER, name="Olga")


def person_in(client, engine, role, sub, token=SDK):
    engine.state.orgs[org_of(engine, token)].invite(f"{sub}@example.com", role, FUTURE)
    return login(client, sub=sub, email=f"{sub}@example.com", name=sub.capitalize())


def create_key(client, person, pid, name="Producción"):
    return client.post(f"/v1/projects/{pid}/api-keys", json={"name": name}, headers=acting(person))


def list_keys(client, person, pid):
    r = client.get(f"/v1/projects/{pid}/api-keys", headers=acting(person))
    assert r.status_code == 200, r.text
    return r.json()["api_keys"]


def revoke(client, person, pid, key_id):
    return client.post(f"/v1/projects/{pid}/api-keys/{key_id}/revoke", headers=acting(person))


def sdk_calls(client, token):
    """Status of the four SDK endpoints with `token`."""
    return {
        "events": client.post("/v1/events", json={"events": [{**EVENT, "id": "e_" + token[-4:]}]}, headers=bearer(token)).status_code,
        "check": client.post("/v1/check", json={"event": {**EVENT, "id": "c_" + token[-4:]}}, headers=bearer(token)).status_code,
        "blocklist": client.get("/v1/blocklist", headers=bearer(token)).status_code,
        "routes": client.get("/v1/routes", headers=bearer(token)).status_code,
    }


# --- create ------------------------------------------------------------------------------------------


def test_an_owner_or_admin_creates_a_key_and_sees_it_once(client, engine, pid, owner):
    admin = person_in(client, engine, "admin", "adm")
    for who in (owner, admin):
        r = create_key(client, who, pid)
        assert r.status_code == 201
        body = r.json()
        assert set(body) == {"id", "name", "prefix", "key"} and body["name"] == "Producción"
        assert KEY_FORMAT.match(body["key"]) and body["key"].startswith(body["prefix"] + "_")
        assert r.headers["cache-control"] == "no-store"


def test_every_key_is_different(client, pid, owner):
    keys = {create_key(client, owner, pid).json()["key"] for _ in range(5)}
    assert len(keys) == 5


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
def test_a_key_needs_a_name_of_up_to_80_characters(client, pid, owner, name):
    assert create_key(client, owner, pid, name).status_code == 422


def test_a_viewer_a_stranger_and_an_sdk_key_cannot_manage_keys(client, engine, pid, owner):
    viewer = person_in(client, engine, "viewer", "vwr")
    stranger = login(client, sub="s-x", email="x@example.com")
    for who in (viewer, stranger):
        assert create_key(client, who, pid).status_code == 403
        assert client.get(f"/v1/projects/{pid}/api-keys", headers=acting(who)).status_code == 403
    assert client.get(f"/v1/projects/{pid}/api-keys", headers=bearer(SDK)).status_code == 403
    assert create_key(client, owner, "p_nope").status_code == 403
    assert list_keys(client, owner, pid) == []


def test_a_member_of_another_org_cannot_manage_this_projects_keys(client, engine, pid, owner):
    elsewhere = person_in(client, engine, "admin", "far", token=SDK_B)
    assert create_key(client, elsewhere, pid).status_code == 403


# --- list --------------------------------------------------------------------------------------------


def test_the_list_never_returns_the_key_or_its_hash(client, pid, owner):
    created = create_key(client, owner, pid).json()
    text = client.get(f"/v1/projects/{pid}/api-keys", headers=acting(owner)).text
    assert created["key"] not in text and "key_hash" not in text and hashlib.sha256(created["key"].encode()).hexdigest() not in text
    [row] = list_keys(client, owner, pid)
    assert "key" not in row
    assert row == {"id": created["id"], "name": "Producción", "prefix": created["prefix"], "status": "active",
                   "created_by": owner["person"]["id"], "created_by_name": "Olga", "created_at": row["created_at"],
                   "last_used_at": None, "revoked_at": None, "revoked_by_name": None}
    assert row["created_at"].endswith("Z")


def test_the_list_is_newest_first_and_per_project(client, engine, pid, owner):
    first = create_key(client, owner, pid, "uno").json()["id"]
    second = create_key(client, owner, pid, "dos").json()["id"]
    assert [k["id"] for k in list_keys(client, owner, pid)] == [second, first]
    assert list_keys(client, owner, project_of(engine, SDK_B)) == []


def test_keys_are_stored_as_a_hash_only(client, engine, pid, owner):
    key = create_key(client, owner, pid).json()["key"]
    stored = list(engine.state.api_keys.values())
    assert [k.key_hash for k in stored] == [hashlib.sha256(key.encode()).hexdigest()]
    assert key not in repr(stored)


# --- a created key is an SDK key -----------------------------------------------------------------------


def test_a_new_key_works_at_once_on_the_four_sdk_endpoints(client, pid, owner):
    key = create_key(client, owner, pid).json()["key"]
    assert sdk_calls(client, key) == {"events": 202, "check": 200, "blocklist": 200, "routes": 200}


def test_a_key_acts_on_its_own_project_only(client, engine, pid, owner):
    key = create_key(client, owner, pid).json()["key"]
    client.post("/v1/events", json={"events": [EVENT]}, headers=bearer(key))
    mine = client.get("/v1/activity", headers=acting(owner, pid)).json()["activity"]
    other = client.get("/v1/activity", headers=acting(owner, project_of(engine, SDK_B))).json()["activity"]
    assert [a["event_id"] for a in mine] == ["e_1"] and other == []


def test_several_keys_are_active_at_once_so_a_rotation_does_not_cut_service(client, pid, owner):
    old = create_key(client, owner, pid, "old").json()
    new = create_key(client, owner, pid, "new").json()
    assert sdk_calls(client, old["key"])["blocklist"] == 200 == sdk_calls(client, new["key"])["blocklist"]
    assert revoke(client, owner, pid, old["id"]).status_code == 200
    assert sdk_calls(client, old["key"])["blocklist"] == 401
    assert sdk_calls(client, new["key"])["blocklist"] == 200


def test_a_key_is_not_a_panel_credential(client, pid, owner):
    key = create_key(client, owner, pid).json()["key"]
    r = client.get("/v1/decisions", headers=bearer(key))
    assert r.status_code == 403
    assert client.post("/v1/people/login", json={"google_sub": "s", "email": "a@example.com"}, headers=bearer(key)).status_code == 403


def test_the_seeded_token_keeps_working(client, owner):
    assert sdk_calls(client, SDK)["blocklist"] == 200


def test_a_key_of_a_deleted_project_or_org_is_rejected(client, engine, pid, owner):
    key = create_key(client, owner, pid).json()["key"]
    engine.state.projects[pid].deleted_at = "2026-10-26T00:00:00Z"
    assert sdk_calls(client, key)["blocklist"] == 401
    engine.state.projects[pid].deleted_at = None
    engine.state.orgs[org_of(engine, SDK)].deleted_at = "2026-10-26T00:00:00Z"
    assert sdk_calls(client, key)["blocklist"] == 401


# --- last_used_at ----------------------------------------------------------------------------------------


def test_last_used_at_is_empty_until_the_key_is_used_then_updated(client, pid, owner):
    created = create_key(client, owner, pid).json()
    assert list_keys(client, owner, pid)[0]["last_used_at"] is None
    client.get("/v1/blocklist", headers=bearer(created["key"]))
    used = list_keys(client, owner, pid)[0]["last_used_at"]
    assert used and used.endswith("Z")


def test_a_rejected_key_does_not_touch_last_used_at(client, pid, owner):
    created = create_key(client, owner, pid).json()
    revoke(client, owner, pid, created["id"])
    client.get("/v1/blocklist", headers=bearer(created["key"]))
    assert list_keys(client, owner, pid)[0]["last_used_at"] is None


# --- revoke ----------------------------------------------------------------------------------------------


def test_a_revoked_key_is_a_401_identical_to_an_unknown_one(client, pid, owner):
    created = create_key(client, owner, pid).json()
    r = revoke(client, owner, pid, created["id"])
    assert r.status_code == 200 and r.json()["status"] == "revoked" and r.json()["revoked_by_name"] == "Olga"
    revoked = client.get("/v1/blocklist", headers=bearer(created["key"]))
    unknown = client.get("/v1/blocklist", headers=bearer("vrd_zzzzzzzz_" + "A" * 43))
    assert revoked.status_code == unknown.status_code == 401 and revoked.json() == unknown.json() == {"error": "unauthorized"}
    assert sdk_calls(client, created["key"]) == {"events": 401, "check": 401, "blocklist": 401, "routes": 401}


def test_revoking_keeps_the_row_in_the_list_as_revoked(client, pid, owner):
    created = create_key(client, owner, pid).json()
    revoke(client, owner, pid, created["id"])
    [row] = list_keys(client, owner, pid)
    assert row["status"] == "revoked" and row["revoked_at"].endswith("Z") and row["revoked_by_name"] == "Olga"


def test_revoking_twice_is_harmless_and_audited_once(client, engine, pid, owner):
    created = create_key(client, owner, pid).json()
    assert revoke(client, owner, pid, created["id"]).status_code == 200
    assert revoke(client, owner, pid, created["id"]).status_code == 200
    audit = client.get(f"/v1/orgs/{org_of(engine, SDK)}/audit", headers=acting(owner)).json()["entries"]
    assert [e["action"] for e in audit] == ["api_key.revoked", "api_key.created"]


def test_the_last_active_key_can_be_revoked(client, pid, owner):
    created = create_key(client, owner, pid).json()
    assert revoke(client, owner, pid, created["id"]).status_code == 200


def test_revoke_is_scoped_to_the_project_and_the_role(client, engine, pid, owner):
    viewer = person_in(client, engine, "viewer", "vwr")
    created = create_key(client, owner, pid).json()
    assert revoke(client, viewer, pid, created["id"]).status_code == 403
    assert revoke(client, owner, pid, "key_nope").status_code == 404
    other = project_of(engine, SDK_B)
    assert revoke(client, owner, other, created["id"]).status_code == 404  # another project's path
    assert sdk_calls(client, created["key"])["blocklist"] == 200


# --- audit ---------------------------------------------------------------------------------------------------


def test_create_and_revoke_are_audited_with_the_project_and_never_the_key(client, engine, pid, owner):
    created = create_key(client, owner, pid, "Producción").json()
    revoke(client, owner, pid, created["id"])
    response = client.get(f"/v1/orgs/{org_of(engine, SDK)}/audit", headers=acting(owner))
    revoked, made = response.json()["entries"]
    assert (made["action"], made["target_type"], made["target_id"]) == ("api_key.created", "api_key", created["id"])
    assert made["project_id"] == pid and made["project_name"] == "Demo 1"
    assert made["meta"] == {"name": "Producción", "prefix": created["prefix"]}
    assert revoked["action"] == "api_key.revoked" and revoked["actor_person_id"] == owner["person"]["id"]
    assert created["key"] not in response.text
    assert created["key"].split("_", 2)[2] not in response.text


def test_a_refused_key_operation_writes_no_audit(client, engine, pid, owner):
    viewer = person_in(client, engine, "viewer", "vwr")
    create_key(client, viewer, pid)
    create_key(client, owner, pid, "")
    assert client.get(f"/v1/orgs/{org_of(engine, SDK)}/audit", headers=acting(owner)).json()["entries"] == []
