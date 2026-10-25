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
    PermissionDenied,
)

# Fixtures live here, not in a conftest.py: a nested conftest would shadow the
# top-level `conftest` module that other engine tests import by name.

T0 = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "control.db"
    db.init_db(str(path))  # the control plane ships as migration 0003
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
def project(conn, owner) -> str:
    return repository.create_project(conn, creator_id=owner, name="Shop", enforcement="observe", now=T0)


@pytest.fixture
def add_member(conn, make_person):
    def add(project_id, actor, person_name, role):
        person = make_person(person_name)
        email = f"{person_name}@example.com"
        invitation = repository.create_invitation(
            conn, actor_id=actor, project_id=project_id, email=email, role=role, now=T0
        )
        assert repository.accept_pending_invitations(conn, person_id=person, verified_email=email, now=T0) == [invitation]
        return person

    return add


def audit_rows(conn, project_id):
    return conn.execute(
        "SELECT * FROM audit_log WHERE project_id = ? ORDER BY seq", (project_id,)
    ).fetchall()


# --- ids and project creation ---


def test_new_project_id_is_prj_plus_random(conn, owner):
    first = repository.create_project(conn, creator_id=owner, name="A", enforcement="observe", now=T0)
    second = repository.create_project(conn, creator_id=owner, name="B", enforcement="observe", now=T0)
    assert re.fullmatch(r"prj_[0-9a-f]{24}", first)
    assert re.fullmatch(r"prj_[0-9a-f]{24}", second)
    assert first != second


def test_entity_id_prefixes(conn, owner, project):
    assert re.fullmatch(r"per_[0-9a-f]{24}", owner)
    invitation = repository.create_invitation(
        conn, actor_id=owner, project_id=project, email="v@example.com", role="viewer", now=T0
    )
    assert re.fullmatch(r"inv_[0-9a-f]{24}", invitation)
    key_id, _ = repository.create_api_key(conn, actor_id=owner, project_id=project, name="prod", now=T0)
    assert re.fullmatch(r"key_[0-9a-f]{24}", key_id)


def test_project_creation_makes_creator_owner_with_defaults(conn, owner, project):
    assert repository.get_role(conn, project, owner) == "owner"
    row = conn.execute("SELECT * FROM project WHERE id = ?", (project,)).fetchone()
    assert row["enforcement"] == "observe"
    assert row["ai_enabled"] == 1
    assert row["retention_days"] == 90
    assert row["created_at"] == "2026-10-26T12:00:00Z"
    actions = [r["action"] for r in audit_rows(conn, project)]
    assert actions == ["project.create"]


def test_enforcement_must_be_valid(conn, owner):
    with pytest.raises(ValueError):
        repository.create_project(conn, creator_id=owner, name="A", enforcement="maybe", now=T0)


def test_upsert_person_is_keyed_by_google_sub(conn):
    first = repository.upsert_person(conn, google_sub="s1", email="a@example.com", name="A", now=T0)
    later = T0 + timedelta(days=1)
    second = repository.upsert_person(conn, google_sub="s1", email="new@example.com", name="A", now=later)
    assert first == second
    row = conn.execute("SELECT email, last_login_at FROM person WHERE id = ?", (first,)).fetchone()
    assert row["email"] == "new@example.com"
    assert row["last_login_at"] == "2026-10-27T12:00:00Z"


# --- last owner rule ---


def test_cannot_remove_last_owner(conn, owner, project):
    with pytest.raises(LastOwnerError):
        repository.remove_member(conn, actor_id=owner, project_id=project, person_id=owner, now=T0)
    assert repository.get_role(conn, project, owner) == "owner"


def test_cannot_demote_last_owner(conn, owner, project):
    with pytest.raises(LastOwnerError):
        repository.set_member_role(conn, actor_id=owner, project_id=project, person_id=owner, role="admin", now=T0)
    assert repository.get_role(conn, project, owner) == "owner"


