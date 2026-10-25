import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from veridian_engine import retention
from veridian_engine.control_plane import apply_schema
from veridian_engine.control_plane.repository import hash_user_id
from veridian_engine.decisions import blocklist, bump_version, create_decision, current_version, mark_normal

# Fixtures live here, not in a conftest.py: a nested conftest would shadow the
# top-level `conftest` module that other engine tests import by name.

MIGRATIONS = Path(retention.__file__).resolve().parent / "migrations"
NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
DAYS = 30
CUTOFF = NOW - timedelta(days=DAYS)
P1 = "prj_one"
P2 = "prj_two"
OWNER = "per_owner"


def build_schema(conn: sqlite3.Connection) -> None:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
    apply_schema(conn)
    conn.execute(
        "INSERT INTO person (id, google_sub, email, name, created_at, last_login_at)"
        " VALUES (?, 'g', 'o@example.com', 'Owner', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (OWNER,),
    )
    for project_id in (P1, P2):
        conn.execute(
            "INSERT INTO project (id, name, enforcement, created_at) VALUES (?, ?, 'enforce', '2026-01-01T00:00:00Z')",
            (project_id, project_id),
        )
    conn.commit()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "retention.db"
    connection = sqlite3.connect(path)
    build_schema(connection)
    connection.close()
    return path


@pytest.fixture
def conn(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


def event_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def add_event(conn, project_id, event_id, ts, user_id="u_42", session_id="s_1", judged=False):
    conn.execute(
        "INSERT INTO events (project_id, id, ts, user_id, session_id, ip, user_agent, device_id, method, route, status, bytes)"
        " VALUES (?, ?, ?, ?, ?, '10.0.0.1', 'ua', 'dev', 'GET', '/x', 200, 10)",
        (project_id, event_id, event_ts(ts), user_id, session_id),
    )
    if judged:
        conn.execute(
            "INSERT INTO ai_judgments (project_id, event_id, ts) VALUES (?, ?, ?)",
            (project_id, event_id, event_ts(ts)),
        )


def add_decision(conn, project_id, ts, event_ids, decision="block", user_id="u_42", session_id="s_1"):
    return create_decision(
        conn,
        project_id,
        ts=ts,
        user_id=user_id,
        session_id=session_id,
        ip="10.0.0.9",
        decision=decision,
        signals={},
        rationale="r",
        event_ids=event_ids,
    )


def count(conn, table, project_id):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project_id,)).fetchone()[0]


def ids(conn, table, project_id):
    return {row[0] for row in conn.execute(f"SELECT id FROM {table} WHERE project_id = ?", (project_id,))}


def audit(conn, project_id):
    return [
        dict(row)
        for row in conn.execute("SELECT * FROM audit_log WHERE project_id = ? ORDER BY seq", (project_id,))
    ]


# --- purge_expired ---


def test_purge_deletes_rows_strictly_older_than_cutoff(conn):
    add_event(conn, P1, "e_old", CUTOFF - timedelta(microseconds=1), judged=True)
    add_event(conn, P1, "e_edge", CUTOFF, judged=True)
    add_event(conn, P1, "e_new", NOW, judged=True)
    old = add_decision(conn, P1, CUTOFF - timedelta(seconds=1), ["e_old"], decision="alert", session_id="s_a")
    edge = add_decision(conn, P1, CUTOFF, ["e_edge"], decision="alert", session_id="s_b")

    counts = retention.purge_expired(conn, P1, DAYS, NOW)

    assert ids(conn, "events", P1) == {"e_edge", "e_new"}
    assert ids(conn, "decisions", P1) == {edge}
    assert {r[0] for r in conn.execute("SELECT event_id FROM ai_judgments WHERE project_id = ?", (P1,))} == {
        "e_edge",
        "e_new",
    }
    assert conn.execute("SELECT COUNT(*) FROM decision_events WHERE decision_id = ?", (old,)).fetchone()[0] == 0
    assert counts == {"events": 1, "decisions": 1, "decision_events": 1, "ai_judgments": 1, "rate_spike_judgments": 0}


