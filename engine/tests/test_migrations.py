import shutil
import sqlite3
from pathlib import Path

import pytest
from conftest import make_event, make_settings, as_panel
from fastapi.testclient import TestClient

from veridian_engine import db
from veridian_engine.api import create_app

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


# --- 0002: client_id -> project_id ------------------------------------------------------------

DEMO_TOKEN = "tok_demo"
DEMO_AUTH = {"Authorization": f"Bearer {DEMO_TOKEN}"}

# Representative demo rows in the legacy column order (client_id where it was).
LEGACY_ROWS = {
    "events": [
        ("demo", "e1", "2026-10-26T10:00:00.000000Z", "u_42", "s_owner", "190.202.14.7", "Chrome", "d_9f3", "GET", "/invoices", 200, 512),
        ("demo", "e2", "2026-10-26T11:00:00.000000Z", "u_42", "s_att", "203.0.113.9", "curl", "", "GET", "/export", 200, 9000),
        ("demo", "e3", "2026-10-26T11:01:00.000000Z", "u_42", "s_att", "203.0.113.9", "curl", "", "GET", "/export", 403, 0),
        ("other", "e1", "2026-10-26T09:00:00.000000Z", "u_1", "s_x", "198.51.100.1", "Firefox", "d_1", "GET", "/", 200, 10),
    ],
    "decisions": [
        (3, "demo", "d_3", "2026-10-26T11:00:05Z", "u_42", "s_att", "203.0.113.9", "block", '{"new_ip": true}', "New IP and device.", 0),
        (5, "demo", "d_5", "2026-10-26T10:00:05Z", "u_42", "s_owner", "", "alert", '{"odd_hour": true}', "Odd hour.", 1),
        (6, "other", "d_6", "2026-10-26T09:00:05Z", "u_1", "s_x", "198.51.100.1", "block", "{}", "Other client.", 0),
    ],
    "decision_events": [("demo", "d_3", "e2"), ("demo", "d_5", "e1"), ("other", "d_6", "e1")],
    "rate_spike_judgments": [("demo", "s_att")],
    "ai_judgments": [("demo", "e2", "2026-10-26T11:00:00.000000Z")],
    "blocklist_versions": [("demo", 7), ("other", 2)],
}
# Decisions up to d_12 were issued once and later reset: new ids must not reuse them.
LEGACY_DECISION_SEQUENCE = 12


def populated_legacy_db(path: Path) -> Path:
    legacy_db(path)
    conn = sqlite3.connect(path)
    for table, table_rows in LEGACY_ROWS.items():
        marks = ", ".join("?" for _ in table_rows[0])
        conn.executemany(f"INSERT INTO {table} VALUES ({marks})", table_rows)
    conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'decisions'", (LEGACY_DECISION_SEQUENCE,))
    conn.commit()
    conn.close()
    return path


