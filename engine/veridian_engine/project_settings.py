"""Per-project switches: enforcement (decision 6) and ai_enabled (decision 10).

`observe` detects, decides and records exactly as `enforce`, but nothing is enforced:
the blocklist is published empty and the synchronous check always allows. `ai_enabled = 0`
makes the project rule-only: no judge is called for it.
"""

import sqlite3
from dataclasses import dataclass

from .decisions import bump_version

ENFORCEMENT_MODES = ("observe", "enforce")


@dataclass(frozen=True)
class ProjectFlags:
    enforce: bool = True
    ai_enabled: bool = True


def get_project_flags(conn: sqlite3.Connection, project_id: str) -> ProjectFlags:
    """Read the project's switches. A project without a row enforces and uses the AI."""
    row = conn.execute("SELECT enforcement, ai_enabled FROM project WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        return ProjectFlags()
    return ProjectFlags(enforce=row["enforcement"] == "enforce", ai_enabled=bool(row["ai_enabled"]))


def set_project_flags(
    conn: sqlite3.Connection, project_id: str, *, enforcement: str | None = None, ai_enabled: bool | None = None
) -> ProjectFlags:
    """Change the switches. A change of enforcement bumps the blocklist version, so middlewares
    polling with `since` get the newly published (or emptied) list instead of a 304."""
    if enforcement is not None and enforcement not in ENFORCEMENT_MODES:
        raise ValueError(f"enforcement must be one of {ENFORCEMENT_MODES}")
    row = conn.execute("SELECT enforcement FROM project WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise LookupError(project_id)
    if enforcement is not None and enforcement != row["enforcement"]:
        conn.execute("UPDATE project SET enforcement = ? WHERE id = ?", (enforcement, project_id))
        bump_version(conn, project_id)
    if ai_enabled is not None:
        conn.execute("UPDATE project SET ai_enabled = ? WHERE id = ?", (int(ai_enabled), project_id))
    return get_project_flags(conn, project_id)