def test_purge_keeps_open_block_and_its_evidence(conn):
    add_event(conn, P1, "e_ev", CUTOFF - timedelta(days=5), judged=True)
    add_event(conn, P1, "e_other", CUTOFF - timedelta(days=5), session_id="s_9")
    block = add_decision(conn, P1, CUTOFF - timedelta(days=5), ["e_ev"])
    conn.execute("INSERT INTO rate_spike_judgments (project_id, session_id) VALUES (?, 's_1')", (P1,))
    conn.execute("INSERT INTO rate_spike_judgments (project_id, session_id) VALUES (?, 's_9')", (P1,))
    bump_version(conn, P1)
    version = current_version(conn, P1)

    counts = retention.purge_expired(conn, P1, DAYS, NOW)

    assert ids(conn, "decisions", P1) == {block}
    assert ids(conn, "events", P1) == {"e_ev"}
    assert count(conn, "decision_events", P1) == 1
    assert count(conn, "ai_judgments", P1) == 1
    assert {r[0] for r in conn.execute("SELECT session_id FROM rate_spike_judgments WHERE project_id = ?", (P1,))} == {
        "s_1"
    }
    assert blocklist(conn, P1)["sessions"] == ["s_1"]
    assert current_version(conn, P1) == version
    assert counts["events"] == 1 and counts["rate_spike_judgments"] == 1


def test_purge_deletes_lifted_block(conn):
    add_event(conn, P1, "e_ev", CUTOFF - timedelta(days=5))
    block = add_decision(conn, P1, CUTOFF - timedelta(days=5), ["e_ev"])
    mark_normal(conn, P1, block)

    retention.purge_expired(conn, P1, DAYS, NOW)

    assert count(conn, "decisions", P1) == 0
    assert count(conn, "events", P1) == 0


def test_purge_leaves_other_project_untouched(conn):
    for project_id in (P1, P2):
        add_event(conn, project_id, "e_old", CUTOFF - timedelta(days=1), judged=True)
        add_decision(conn, project_id, CUTOFF - timedelta(days=1), ["e_old"], decision="alert")

    retention.purge_expired(conn, P1, DAYS, NOW)

    assert count(conn, "events", P2) == 1
    assert count(conn, "decisions", P2) == 1
    assert count(conn, "decision_events", P2) == 1
    assert count(conn, "ai_judgments", P2) == 1


def test_purge_audits_counts_as_system_without_personal_data(conn):
    add_event(conn, P1, "e_old", CUTOFF - timedelta(days=1), user_id="u_secret")

    retention.purge_expired(conn, P1, DAYS, NOW)

    [row] = audit(conn, P1)
    assert row["actor_kind"] == "system" and row["actor_person_id"] is None
    assert row["action"] == "retention.purge"
    assert json.loads(row["meta"])["events"] == 1
    assert "u_secret" not in json.dumps(row)


def test_purge_is_idempotent_and_silent_when_nothing_expired(conn):
    add_event(conn, P1, "e_old", CUTOFF - timedelta(days=1))
    retention.purge_expired(conn, P1, DAYS, NOW)

    counts = retention.purge_expired(conn, P1, DAYS, NOW)

    assert sum(counts.values()) == 0
    assert len(audit(conn, P1)) == 1


def test_purge_rejects_non_positive_retention(conn):
    with pytest.raises(ValueError):
        retention.purge_expired(conn, P1, 0, NOW)


# --- delete_user_data ---


def test_delete_user_removes_only_that_user_and_lifts_blocks(conn):
    add_event(conn, P1, "e_u", NOW, judged=True)
    add_event(conn, P1, "e_x", NOW, user_id="u_other", session_id="s_x")
    add_event(conn, P2, "e_u", NOW)
    add_decision(conn, P1, NOW, ["e_u"])
    kept = add_decision(conn, P1, NOW, ["e_x"], user_id="u_other", session_id="s_x")
    add_decision(conn, P2, NOW, ["e_u"])
    conn.execute("INSERT INTO rate_spike_judgments (project_id, session_id) VALUES (?, 's_1')", (P1,))
    bump_version(conn, P1)
    before = current_version(conn, P1)
    p2_version = current_version(conn, P2)

    result = retention.delete_user_data(conn, P1, "u_42", NOW, OWNER)

    assert result == {"deleted": {"events": 1, "decisions": 1}, "version": before + 1}
    assert ids(conn, "events", P1) == {"e_x"}
    assert ids(conn, "decisions", P1) == {kept}
    assert count(conn, "decision_events", P1) == 1
    assert count(conn, "ai_judgments", P1) == 0
    assert count(conn, "rate_spike_judgments", P1) == 0
    assert blocklist(conn, P1)["sessions"] == ["s_x"]
    assert count(conn, "events", P2) == 1 and count(conn, "decisions", P2) == 1
    assert current_version(conn, P2) == p2_version


