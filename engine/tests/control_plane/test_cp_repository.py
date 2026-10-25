import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from veridian_engine import db
from veridian_engine.control_plane import repository
from veridian_engine.control_plane.repository import (
    InvitationUnavailable,
    LastOwnerError,
    NotFound,
    PermissionDenied,
)
from veridian_engine.keys import hash_key, key_prefix

# Fixtures live here, not in a conftest.py: a nested conftest would shadow the
# top-level `conftest` module that other engine tests import by name.

T0 = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "control.db"
    db.init_db(str(path))  # the control plane ships as migrations 0003 and 0004
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


@pytest.fixture
def make_person(conn):
    def make(name: str) -> str:
        return repository.upsert_person(
            conn, google_sub=f"sub-{name}", email=f"{name}@example.com", name=name, now=T0
        )

    return make


@pytest.fixture
def owner(make_person) -> str:
    return make_person("olga")


@pytest.fixture
def org(conn, owner) -> str:
    return repository.create_organization(conn, creator_id=owner, name="Acme", now=T0)


@pytest.fixture
def project(conn, owner, org) -> str:
    return repository.create_project(conn, actor_id=owner, org_id=org, name="Shop", enforcement="observe", now=T0)


@pytest.fixture
def add_member(conn, make_person):
    def add(org_id, actor, person_name, role):
        person = make_person(person_name)
        email = f"{person_name}@example.com"
        invitation = repository.create_invitation(conn, actor_id=actor, org_id=org_id, email=email, role=role, now=T0)
        assert repository.accept_pending_invitations(conn, person_id=person, verified_email=email, now=T0) == [invitation]
        return person

    return add


def audit_rows(conn, org_id):
    return conn.execute("SELECT * FROM audit_log WHERE org_id = ? ORDER BY seq", (org_id,)).fetchall()


# --- ids, organizations and project creation ---


def test_new_ids_are_prefix_plus_random(conn, owner, org):
    first = repository.create_project(conn, actor_id=owner, org_id=org, name="A", enforcement="observe", now=T0)
    second = repository.create_project(conn, actor_id=owner, org_id=org, name="B", enforcement="observe", now=T0)
    assert re.fullmatch(r"prj_[0-9a-f]{24}", first)
    assert re.fullmatch(r"prj_[0-9a-f]{24}", second)
    assert re.fullmatch(r"org_[0-9a-f]{24}", org)
    assert first != second


def test_entity_id_prefixes(conn, owner, org, project):
    assert re.fullmatch(r"per_[0-9a-f]{24}", owner)
    invitation = repository.create_invitation(conn, actor_id=owner, org_id=org, email="v@example.com", role="viewer", now=T0)
    assert re.fullmatch(r"inv_[0-9a-f]{24}", invitation)
    created = repository.create_api_key(conn, actor_id=owner, project_id=project, name="prod", now=T0)
    assert re.fullmatch(r"key_[0-9a-f]{24}", created.id)


def test_organization_creation_makes_creator_owner(conn, owner, org):
    assert repository.get_role(conn, org, owner) == "owner"
    row = conn.execute("SELECT * FROM organization WHERE id = ?", (org,)).fetchone()
    assert (row["name"], row["created_at"], row["deleted_at"]) == ("Acme", "2026-10-26T12:00:00Z", None)
    assert [r["action"] for r in audit_rows(conn, org)] == ["org.create"]


def test_project_belongs_to_the_org_with_defaults_and_no_project_roles(conn, owner, org, project):
    row = conn.execute("SELECT * FROM project WHERE id = ?", (project,)).fetchone()
    assert row["org_id"] == org
    assert row["enforcement"] == "observe"
    assert row["ai_enabled"] == 1
    assert row["retention_days"] == 90
    assert row["created_at"] == "2026-10-26T12:00:00Z"
    assert repository.role_in_project(conn, project, owner) == "owner"
    assert [r["action"] for r in audit_rows(conn, org)] == ["org.create", "project.create"]
    assert audit_rows(conn, org)[-1]["project_id"] == project


def test_enforcement_must_be_valid(conn, owner, org):
    with pytest.raises(ValueError):
        repository.create_project(conn, actor_id=owner, org_id=org, name="A", enforcement="maybe", now=T0)


def test_only_org_admins_create_projects(conn, owner, org, add_member, make_person):
    admin = add_member(org, owner, "ada", "admin")
    viewer = add_member(org, owner, "vic", "viewer")
    stranger = make_person("eve")
    assert repository.create_project(conn, actor_id=admin, org_id=org, name="A", enforcement="observe", now=T0)
    for actor in (viewer, stranger):
        with pytest.raises(PermissionDenied):
            repository.create_project(conn, actor_id=actor, org_id=org, name="B", enforcement="observe", now=T0)