def rows(path: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return sorted(conn.execute(f"SELECT * FROM {table}").fetchall())
    finally:
        conn.close()


def columns(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_0002_keeps_every_legacy_row_under_project_id(tmp_path):
    path = populated_legacy_db(tmp_path / "veridian.db")
    expected = {table: rows(path, table) for table in DATA_TABLES}

    db.init_db(str(path))

    assert applied_versions(path) == all_versions()
    assert 2 in all_versions()
    for table in DATA_TABLES:
        assert "client_id" not in columns(path, table)
        assert "project_id" in columns(path, table)
        assert rows(path, table) == expected[table]
    backup = tmp_path / "veridian.db.pre-0002"
    assert rows(backup, "blocklist_versions") == [("demo", 7), ("other", 2)]
    assert "client_id" in columns(backup, "events")


def test_0002_keeps_the_indexes_on_project_id(tmp_path):
    path = tmp_path / "veridian.db"
    db.init_db(str(path))
    conn = sqlite3.connect(path)
    names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")]
    indexes = {name: [info[2] for info in conn.execute(f"PRAGMA index_info({name})")] for name in names}
    conn.close()

    assert indexes["events_by_user"] == ["project_id", "user_id", "ts"]
    assert indexes["events_by_session"] == ["project_id", "session_id", "ts"]
    assert indexes["one_block_per_session"] == ["project_id", "session_id"]
    assert indexes["ai_judgments_by_ts"] == ["project_id", "ts"]


DECISION_INSERT = (
    "INSERT INTO decisions (project_id, id, ts, user_id, session_id, ip, decision, signals, rationale)"
    " VALUES (?, ?, 't', 'u', ?, '', ?, '{}', '')"
)


def test_0002_makes_decision_ids_unique_per_project(tmp_path):
    path = tmp_path / "veridian.db"
    db.init_db(str(path))
    conn = sqlite3.connect(path)
    conn.execute(DECISION_INSERT, ("p_a", "d_1", "s_1", "alert"))
    conn.execute(DECISION_INSERT, ("p_b", "d_1", "s_1", "alert"))

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(DECISION_INSERT, ("p_a", "d_1", "s_2", "alert"))
    conn.close()


def test_0002_keeps_one_block_per_session(tmp_path):
    path = tmp_path / "veridian.db"
    db.init_db(str(path))
    conn = sqlite3.connect(path)
    conn.execute(DECISION_INSERT, ("p_a", "d_1", "s_1", "block"))

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(DECISION_INSERT, ("p_a", "d_2", "s_1", "block"))
    conn.close()


def test_0002_keeps_the_decision_sequence(tmp_path):
    path = populated_legacy_db(tmp_path / "veridian.db")

    db.init_db(str(path))

    conn = sqlite3.connect(path)
    cursor = conn.execute(DECISION_INSERT, ("demo", "pending", "s_new", "alert"))
    conn.close()
    assert cursor.lastrowid == LEGACY_DECISION_SEQUENCE + 1


def migrations_up_to(tmp_path: Path, last: int) -> Path:
    directory = tmp_path / f"migrations_up_to_{last}"
    directory.mkdir()
    for source in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        if int(source.name[:4]) <= last:
            shutil.copy(source, directory / source.name)
    return directory


def test_0006_drops_the_unused_project_webhook_columns(tmp_path):
    path = tmp_path / "veridian.db"
    db.init_db(str(path))
    assert not {"webhook_url", "webhook_secret_enc"} & set(columns(path, "project"))
    assert "enforced" in columns(path, "notifications")


def test_0006_keeps_projects_and_marks_queued_notifications_enforced(tmp_path):
    path = tmp_path / "veridian.db"
    db.init_db(str(path), migrations_dir=migrations_up_to(tmp_path, 5))
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO organization (id, name, created_at) VALUES ('org_1', 'Org', '2026-10-26T00:00:00Z')")
    conn.execute("INSERT INTO project (id, org_id, name, enforcement, created_at, webhook_url) VALUES"
                 " ('prj_1', 'org_1', 'App', 'enforce', '2026-10-26T00:00:00Z', NULL)")
    conn.execute("INSERT INTO notifications (id, project_id, kind, decision_id, status, created_at, updated_at)"
                 " VALUES ('ntf_1', 'prj_1', 'webhook', 'd_1', 'pending', 'x', 'x')")
    conn.commit()
    conn.close()

    db.init_db(str(path))

    conn = sqlite3.connect(path)
    assert conn.execute("SELECT id, name FROM project WHERE id = 'prj_1'").fetchone() == ("prj_1", "App")
    assert conn.execute("SELECT enforced FROM notifications WHERE id = 'ntf_1'").fetchone() == (1,)
    conn.close()


def test_sqlite_supports_drop_column():
    # 0006 uses ALTER TABLE ... DROP COLUMN, available since SQLite 3.35.
    assert sqlite3.sqlite_version_info >= (3, 35, 0)


def test_sqlite_supports_rename_column():
    # 0002 uses ALTER TABLE ... RENAME COLUMN, available since SQLite 3.25.
    assert sqlite3.sqlite_version_info >= (3, 25, 0)


def fresh_db_with_same_rows(path: Path) -> Path:
    """The same rows written into a database built from migrations, for comparison."""
    db.init_db(str(path))
    conn = sqlite3.connect(path)
    for table, table_rows in LEGACY_ROWS.items():
        names = ", ".join(columns(path, table))
        marks = ", ".join("?" for _ in table_rows[0])
        conn.executemany(f"INSERT INTO {table} ({names}) VALUES ({marks})", table_rows)
    conn.commit()
    conn.close()
    return path


def demo_client(path: Path, tmp_path: Path, clock) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path, clock, db_path=str(path), tokens={DEMO_TOKEN: "demo"})))


def read_all(path: Path, tmp_path: Path, clock) -> dict:
    endpoints = (
        "/v1/blocklist",
        "/v1/decisions",
        "/v1/activity",
        "/v1/summary?since=2026-10-26T00:00:00Z&until=2026-10-27T00:00:00Z",
        "/v1/users",
        "/v1/profile/u_42",
    )
    with demo_client(path, tmp_path, clock) as client:
        return {endpoint: client.get(endpoint, headers=as_panel(client, DEMO_AUTH)).json() for endpoint in endpoints}


def test_migrated_demo_database_serves_the_same_responses(tmp_path, clock):
    migrated = populated_legacy_db(tmp_path / "migrated.db")
    fresh = fresh_db_with_same_rows(tmp_path / "fresh.db")

    responses = read_all(migrated, tmp_path, clock)

    assert responses == read_all(fresh, tmp_path, clock)
    assert responses["/v1/blocklist"] == {"version": 7, "users": [], "sessions": ["s_att"], "ips": ["203.0.113.9"]}
    decisions = responses["/v1/decisions"]["decisions"]
    assert [(d["id"], d["status"], d["event_ids"]) for d in decisions] == [
        ("d_5", "marked_normal", ["e1"]),
        ("d_3", "open", ["e2"]),
    ]
    activity = responses["/v1/activity"]["activity"]
    assert [(a["event_id"], a["verdict"]) for a in activity] == [("e3", "block"), ("e2", "block"), ("e1", "normal")]


def test_migrated_demo_database_keeps_blocking(tmp_path, clock):
    path = populated_legacy_db(tmp_path / "veridian.db")

    with demo_client(path, tmp_path, clock) as client:
        unchanged = client.get("/v1/blocklist?since=7", headers=DEMO_AUTH)
        verdict = client.post(
            "/v1/check",
            json={"event": make_event("e4", clock(), session_id="s_att", ip="203.0.113.9")},
            headers=DEMO_AUTH,
        )

    assert unchanged.status_code == 304
    assert verdict.status_code == 200
    assert verdict.json() == {"verdict": "block", "decision_id": "d_3"}
