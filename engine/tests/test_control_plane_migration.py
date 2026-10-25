import sqlite3
from datetime import datetime, timezone

import pytest
from conftest import TOKEN_A, TOKEN_B, make_settings
from fastapi.testclient import TestClient
from test_migrations import applied_versions, populated_legacy_db, table_names

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.control_plane import repository
from veridian_engine.keys import LEGACY_CREATED_BY, LEGACY_PREFIX, hash_key

CONTROL_TABLES = {"person", "project", "membership", "invitation", "api_key", "audit_log"}


def query(path, sql, params=()):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def start(tmp_path, clock, **overrides):
    """Run the engine startup (migrations + seeding) once and stop."""
    with TestClient(create_app(make_settings(tmp_path, clock, **overrides))):
        pass


def test_0003_creates_the_control_plane_on_a_new_database(tmp_path):
    path = tmp_path / "veridian.db"

    db.init_db(str(path))

    assert 3 in applied_versions(path)
    assert CONTROL_TABLES <= table_names(path)


def test_0003_upgrades_a_legacy_database_and_keeps_its_rows(tmp_path):
    path = populated_legacy_db(tmp_path / "veridian.db")

    db.init_db(str(path))

    assert 3 in applied_versions(path)
    assert CONTROL_TABLES <= table_names(path)
    assert len(query(path, "SELECT * FROM events")) == 4


def test_db_connections_enforce_foreign_keys(tmp_path):
    path = tmp_path / "veridian.db"
    db.init_db(str(path))

    with pytest.raises(sqlite3.IntegrityError):
        with db.connect(str(path)) as conn:
            conn.execute(
                "INSERT INTO api_key (id, project_id, name, prefix, key_hash, scope, created_by, created_at)"
                " VALUES ('k1', 'missing', 'n', 'p', 'h', 'sdk', 'x', '2026-10-26T12:00:00Z')"
            )


def test_startup_creates_a_project_per_existing_id_and_token(tmp_path, clock):
    path = populated_legacy_db(tmp_path / "veridian.db")

    start(tmp_path, clock, db_path=str(path), tokens={"tok_demo": "demo", "tok_new": "fresh"})

    projects = query(path, "SELECT id, name, enforcement, deleted_at FROM project ORDER BY id")
    assert projects == [
        ("demo", "demo", "enforce", None),
        ("fresh", "fresh", "enforce", None),
        ("other", "other", "enforce", None),
    ]


def test_startup_imports_tokens_as_hashed_legacy_keys(tmp_path, clock):
    path = tmp_path / "veridian.db"

    start(tmp_path, clock, db_path=str(path))

    keys = query(path, "SELECT project_id, prefix, key_hash, scope, created_by, revoked_at FROM api_key ORDER BY project_id")
    assert keys == [
        ("client_a", LEGACY_PREFIX, hash_key(TOKEN_A), "sdk", LEGACY_CREATED_BY, None),
        ("client_b", LEGACY_PREFIX, hash_key(TOKEN_B), "sdk", LEGACY_CREATED_BY, None),
    ]
    stored = " ".join(str(value) for row in query(path, "SELECT * FROM api_key") for value in row)
    assert TOKEN_A not in stored.split()


def test_startup_seeding_is_idempotent(tmp_path, clock):
    path = populated_legacy_db(tmp_path / "veridian.db")
    tokens = {"tok_demo": "demo"}

    start(tmp_path, clock, db_path=str(path), tokens=tokens)
    first = (query(path, "SELECT * FROM project ORDER BY id"), query(path, "SELECT * FROM api_key ORDER BY id"))
    start(tmp_path, clock, db_path=str(path), tokens=tokens)

    assert (query(path, "SELECT * FROM project ORDER BY id"), query(path, "SELECT * FROM api_key ORDER BY id")) == first


def test_startup_does_not_restore_a_revoked_legacy_key(tmp_path, clock):
    path = tmp_path / "veridian.db"
    start(tmp_path, clock, db_path=str(path))
    conn = sqlite3.connect(path)
    conn.execute("UPDATE api_key SET revoked_at = '2026-10-26T12:00:00Z' WHERE key_hash = ?", (hash_key(TOKEN_A),))
    conn.commit()
    conn.close()

    start(tmp_path, clock, db_path=str(path))

    assert query(path, "SELECT revoked_at FROM api_key WHERE key_hash = ?", (hash_key(TOKEN_A),)) == [
        ("2026-10-26T12:00:00Z",)
    ]


# --- 0004: organizations above projects; roles, invitations and audit move to the org ---


def migrations_up_to(tmp_path, version: int):
    """A directory holding only the real migrations up to `version`."""
    directory = tmp_path / f"migrations_upto_{version}"
    directory.mkdir()
    for migration in db.load_migrations(db.MIGRATIONS_DIR):
        if migration.version <= version:
            (directory / migration.filename).write_text(migration.sql, encoding="utf-8")
    return directory