def test_owner_can_leave_when_another_owner_exists(conn, owner, project, add_member):
    second = add_member(project, owner, "sam", "owner")
    repository.remove_member(conn, actor_id=second, project_id=project, person_id=owner, now=T0)
    assert repository.get_role(conn, project, owner) is None
    with pytest.raises(LastOwnerError):
        repository.set_member_role(conn, actor_id=second, project_id=project, person_id=second, role="viewer", now=T0)


# --- role checks on member management ---


def test_admin_cannot_invite_or_touch_owners(conn, owner, project, add_member):
    admin = add_member(project, owner, "ada", "admin")
    with pytest.raises(PermissionDenied):
        repository.create_invitation(conn, actor_id=admin, project_id=project, email="x@example.com", role="owner", now=T0)
    with pytest.raises(PermissionDenied):
        repository.remove_member(conn, actor_id=admin, project_id=project, person_id=owner, now=T0)
    with pytest.raises(PermissionDenied):
        repository.set_member_role(conn, actor_id=admin, project_id=project, person_id=admin, role="owner", now=T0)


def test_admin_manages_viewers(conn, owner, project, add_member):
    admin = add_member(project, owner, "ada", "admin")
    viewer = add_member(project, admin, "vic", "viewer")
    repository.set_member_role(conn, actor_id=admin, project_id=project, person_id=viewer, role="admin", now=T0)
    assert repository.get_role(conn, project, viewer) == "admin"
    repository.remove_member(conn, actor_id=admin, project_id=project, person_id=viewer, now=T0)
    assert repository.get_role(conn, project, viewer) is None


def test_viewer_and_non_member_cannot_change_state(conn, owner, project, make_person, add_member):
    viewer = add_member(project, owner, "vic", "viewer")
    stranger = make_person("eve")
    for actor in (viewer, stranger):
        with pytest.raises(PermissionDenied):
            repository.create_invitation(conn, actor_id=actor, project_id=project, email="x@example.com", role="viewer", now=T0)
        with pytest.raises(PermissionDenied):
            repository.create_api_key(conn, actor_id=actor, project_id=project, name="k", now=T0)


# --- audit in the same transaction ---


def test_change_writes_audit_with_actor(conn, owner, project, add_member):
    viewer = add_member(project, owner, "vic", "viewer")
    repository.remove_member(conn, actor_id=owner, project_id=project, person_id=viewer, now=T0)
    last = audit_rows(conn, project)[-1]
    assert last["action"] == "member.remove"
    assert last["actor_person_id"] == owner
    assert last["actor_kind"] == "person"
    assert last["target_type"] == "person"
    assert last["target_id"] == viewer
    assert last["ts"] == "2026-10-26T12:00:00Z"


