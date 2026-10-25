import sqlite3

import pytest
from conftest import TOKEN_A, TOKEN_B, make_settings
from fastapi.testclient import TestClient
from test_migrations import applied_versions, populated_legacy_db, table_names

from veridian_engine import db
from veridian_engine.api import create_app
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

    assert applied_versions(path)[-1] == 3
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
