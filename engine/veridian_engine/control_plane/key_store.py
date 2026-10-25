"""Storage side of API key authentication: lookup by hash and last-use bookkeeping."""

import sqlite3

from ..keys import StoredKey


def find_by_hash(conn: sqlite3.Connection, key_hash: str) -> StoredKey | None:
    row = conn.execute(
        "SELECT k.id, k.project_id, k.key_hash, k.revoked_at, p.deleted_at"
        " FROM api_key k JOIN project p ON p.id = k.project_id WHERE k.key_hash = ?",
        (key_hash,),
    ).fetchone()
    if row is None:
        return None
    return StoredKey(id=row[0], project_id=row[1], key_hash=row[2], revoked_at=row[3], project_deleted_at=row[4])


def touch_last_used(conn: sqlite3.Connection, key_id: str, used_at: str) -> None:
    conn.execute("UPDATE api_key SET last_used_at = ? WHERE id = ?", (used_at, key_id))