def control_plane_0003_db(tmp_path):
    """A database at 0003 with the demo data, two projects, per-project roles, an invitation,
    a key and an audit row, as the engine left it before organizations existed."""
    path = populated_legacy_db(tmp_path / "veridian.db")
    db.init_db(str(path), migrations_up_to(tmp_path, 3))
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        INSERT INTO project (id, name, enforcement, created_at) VALUES ('demo', 'demo', 'enforce', '2026-10-20T00:00:00Z');
        INSERT INTO project (id, name, enforcement, created_at) VALUES ('other', 'other', 'enforce', '2026-10-21T00:00:00Z');
        INSERT INTO person (id, google_sub, email, name, created_at, last_login_at)
            VALUES ('per_a', 'sub-a', 'a@example.com', 'A', '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z');
        INSERT INTO person (id, google_sub, email, name, created_at, last_login_at)
            VALUES ('per_b', 'sub-b', 'b@example.com', 'B', '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z');
        INSERT INTO membership (project_id, person_id, role, created_at) VALUES ('demo', 'per_a', 'viewer', '2026-10-20T00:00:00Z');
        INSERT INTO membership (project_id, person_id, role, created_at) VALUES ('other', 'per_a', 'owner', '2026-10-21T00:00:00Z');
        INSERT INTO membership (project_id, person_id, role, created_at) VALUES ('demo', 'per_b', 'admin', '2026-10-20T00:00:00Z');
        INSERT INTO invitation (id, project_id, email, role, invited_by, created_at, expires_at)
            VALUES ('inv_1', 'demo', 'c@example.com', 'viewer', 'per_a', '2026-10-20T00:00:00Z', '2999-01-01T00:00:00Z');
        INSERT INTO audit_log (seq, project_id, actor_person_id, actor_kind, action, target_type, target_id, meta, ts)
            VALUES (1, 'demo', 'per_a', 'person', 'project.create', 'project', 'demo', '{}', '2026-10-20T00:00:00Z');
        """
    )
    conn.execute(
        "INSERT INTO api_key (id, project_id, name, prefix, key_hash, scope, created_by, created_at)"
        " VALUES ('key_demo', 'demo', 'legacy', 'legacy', ?, 'sdk', 'migration', '2026-10-20T00:00:00Z')",
        (hash_key("tok_demo"),),
    )
    conn.commit()
    conn.close()
    return path


def test_0004_creates_organizations_on_a_new_database(tmp_path):
    path = tmp_path / "veridian.db"

    db.init_db(str(path))

    assert 4 in applied_versions(path)
    assert "organization" in table_names(path)
    assert query(path, "SELECT COUNT(*) FROM organization") == [(0,)]


def test_0004_puts_existing_projects_in_org_demo(tmp_path):
    path = control_plane_0003_db(tmp_path)

    db.init_db(str(path))

    assert query(path, "SELECT id, name, deleted_at FROM organization") == [("org_demo", "Demo", None)]
    assert query(path, "SELECT id, org_id FROM project ORDER BY id") == [("demo", "org_demo"), ("other", "org_demo")]


def test_0004_moves_roles_to_the_org_keeping_the_strongest_one(tmp_path):
    path = control_plane_0003_db(tmp_path)

    db.init_db(str(path))

    assert query(path, "SELECT org_id, person_id, role FROM membership ORDER BY person_id") == [
        ("org_demo", "per_a", "owner"),
        ("org_demo", "per_b", "admin"),
    ]


def test_0004_moves_invitations_and_audit_to_the_org(tmp_path):
    path = control_plane_0003_db(tmp_path)

    db.init_db(str(path))

    assert query(path, "SELECT id, org_id, email, role FROM invitation") == [("inv_1", "org_demo", "c@example.com", "viewer")]
    assert query(path, "SELECT seq, org_id, project_id, action FROM audit_log") == [(1, "org_demo", "demo", "project.create")]


def test_0004_keeps_the_data_and_the_legacy_key_working(tmp_path, clock):
    path = control_plane_0003_db(tmp_path)

    with TestClient(create_app(make_settings(tmp_path, clock, db_path=str(path), tokens={"tok_demo": "demo"}))) as client:
        listed = client.get("/v1/blocklist", headers={"Authorization": "Bearer tok_demo"})

    assert listed.status_code == 200
    assert listed.json()["sessions"] == ["s_att"]
    assert len(query(path, "SELECT * FROM events")) == 4


def test_startup_seeds_legacy_projects_into_org_demo(tmp_path, clock):
    path = tmp_path / "veridian.db"

    start(tmp_path, clock, db_path=str(path))
    start(tmp_path, clock, db_path=str(path))

    assert query(path, "SELECT id, name FROM organization") == [("org_demo", "Demo")]
    assert query(path, "SELECT id, org_id FROM project ORDER BY id") == [("client_a", "org_demo"), ("client_b", "org_demo")]


# --- 0011: provider identities, so one person can sign in with Google and GitHub ---


def test_0011_backfills_identities_and_lowercases_emails(tmp_path):
    path = tmp_path / "veridian.db"
    db.init_db(str(path), migrations_up_to(tmp_path, 10))
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO person (id, google_sub, email, name, created_at, last_login_at)"
                 " VALUES ('per_old', 'sub-old', 'Old@Example.com', 'Old', '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z')")
    conn.commit()
    conn.close()

    db.init_db(str(path))

    assert query(path, "SELECT sub, person_id FROM person_identity") == [("sub-old", "per_old")]
    assert query(path, "SELECT email FROM person WHERE id = 'per_old'") == [("old@example.com",)]


def test_0011_existing_person_keeps_its_id_on_the_next_login(tmp_path):
    path = tmp_path / "veridian.db"
    db.init_db(str(path), migrations_up_to(tmp_path, 10))
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO person (id, google_sub, email, name, created_at, last_login_at)"
                 " VALUES ('per_old', 'sub-old', 'old@example.com', 'Old', '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z')")
    conn.commit()
    conn.close()
    db.init_db(str(path))

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    same = repository.upsert_person(conn, google_sub="sub-old", email="old@example.com", name=None,
                                    now=datetime(2026, 10, 27, tzinfo=timezone.utc))
    joined = repository.upsert_person(conn, google_sub="github:1", email="OLD@example.com", name=None,
                                      now=datetime(2026, 10, 27, tzinfo=timezone.utc))
    conn.close()
    assert same == joined == "per_old"
