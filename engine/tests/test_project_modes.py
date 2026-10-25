"""U14: enforcement = observe and ai_enabled per project (docs/DISENO-PRODUCTO.md, decisions 6 and 10)."""

import pytest
from conftest import NOW, make_event, make_settings
from fastapi.testclient import TestClient
from test_ai_wiring import FakeJudge
from test_check import CRITICAL, GRAY, check
from test_decisions import ATTACKER, attack, blocklist, decisions, seed_owner_history

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.project_settings import ProjectFlags, get_project_flags, set_project_flags

PROJECT_A = "client_a"


@pytest.fixture
def app_client(tmp_path, clock):
    clients = []

    def factory(judge=None):
        settings = make_settings(tmp_path, clock, judge=judge)
        test_client = TestClient(create_app(settings))
        clients.append(test_client)
        return test_client, settings

    yield factory
    for test_client in clients:
        test_client.close()


def set_flags(settings, **flags):
    with db.connect(settings.db_path) as conn:
        set_project_flags(conn, PROJECT_A, **flags)


def ai_judgments(settings) -> int:
    with db.connect(settings.db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM ai_judgments WHERE project_id = ?", (PROJECT_A,)).fetchone()[0]


def activity_status(client, event_id):
    items = client.get("/v1/activity", headers={"Authorization": "Bearer tok_a"}).json()["activity"]
    return {item["event_id"]: item["status"] for item in items}[event_id]


# --- observe ---


def test_observe_records_the_block_but_publishes_an_empty_blocklist(app_client):
    client, settings = app_client()
    set_flags(settings, enforcement="observe")
    seed_owner_history(client)

    attack(client)

    [decision] = decisions(client)
    assert decision["decision"] == "block"
    body = blocklist(client).json()
    assert body["users"] == [] and body["sessions"] == [] and body["ips"] == []


def test_observe_check_allows_and_returns_the_decision_it_would_have_taken(app_client):
    client, settings = app_client()
    set_flags(settings, enforcement="observe")
    seed_owner_history(client)

    body = check(client, make_event("c1", NOW, **ATTACKER, **CRITICAL)).json()

    [decision] = decisions(client)
    assert decision["decision"] == "block"
    assert body == {"verdict": "allow", "decision_id": decision["id"]}
    # The app runs the request, so the stored status is the reported one.
    assert activity_status(client, "c1") == 200


def test_observe_check_allows_a_session_already_in_the_recorded_blocks(app_client):
    client, settings = app_client()
    set_flags(settings, enforcement="observe")
    seed_owner_history(client)
    attack(client)

    body = check(client, make_event("c2", NOW, **ATTACKER, **CRITICAL)).json()

    assert body["verdict"] == "allow"


def test_observe_check_allows_an_ai_block(app_client):
    judge = FakeJudge(decision="block")
    client, settings = app_client(judge)
    set_flags(settings, enforcement="observe")
    seed_owner_history(client)

    body = check(client, make_event("c1", NOW, **GRAY)).json()

    assert len(judge.calls) == 1
    [decision] = decisions(client)
    assert decision["decision"] == "block"
    assert body == {"verdict": "allow", "decision_id": decision["id"]}


def test_switching_to_enforce_publishes_the_blocks_with_a_new_version(app_client):
    client, settings = app_client()
    set_flags(settings, enforcement="observe")
    seed_owner_history(client)
    attack(client)
    observed = blocklist(client).json()

    set_flags(settings, enforcement="enforce")

    assert blocklist(client, since=observed["version"]).status_code == 200
    enforced = blocklist(client).json()
    assert enforced["version"] > observed["version"]
    assert enforced["sessions"] == ["s_evil"]
    assert check(client, make_event("c2", NOW, **ATTACKER, **CRITICAL)).json()["verdict"] == "block"


def test_switching_to_observe_bumps_the_version_and_empties_the_list(app_client):
    client, settings = app_client()
    seed_owner_history(client)
    attack(client)
    enforced = blocklist(client).json()
    assert enforced["sessions"] == ["s_evil"]

    set_flags(settings, enforcement="observe")

    observed = blocklist(client, since=enforced["version"])
    assert observed.status_code == 200
    assert observed.json()["version"] > enforced["version"]
    assert observed.json()["sessions"] == []


def test_setting_the_same_enforcement_does_not_bump_the_version(app_client):
    client, settings = app_client()
    seed_owner_history(client)
    attack(client)
    version = blocklist(client).json()["version"]

    set_flags(settings, enforcement="enforce")

    assert blocklist(client).json()["version"] == version


def test_observe_is_per_project(app_client):
    _, settings = app_client()

    set_flags(settings, enforcement="observe", ai_enabled=False)

    with db.connect(settings.db_path) as conn:
        assert get_project_flags(conn, PROJECT_A) == ProjectFlags(enforce=False, ai_enabled=False)
        assert get_project_flags(conn, "client_b") == ProjectFlags(enforce=True, ai_enabled=True)


# --- ai_enabled ---


def test_ai_disabled_ingestion_never_calls_the_judge_and_rules_still_block(app_client):
    judge = FakeJudge(decision="block")
    client, settings = app_client(judge)
    set_flags(settings, ai_enabled=False)
    seed_owner_history(client)

    attack(client)
    # A gray case on a new network that the AI would otherwise judge.
    client.post(
        "/v1/events",
        json={"events": [make_event("g1", NOW, session_id="s_gray", ip="200.44.10.5")]},
        headers={"Authorization": "Bearer tok_a"},
    )

    assert judge.calls == []
    assert ai_judgments(settings) == 0
    assert blocklist(client).json()["sessions"] == ["s_evil"]


def test_ai_disabled_check_never_calls_the_judge_and_rules_still_block(app_client):
    judge = FakeJudge(decision="block")
    client, settings = app_client(judge)
    set_flags(settings, ai_enabled=False)
    seed_owner_history(client)

    gray = check(client, make_event("c1", NOW, **GRAY)).json()
    attacker = check(client, make_event("c2", NOW, **ATTACKER, **CRITICAL)).json()

    assert gray["verdict"] == "allow"
    assert attacker["verdict"] == "block"
    assert judge.calls == []
    assert ai_judgments(settings) == 0


# --- flags ---


def test_missing_project_row_means_enforce_with_ai(tmp_path, clock):
    with TestClient(create_app(make_settings(tmp_path, clock))):
        pass
    with db.connect(str(tmp_path / "veridian.db")) as conn:
        assert get_project_flags(conn, "no_such_project") == ProjectFlags(enforce=True, ai_enabled=True)


def test_set_flags_rejects_unknown_enforcement_and_missing_project(app_client):
    _, settings = app_client()
    with db.connect(settings.db_path) as conn:
        with pytest.raises(ValueError):
            set_project_flags(conn, PROJECT_A, enforcement="audit")
        with pytest.raises(LookupError):
            set_project_flags(conn, "no_such_project", enforcement="observe")