def test_upsert_person_is_keyed_by_google_sub(conn):
    first = repository.upsert_person(conn, google_sub="s1", email="a@example.com", name="A", now=T0)
    later = T0 + timedelta(days=1)
    second = repository.upsert_person(conn, google_sub="s1", email="new@example.com", name="A", now=later)
    assert first == second
    row = conn.execute("SELECT email, last_login_at FROM person WHERE id = ?", (first,)).fetchone()
    assert row["email"] == "new@example.com"
    assert row["last_login_at"] == "2026-10-27T12:00:00Z"


# --- an org role covers every project of the org, and no other ---


def test_org_role_applies_to_every_project_of_the_org_and_to_no_other(conn, owner, org, add_member, make_person):
    viewer = add_member(org, owner, "vic", "viewer")
    first = repository.create_project(conn, actor_id=owner, org_id=org, name="A", enforcement="observe", now=T0)
    second = repository.create_project(conn, actor_id=owner, org_id=org, name="B", enforcement="observe", now=T0)
    other_owner = make_person("otto")
    other_org = repository.create_organization(conn, creator_id=other_owner, name="Other", now=T0)
    foreign = repository.create_project(conn, actor_id=other_owner, org_id=other_org, name="C", enforcement="observe", now=T0)
    assert repository.role_in_project(conn, first, viewer) == "viewer"
    assert repository.role_in_project(conn, second, viewer) == "viewer"
    assert repository.role_in_project(conn, foreign, viewer) is None
    assert repository.role_in_project(conn, "prj_missing", viewer) is None


def test_a_person_has_one_role_per_org_and_may_belong_to_several(conn, owner, org, add_member, make_person):
    ada = add_member(org, owner, "ada", "admin")
    other_owner = make_person("otto")
    other_org = repository.create_organization(conn, creator_id=other_owner, name="Other", now=T0)
    repository.create_invitation(conn, actor_id=other_owner, org_id=other_org, email="ada@example.com", role="viewer", now=T0)
    repository.accept_pending_invitations(conn, person_id=ada, verified_email="ada@example.com", now=T0)
    assert repository.get_role(conn, org, ada) == "admin"
    assert repository.get_role(conn, other_org, ada) == "viewer"
    assert [o["id"] for o in repository.list_orgs_for(conn, ada)] == [org, other_org]


def test_deleted_project_or_org_grants_no_role(conn, owner, org, project):
    conn.execute("UPDATE project SET deleted_at = '2026-10-27T00:00:00Z' WHERE id = ?", (project,))
    assert repository.role_in_project(conn, project, owner) is None
    conn.execute("UPDATE project SET deleted_at = NULL WHERE id = ?", (project,))
    conn.execute("UPDATE organization SET deleted_at = '2026-10-27T00:00:00Z' WHERE id = ?", (org,))
    assert repository.role_in_project(conn, project, owner) is None
    assert repository.get_role(conn, org, owner) is None


# --- last owner rule, per org ---


def test_cannot_remove_last_owner(conn, owner, org):
    with pytest.raises(LastOwnerError):
        repository.remove_member(conn, actor_id=owner, org_id=org, person_id=owner, now=T0)
    assert repository.get_role(conn, org, owner) == "owner"


def test_cannot_demote_last_owner(conn, owner, org):
    with pytest.raises(LastOwnerError):
        repository.set_member_role(conn, actor_id=owner, org_id=org, person_id=owner, role="admin", now=T0)
    assert repository.get_role(conn, org, owner) == "owner"


def test_owner_can_leave_when_another_owner_exists(conn, owner, org, add_member):
    second = add_member(org, owner, "sam", "owner")
    repository.remove_member(conn, actor_id=second, org_id=org, person_id=owner, now=T0)
    assert repository.get_role(conn, org, owner) is None
    with pytest.raises(LastOwnerError):
        repository.set_member_role(conn, actor_id=second, org_id=org, person_id=second, role="viewer", now=T0)


# --- role checks on member management ---


def test_admin_cannot_invite_or_touch_owners(conn, owner, org, add_member):
    admin = add_member(org, owner, "ada", "admin")
    with pytest.raises(PermissionDenied):
        repository.create_invitation(conn, actor_id=admin, org_id=org, email="x@example.com", role="owner", now=T0)
    with pytest.raises(PermissionDenied):
        repository.remove_member(conn, actor_id=admin, org_id=org, person_id=owner, now=T0)
    with pytest.raises(PermissionDenied):
        repository.set_member_role(conn, actor_id=admin, org_id=org, person_id=admin, role="owner", now=T0)