def test_delete_user_audits_hashed_user_id(conn):
    add_event(conn, P1, "e_u", NOW)

    retention.delete_user_data(conn, P1, "u_42", NOW, OWNER)

    [row] = audit(conn, P1)
    assert row["actor_kind"] == "person" and row["actor_person_id"] == OWNER
    assert row["action"] == "user_data.delete"
    assert row["target_type"] == "user"
    assert row["target_id"] == hash_user_id(P1, "u_42")
    assert "u_42" not in json.dumps(row)


def test_delete_user_without_open_block_keeps_version(conn):
    add_event(conn, P1, "e_u", NOW)
    add_decision(conn, P1, NOW, ["e_u"], decision="alert")
    bump_version(conn, P1)
    before = current_version(conn, P1)

    result = retention.delete_user_data(conn, P1, "u_42", NOW, OWNER)

    assert result["version"] == before


def test_delete_user_is_idempotent(conn):
    add_event(conn, P1, "e_u", NOW)
    add_decision(conn, P1, NOW, ["e_u"])
    first = retention.delete_user_data(conn, P1, "u_42", NOW, OWNER)

    second = retention.delete_user_data(conn, P1, "u_42", NOW, OWNER)

    assert second == {"deleted": {"events": 0, "decisions": 0}, "version": first["version"]}


def test_delete_user_rolls_back_when_audit_fails(conn):
    add_event(conn, "prj_missing", "e_u", NOW)
    add_decision(conn, "prj_missing", NOW, ["e_u"])
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        retention.delete_user_data(conn, "prj_missing", "u_42", NOW, OWNER)

    assert count(conn, "events", "prj_missing") == 1
    assert count(conn, "decisions", "prj_missing") == 1
    assert current_version(conn, "prj_missing") == 0


# --- delete_project_data ---


def test_delete_project_wipes_data_keeps_control_plane_and_bumps_version(conn):
    add_event(conn, P1, "e_1", NOW, judged=True)
    add_event(conn, P1, "e_2", NOW)
    add_decision(conn, P1, NOW, ["e_1"])
    conn.execute("INSERT INTO rate_spike_judgments (project_id, session_id) VALUES (?, 's_1')", (P1,))
    add_event(conn, P2, "e_1", NOW)
    add_decision(conn, P2, NOW, ["e_1"])

    result = retention.delete_project_data(conn, P1, NOW, OWNER)

    assert result == {"deleted": {"events": 2, "decisions": 1}, "version": 1}
    for table in ("events", "decisions", "decision_events", "ai_judgments", "rate_spike_judgments"):
        assert count(conn, table, P1) == 0
    assert conn.execute("SELECT COUNT(*) FROM project WHERE id = ?", (P1,)).fetchone()[0] == 1
    assert count(conn, "events", P2) == 1 and count(conn, "decisions", P2) == 1
    [row] = audit(conn, P1)
    assert row["action"] == "project_data.delete" and row["target_id"] == P1
    assert row["actor_person_id"] == OWNER


def test_delete_project_never_moves_version_back(conn):
    for _ in range(3):
        bump_version(conn, P1)

    first = retention.delete_project_data(conn, P1, NOW, OWNER)
    second = retention.delete_project_data(conn, P1, NOW, OWNER)

    assert first["version"] == 4
    assert second == {"deleted": {"events": 0, "decisions": 0}, "version": 5}


def test_delete_project_keeps_decision_id_high_water_mark(conn):
    add_event(conn, P1, "e_1", NOW)
    first = add_decision(conn, P1, NOW, ["e_1"])
    retention.delete_project_data(conn, P1, NOW, OWNER)

    second = add_decision(conn, P1, NOW, ["e_1"])

    assert second != first


# --- run_purge_once ---


def test_run_purge_once_purges_each_project_and_survives_failures(db_path):
    seed = sqlite3.connect(db_path)
    add_event(seed, P1, "e_old", CUTOFF - timedelta(days=1))
    add_event(seed, P2, "e_old", NOW - timedelta(days=10))
    seed.commit()
    seed.close()

    @contextmanager
    def factory():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    results = retention.run_purge_once(factory, [(P1, DAYS), ("prj_bad", 0), (P2, 5)], NOW)

    assert results[P1]["events"] == 1
    assert results[P2]["events"] == 1
    assert isinstance(results["prj_bad"], Exception)
    check = sqlite3.connect(db_path)
    assert check.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    check.close()
