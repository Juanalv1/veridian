"""Control plane: people, projects, memberships, invitations, API keys and audit."""

import sqlite3
from pathlib import Path

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create the control-plane tables. Stand-in until it ships as migration 0003."""
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