def test_failed_audit_rolls_back_the_change(conn, owner, project, monkeypatch, add_member):
    viewer = add_member(project, owner, "vic", "viewer")
    before = len(audit_rows(conn, project))

    def broken_audit(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(repository, "record_audit", broken_audit)
    with pytest.raises(RuntimeError):
        repository.remove_member(conn, actor_id=owner, project_id=project, person_id=viewer, now=T0)
    with pytest.raises(RuntimeError):
        repository.create_api_key(conn, actor_id=owner, project_id=project, name="k", now=T0)

    assert repository.get_role(conn, project, viewer) == "viewer"
    assert conn.execute("SELECT COUNT(*) FROM api_key").fetchone()[0] == 0
    assert len(audit_rows(conn, project)) == before


def test_failed_project_creation_leaves_no_rows(conn, owner, monkeypatch):
    monkeypatch.setattr(repository, "record_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(RuntimeError):
        repository.create_project(conn, creator_id=owner, name="A", enforcement="observe", now=T0)
    assert conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM membership").fetchone()[0] == 0


def test_audit_hashes_user_targets(conn, owner, project):
    repository.record_audit(
        conn, project_id=project, actor_person_id=owner, actor_kind="person",
        action="decision.mark_normal", target_type="user", target_id="alice@shop.com", now=T0,
    )
    last = audit_rows(conn, project)[-1]
    assert last["target_id"] != "alice@shop.com"
    assert last["target_id"] == repository.hash_user_id(project, "alice@shop.com")
    assert last["target_id"] == hashlib.sha256(f"{project}:alice@shop.com".encode()).hexdigest()


# --- invitations ---


def test_invitation_accepted_before_expiry(conn, owner, project, make_person):
    invitation = repository.create_invitation(
        conn, actor_id=owner, project_id=project, email="New@Example.com", role="admin", now=T0
    )
    person = make_person("new")
    accepted = repository.accept_pending_invitations(
        conn, person_id=person, verified_email="new@example.com", now=T0 + timedelta(days=6)
    )
    assert accepted == [invitation]
    assert repository.get_role(conn, project, person) == "admin"
    assert audit_rows(conn, project)[-1]["action"] == "invitation.accept"
    # Accepting is one-shot.
    assert repository.accept_pending_invitations(conn, person_id=person, verified_email="new@example.com", now=T0) == []


def test_invitation_expires(conn, owner, project, make_person):
    repository.create_invitation(conn, actor_id=owner, project_id=project, email="late@example.com", role="viewer", now=T0)
    person = make_person("late")
    expired_at = T0 + repository.INVITATION_TTL
    assert repository.accept_pending_invitations(conn, person_id=person, verified_email="late@example.com", now=expired_at) == []
    assert repository.get_role(conn, project, person) is None


def test_revoked_invitation_cannot_be_accepted(conn, owner, project, make_person):
    invitation = repository.create_invitation(conn, actor_id=owner, project_id=project, email="r@example.com", role="viewer", now=T0)
    repository.revoke_invitation(conn, actor_id=owner, project_id=project, invitation_id=invitation, now=T0)
    person = make_person("r")
    assert repository.accept_pending_invitations(conn, person_id=person, verified_email="r@example.com", now=T0) == []
    with pytest.raises(InvitationUnavailable):
        repository.revoke_invitation(conn, actor_id=owner, project_id=project, invitation_id=invitation, now=T0)


def test_invitation_does_not_downgrade_existing_member(conn, owner, project):
    repository.create_invitation(conn, actor_id=owner, project_id=project, email="olga@example.com", role="viewer", now=T0)
    assert repository.accept_pending_invitations(conn, person_id=owner, verified_email="olga@example.com", now=T0) == []
    assert repository.get_role(conn, project, owner) == "owner"


# --- api keys ---


def test_api_key_stores_only_hash_and_prefix(conn, owner, project):
    key_id, secret = repository.create_api_key(conn, actor_id=owner, project_id=project, name="prod", now=T0)
    row = conn.execute("SELECT * FROM api_key WHERE id = ?", (key_id,)).fetchone()
    assert secret not in tuple(str(value) for value in row)
    assert row["key_hash"] == hashlib.sha256(secret.encode()).hexdigest()
    assert secret.startswith(row["prefix"])
    assert row["scope"] == "sdk"
    assert row["created_by"] == owner
    assert row["last_used_at"] is None and row["revoked_at"] is None
    assert audit_rows(conn, project)[-1]["action"] == "api_key.create"


def test_revoke_api_key(conn, owner, project):
    key_id, _ = repository.create_api_key(conn, actor_id=owner, project_id=project, name="prod", now=T0)
    repository.revoke_api_key(conn, actor_id=owner, project_id=project, key_id=key_id, now=T0)
    row = conn.execute("SELECT revoked_at FROM api_key WHERE id = ?", (key_id,)).fetchone()
    assert row["revoked_at"] == "2026-10-26T12:00:00Z"
    assert audit_rows(conn, project)[-1]["action"] == "api_key.revoke"


# --- transaction boundaries ---


def test_change_outside_a_transaction_is_committed(conn, owner, tmp_path):
    project_id = repository.create_project(conn, creator_id=owner, name="A", enforcement="enforce", now=T0)
    other = sqlite3.connect(tmp_path / "control.db")
    try:
        assert other.execute("SELECT enforcement FROM project WHERE id = ?", (project_id,)).fetchone() == ("enforce",)
    finally:
        other.close()


def test_change_joins_the_callers_transaction(conn, owner):
    conn.execute("BEGIN")
    repository.create_project(conn, creator_id=owner, name="A", enforcement="observe", now=T0)
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
