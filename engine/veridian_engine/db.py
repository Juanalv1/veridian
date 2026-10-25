import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_FILE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

TRACKING_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    sql: str


def load_migrations(directory: Path) -> list[Migration]:
    """Numbered NNNN_name.sql files, in version order."""
    migrations: dict[int, Migration] = {}
    for file in sorted(Path(directory).iterdir()):
        match = MIGRATION_FILE.match(file.name)
        if not match:
            continue
        version = int(match.group(1))
        if version in migrations:
            raise ValueError(f"duplicate migration version {version:04d}: {file.name}")
        migrations[version] = Migration(version, file.name, file.read_text(encoding="utf-8"))
    return [migrations[version] for version in sorted(migrations)]


def init_db(path: str, migrations_dir: Path | None = None) -> None:
    """Bring the database up to the latest migration before the engine takes traffic.

    A database created before migrations existed (it has `events` but no `schema_migrations`)
    already holds the 0001 schema: it is marked as 0001 without running it. Before applying
    anything to a database with tables, it is copied to `<path>.pre-NNNN` (NNNN = the first
    pending version): there are no down migrations, that copy is the way back. Each migration
    runs in its own transaction, so a failing one leaves the database as it was.
    """
    migrations = load_migrations(migrations_dir or MIGRATIONS_DIR)
    conn = sqlite3.connect(path, isolation_level=None)  # transactions are explicit below
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "schema_migrations" in tables:
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        elif "events" in tables:
            applied = {1}
        else:
            applied = set()
        pending = [migration for migration in migrations if migration.version not in applied]
        if "schema_migrations" in tables and not pending:
            return
        if tables and pending:
            backup(conn, f"{path}.pre-{pending[0].version:04d}")

        if "schema_migrations" not in tables:
            run_in_transaction(conn, lambda: mark_applied(conn, applied))
        for migration in pending:
            run_in_transaction(conn, lambda m=migration: apply(conn, m))
    finally:
        conn.close()


def backup(conn: sqlite3.Connection, backup_path: str) -> None:
    target = sqlite3.connect(backup_path)
    try:
        conn.backup(target)
    finally:
        target.close()


def run_in_transaction(conn: sqlite3.Connection, work) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        work()
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def mark_applied(conn: sqlite3.Connection, versions: set[int]) -> None:
    conn.execute(TRACKING_SCHEMA)
    for version in sorted(versions):
        record(conn, version)


def apply(conn: sqlite3.Connection, migration: Migration) -> None:
    # executescript would commit the open transaction first, so statements run one by one.
    for statement in split_statements(migration.sql):
        conn.execute(statement)
    record(conn, migration.version)


def record(conn: sqlite3.Connection, version: int) -> None:
    applied_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?) ON CONFLICT (version) DO NOTHING",
        (version, applied_at),
    )


def split_statements(sql: str) -> list[str]:
    """Split a migration script into complete statements, as sqlite3 itself sees them."""
    statements, current = [], ""
    for line in sql.splitlines(keepends=True):
        current += line
        if sqlite3.complete_statement(current):
            if strip_comments(current):
                statements.append(current)
            current = ""
    if strip_comments(current):
        raise ValueError("migration ends with an incomplete statement")
    return statements


def strip_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--")).strip()


@contextmanager
def connect(path: str) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Only control-plane tables declare foreign keys; the data tables have none (see 0003).
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
