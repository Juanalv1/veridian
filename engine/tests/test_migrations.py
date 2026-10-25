import shutil
import sqlite3
from pathlib import Path

import pytest

from veridian_engine import db

# The schema every deployed database had before numbered migrations existed, verbatim from
# the old db.SCHEMA. Kept here, not imported, so the fixture does not move with the code.
LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    client_id TEXT NOT NULL,
    id TEXT NOT NULL,
    ts TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ip TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    device_id TEXT NOT NULL,
    method TEXT NOT NULL,
    route TEXT NOT NULL,
    status INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    PRIMARY KEY (client_id, id)
);
CREATE INDEX IF NOT EXISTS events_by_user ON events (client_id, user_id, ts);
CREATE INDEX IF NOT EXISTS events_by_session ON events (client_id, session_id, ts);

CREATE TABLE IF NOT EXISTS decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL,
    id TEXT NOT NULL UNIQUE,
    ts TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ip TEXT NOT NULL,
    decision TEXT NOT NULL,
    signals TEXT NOT NULL,
    rationale TEXT NOT NULL,
    normal INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS one_block_per_session
    ON decisions (client_id, session_id) WHERE decision = 'block';

CREATE TABLE IF NOT EXISTS decision_events (
    client_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY (client_id, decision_id, event_id)
);

-- Sessions whose first rate spike was already sent to the judge: it is judged once.
CREATE TABLE IF NOT EXISTS rate_spike_judgments (
    client_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    PRIMARY KEY (client_id, session_id)
);

-- Every real call to the AI judge, by the judged event's ts (reviewed_by_ai, contract 6).
CREATE TABLE IF NOT EXISTS ai_judgments (
    client_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_judgments_by_ts ON ai_judgments (client_id, ts);

CREATE TABLE IF NOT EXISTS blocklist_versions (
    client_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
"""

DATA_TABLES = ("events", "decisions", "decision_events", "rate_spike_judgments", "ai_judgments", "blocklist_versions")


def legacy_db(path: Path) -> Path:
    """An empty database exactly as the pre-migration code left it."""
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.close()
    return path


def table_names(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def applied_versions(path: Path) -> list[int]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def dump(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def all_versions() -> list[int]:
    return [migration.version for migration in db.load_migrations(db.MIGRATIONS_DIR)]


def migrations_dir_with(tmp_path: Path, extra: dict[str, str]) -> Path:
    """The real migrations plus extra files (name -> SQL), in a directory of their own."""
    directory = tmp_path / "migrations"
    shutil.copytree(db.MIGRATIONS_DIR, directory)
    for name, sql in extra.items():
        (directory / name).write_text(sql, encoding="utf-8")
    return directory


def test_new_database_gets_every_migration_recorded(tmp_path):
    path = tmp_path / "veridian.db"

    db.init_db(str(path))

    assert applied_versions(path) == all_versions()
    assert all_versions()[0] == 1
    assert set(DATA_TABLES) <= table_names(path)
    conn = sqlite3.connect(path)
    applied_at = conn.execute("SELECT applied_at FROM schema_migrations WHERE version = 1").fetchone()[0]
    conn.close()
    assert applied_at.endswith("Z")


def test_new_database_takes_no_backup(tmp_path):
    path = tmp_path / "veridian.db"

    db.init_db(str(path))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["veridian.db"]


def test_migration_0001_is_the_legacy_schema(tmp_path):
    legacy = legacy_db(tmp_path / "legacy.db")
    migrated = tmp_path / "migrated.db"
    conn = sqlite3.connect(migrated)
    conn.executescript(db.load_migrations(db.MIGRATIONS_DIR)[0].sql)
    conn.close()

    def schema(path):
        conn = sqlite3.connect(path)
        rows = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        conn.close()
        return rows

    assert schema(migrated) == schema(legacy)


def test_existing_database_is_marked_0001_without_rerunning_it(tmp_path):
    path = legacy_db(tmp_path / "veridian.db")
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO blocklist_versions (client_id, version) VALUES ('demo', 7)")
    conn.commit()
    conn.close()
    # Only 0001 exists: the marker alone must bring the database up to date.
    directory = tmp_path / "only_0001"
    directory.mkdir()
    first = db.load_migrations(db.MIGRATIONS_DIR)[0]
    (directory / first.filename).write_text(
        first.sql + "\nINSERT INTO blocklist_versions (client_id, version) VALUES ('rerun', 1);\n", encoding="utf-8"
    )

    db.init_db(str(path), directory)

    assert applied_versions(path) == [1]
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT client_id, version FROM blocklist_versions").fetchall()
    conn.close()
    assert rows == [("demo", 7)]


def test_restart_is_idempotent(tmp_path):
    path = tmp_path / "veridian.db"
    db.init_db(str(path))
    before = dump(path)

    db.init_db(str(path))

    assert dump(path) == before
    assert applied_versions(path) == all_versions()


def test_backup_is_taken_before_pending_migrations(tmp_path):
    path = tmp_path / "veridian.db"
    directory = migrations_dir_with(tmp_path, {})
    db.init_db(str(path), directory)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO blocklist_versions VALUES ('demo', 3)")
    conn.commit()
    conn.close()
    before = dump(path)
    next_version = all_versions()[-1] + 1
    (directory / f"{next_version:04d}_add_notes.sql").write_text("CREATE TABLE notes (body TEXT);", encoding="utf-8")

    db.init_db(str(path), directory)

    backup = tmp_path / f"veridian.db.pre-{next_version:04d}"
    assert backup.exists()
    assert dump(backup) == before
    assert "notes" in table_names(path)
    assert applied_versions(path)[-1] == next_version


def test_restart_without_pending_migrations_takes_no_new_backup(tmp_path):
    path = tmp_path / "veridian.db"
    db.init_db(str(path))
    db.init_db(str(path))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["veridian.db"]


def test_broken_migration_rolls_back_and_leaves_database_unchanged(tmp_path):
    path = tmp_path / "veridian.db"
    directory = migrations_dir_with(tmp_path, {})
    db.init_db(str(path), directory)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO blocklist_versions VALUES ('demo', 3)")
    conn.commit()
    conn.close()
    before = dump(path)
    next_version = all_versions()[-1] + 1
    (directory / f"{next_version:04d}_broken.sql").write_text(
        "CREATE TABLE half_done (a TEXT);\n"
        "UPDATE blocklist_versions SET version = 99;\n"
        "INSERT INTO table_that_does_not_exist VALUES (1);\n",
        encoding="utf-8",
    )

    with pytest.raises(sqlite3.OperationalError):
        db.init_db(str(path), directory)

    assert dump(path) == before


def test_duplicate_migration_versions_are_rejected(tmp_path):
    directory = migrations_dir_with(tmp_path, {"0001_again.sql": "SELECT 1;"})

    with pytest.raises(ValueError):
        db.load_migrations(directory)
