"""A project's webhook endpoint (contract 13). No HTTP routes yet: the panel endpoints call these.

The URL passes the SSRF guard before anything is stored. The signing secret is generated
here, returned once to the caller, and stored only encrypted (`VERIDIAN_SECRET_KEY`).
Without that key `set_webhook_endpoint` raises `SecretKeyMissing`; the engine keeps running.
Functions never commit: the caller's transaction (db.connect) does.
"""

import sqlite3
from datetime import datetime

from .notifications.outbox import format_ts
from .notifications.secret_box import encrypt_secret, generate_secret
from .notifications.webhook import Resolver, check_url, default_resolver


def set_webhook_endpoint(
    conn: sqlite3.Connection,
    project_id: str,
    url: str,
    *,
    now: datetime,
    resolver: Resolver = default_resolver,
) -> str:
    """Create or replace the endpoint, enabled, with a new secret. Return the plain secret.

    Raises `UnsafeUrlError` for an unsafe URL and `SecretEncryptionError` (missing key or
    dependency) before writing anything.
    """
    check_url(url, resolver)
    secret = generate_secret()
    secret_enc = encrypt_secret(secret)
    conn.execute(
        "INSERT INTO webhook_endpoint (project_id, url, secret_enc, enabled, created_at) VALUES (?, ?, ?, 1, ?)"
        " ON CONFLICT (project_id) DO UPDATE SET url = excluded.url, secret_enc = excluded.secret_enc,"
        " enabled = 1, created_at = excluded.created_at",
        (project_id, url, secret_enc, format_ts(now)),
    )
    return secret


def clear_webhook_endpoint(conn: sqlite3.Connection, project_id: str) -> bool:
    """Remove the endpoint. Pending notifications then fail without being sent."""
    cursor = conn.execute("DELETE FROM webhook_endpoint WHERE project_id = ?", (project_id,))
    return cursor.rowcount > 0


def set_webhook_enabled(conn: sqlite3.Connection, project_id: str, enabled: bool) -> bool:
    cursor = conn.execute(
        "UPDATE webhook_endpoint SET enabled = ? WHERE project_id = ?", (int(enabled), project_id)
    )
    return cursor.rowcount > 0


def get_webhook_endpoint(conn: sqlite3.Connection, project_id: str) -> dict | None:
    """The endpoint as the panel shows it. The secret is never returned."""
    row = conn.execute(
        "SELECT url, enabled, created_at FROM webhook_endpoint WHERE project_id = ?", (project_id,)
    ).fetchone()
    if row is None:
        return None
    return {"url": row["url"], "enabled": bool(row["enabled"]), "created_at": row["created_at"]}