def test_admin_manages_viewers(conn, owner, org, add_member):
    admin = add_member(org, owner, "ada", "admin")
    viewer = add_member(org, admin, "vic", "viewer")
    repository.set_member_role(conn, actor_id=admin, org_id=org, person_id=viewer, role="admin", now=T0)
    assert repository.get_role(conn, org, viewer) == "admin"
    repository.remove_member(conn, actor_id=admin, org_id=org, person_id=viewer, now=T0)
    assert repository.get_role(conn, org, viewer) is None


def test_viewer_and_non_member_cannot_change_state(conn, owner, org, project, make_person, add_member):
    viewer = add_member(org, owner, "vic", "viewer")
    stranger = make_person("eve")
    for actor in (viewer, stranger):
        with pytest.raises(PermissionDenied):
            repository.create_invitation(conn, actor_id=actor, org_id=org, email="x@example.com", role="viewer", now=T0)
        with pytest.raises(PermissionDenied):
            repository.create_api_key(conn, actor_id=actor, project_id=project, name="k", now=T0)


# --- audit in the same transaction ---


def test_change_writes_audit_with_actor(conn, owner, org, add_member):
    viewer = add_member(org, owner, "vic", "viewer")
    repository.remove_member(conn, actor_id=owner, org_id=org, person_id=viewer, now=T0)
    last = audit_rows(conn, org)[-1]
    assert last["action"] == "member.remove"
    assert last["project_id"] is None  # an org action names no project
    assert last["actor_person_id"] == owner
    assert last["actor_kind"] == "person"
    assert last["target_type"] == "person"
    assert last["target_id"] == viewer
    assert last["ts"] == "2026-10-26T12:00:00Z"


