"""Startup seeding of the control plane from the single-client era.

Runs after the migrations, on every start, and is idempotent:

- every project id already present in the data (events, decisions) and every id in
  `Settings.tokens` gets a `project` row with `id = name = <id>` and enforcement `enforce`
  (decision 6: the migrated demo project keeps blocking);
- every `Settings.tokens` entry is imported once as an `sdk` API key (hash only, prefix
  "legacy", created_by "migration"), so the demo SDK keeps its token.

Existing rows are never changed: a revoked legacy key stays revoked and a soft-deleted
project stays deleted even if its token is still in the environment.
"""

import sqlite3
from collections.abc import Mapping
from datetime import datetime

from ..keys import legacy_import
from .repository import format_ts, new_id

LEGACY_KEY_NAME = "VERIDIAN_TOKENS import"


def seed_legacy(conn: sqlite3.Connection, tokens: Mapping[str, str], now: datetime) -> None:
    created_at = format_ts(now)
    data_ids = {
        row[0]
        for row in conn.execute("SELECT DISTINCT project_id FROM events UNION SELECT DISTINCT project_id FROM decisions")
    }
    token_ids = {project_id.strip() for project_id in tokens.values()}
    for project_id in sorted(data_ids | token_ids):
        conn.execute(
            "INSERT INTO project (id, name, enforcement, created_at) VALUES (?, ?, 'enforce', ?)"
            " ON CONFLICT (id) DO NOTHING",
            (project_id, project_id, created_at),
        )

    existing = {row[0] for row in conn.execute("SELECT key_hash FROM api_key")}
    for row in legacy_import(tokens, existing_hashes=existing):
        conn.execute(
            "INSERT INTO api_key (id, project_id, name, prefix, key_hash, scope, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'sdk', ?, ?)",
            (new_id("key"), row.project_id, LEGACY_KEY_NAME, row.prefix, row.key_hash, row.created_by, created_at),
        )