def test_failed_audit_rolls_back_the_change(conn, owner, org, project, monkeypatch, add_member):
    viewer = add_member(org, owner, "vic", "viewer")
    before = len(audit_rows(conn, org))

    def broken_audit(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(repository, "record_audit", broken_audit)
    with pytest.raises(RuntimeError):
        repository.remove_member(conn, actor_id=owner, org_id=org, person_id=viewer, now=T0)
    with pytest.raises(RuntimeError):
        repository.create_api_key(conn, actor_id=owner, project_id=project, name="k", now=T0)

    assert repository.get_role(conn, org, viewer) == "viewer"
    assert conn.execute("SELECT COUNT(*) FROM api_key").fetchone()[0] == 0
    assert len(audit_rows(conn, org)) == before


def test_failed_org_creation_leaves_no_rows(conn, owner, monkeypatch):
    monkeypatch.setattr(repository, "record_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(RuntimeError):
        repository.create_organization(conn, creator_id=owner, name="A", now=T0)
    assert conn.execute("SELECT COUNT(*) FROM organization").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM membership").fetchone()[0] == 0


def test_audit_derives_the_org_from_the_project_and_hashes_user_targets(conn, owner, org, project):
    repository.record_audit(
        conn, project_id=project, actor_person_id=owner, actor_kind="person",
        action="decision.mark_normal", target_type="user", target_id="alice@shop.com", now=T0,
    )
    last = audit_rows(conn, org)[-1]
    assert last["project_id"] == project
    assert last["target_id"] != "alice@shop.com"
    assert last["target_id"] == repository.hash_user_id(project, "alice@shop.com")
    assert last["target_id"] == hashlib.sha256(f"{project}:alice@shop.com".encode()).hexdigest()


# --- invitations (to the org) ---


def test_invitation_accepted_before_expiry(conn, owner, org, project, make_person):
    invitation = repository.create_invitation(conn, actor_id=owner, org_id=org, email="New@Example.com", role="admin", now=T0)
    person = make_person("new")
    accepted = repository.accept_pending_invitations(
        conn, person_id=person, verified_email="new@example.com", now=T0 + timedelta(days=6)
    )
    assert accepted == [invitation]
    assert repository.get_role(conn, org, person) == "admin"
    assert repository.role_in_project(conn, project, person) == "admin"
    assert audit_rows(conn, org)[-1]["action"] == "invitation.accept"
    # Accepting is one-shot.
    assert repository.accept_pending_invitations(conn, person_id=person, verified_email="new@example.com", now=T0) == []


def test_invitation_expires(conn, owner, org, make_person):
    repository.create_invitation(conn, actor_id=owner, org_id=org, email="late@example.com", role="viewer", now=T0)
    person = make_person("late")
    expired_at = T0 + repository.INVITATION_TTL
    assert repository.accept_pending_invitations(conn, person_id=person, verified_email="late@example.com", now=expired_at) == []
    assert repository.get_role(conn, org, person) is None


def test_revoked_invitation_cannot_be_accepted(conn, owner, org, make_person):
    invitation = repository.create_invitation(conn, actor_id=owner, org_id=org, email="r@example.com", role="viewer", now=T0)
    repository.revoke_invitation(conn, actor_id=owner, org_id=org, invitation_id=invitation, now=T0)
    person = make_person("r")
    assert repository.accept_pending_invitations(conn, person_id=person, verified_email="r@example.com", now=T0) == []
    with pytest.raises(InvitationUnavailable):
        repository.revoke_invitation(conn, actor_id=owner, org_id=org, invitation_id=invitation, now=T0)


def test_invitation_does_not_downgrade_existing_member(conn, owner, org):
    repository.create_invitation(conn, actor_id=owner, org_id=org, email="olga@example.com", role="viewer", now=T0)
    assert repository.accept_pending_invitations(conn, person_id=owner, verified_email="olga@example.com", now=T0) == []
    assert repository.get_role(conn, org, owner) == "owner"


# --- bootstrap owners ---


def test_bootstrap_takes_only_orgs_without_an_owner(conn, owner, org, make_person):
    conn.execute("INSERT INTO organization (id, name, created_at) VALUES ('org_orphan', 'Orphan', '2026-10-26T12:00:00Z')")
    boot = make_person("boot")
    taken = repository.bootstrap_owner(conn, person_id=boot, email="Boot@Example.com", owners={"boot@example.com"}, now=T0)
    assert taken == ["org_orphan"]
    assert repository.get_role(conn, "org_orphan", boot) == "owner"
    assert repository.get_role(conn, org, boot) is None
    other = make_person("late")
    assert repository.bootstrap_owner(conn, person_id=other, email="late@example.com", owners={"late@example.com"}, now=T0) == []


def test_bootstrap_ignores_emails_off_the_list(conn, make_person):
    conn.execute("INSERT INTO organization (id, name, created_at) VALUES ('org_orphan', 'Orphan', '2026-10-26T12:00:00Z')")
    person = make_person("ana")
    assert repository.bootstrap_owner(conn, person_id=person, email="ana@example.com", owners=set(), now=T0) == []
    assert repository.get_role(conn, "org_orphan", person) is None


# --- api keys (contract 12 format) ---


def test_api_key_uses_the_contract_format_and_stores_only_hash_and_prefix(conn, owner, org, project):
    created = repository.create_api_key(conn, actor_id=owner, project_id=project, name="prod", now=T0)
    assert re.fullmatch(r"vrd_[0-9a-f]{8}_[A-Za-z0-9_-]+", created.key)
    assert created.prefix == key_prefix(created.key)
    row = conn.execute("SELECT * FROM api_key WHERE id = ?", (created.id,)).fetchone()
    assert created.key not in tuple(str(value) for value in row)
    assert row["key_hash"] == hash_key(created.key)
    assert row["prefix"] == created.prefix
    assert row["scope"] == "sdk"
    assert row["created_by"] == owner
    assert row["last_used_at"] is None and row["revoked_at"] is None
    assert audit_rows(conn, org)[-1]["action"] == "api_key.create"


def test_list_and_revoke_api_keys(conn, owner, org, project):
    created = repository.create_api_key(conn, actor_id=owner, project_id=project, name="prod", now=T0)
    [listed] = repository.list_api_keys(conn, project)
    assert listed == {
        "id": created.id, "name": "prod", "prefix": created.prefix, "created_by": owner,
        "created_at": "2026-10-26T12:00:00Z", "last_used_at": None, "revoked_at": None,
    }
    repository.revoke_api_key(conn, actor_id=owner, project_id=project, key_id=created.id, now=T0)
    row = conn.execute("SELECT revoked_at FROM api_key WHERE id = ?", (created.id,)).fetchone()
    assert row["revoked_at"] == "2026-10-26T12:00:00Z"
    assert audit_rows(conn, org)[-1]["action"] == "api_key.revoke"
    with pytest.raises(NotFound):
        repository.revoke_api_key(conn, actor_id=owner, project_id=project, key_id=created.id, now=T0)


# --- transaction boundaries ---


def test_change_outside_a_transaction_is_committed(conn, owner, org, tmp_path):
    project_id = repository.create_project(conn, actor_id=owner, org_id=org, name="A", enforcement="enforce", now=T0)
    other = sqlite3.connect(tmp_path / "control.db")
    try:
        assert other.execute("SELECT enforcement FROM project WHERE id = ?", (project_id,)).fetchone() == ("enforce",)
    finally:
        other.close()


def test_change_joins_the_callers_transaction(conn, owner, org):
    conn.commit()
    conn.execute("BEGIN")
    repository.create_project(conn, actor_id=owner, org_id=org, name="A", enforcement="observe", now=T0)
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 0
    assert [r["action"] for r in audit_rows(conn, org)] == ["org.create"]
