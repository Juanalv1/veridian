"""T1: optional `user_email` on events (contract 1) and check (contract 9). Only the last one is kept
per (project, user), it never reaches the AI judge, and erasure and retention remove it."""

import json
from datetime import timedelta

import pytest
from conftest import AUTH_A, AUTH_B, NOW, acting, as_panel, login, make_event, make_settings, post_events
from fastapi.testclient import TestClient
from test_ai_wiring import FakeJudge
from test_decisions import attack, seed_owner_history

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.retention import purge_expired

EMAIL = "ana@example.com"


def contacts(client):
    with db.connect(client.app.state.settings.db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM user_contact ORDER BY project_id, user_id")]


def test_events_store_the_last_user_email_per_project_and_user(client):
    post_events(client, [make_event("e1", NOW - timedelta(minutes=2), user_email="old@example.com"),
                         make_event("e2", NOW - timedelta(minutes=1), user_email=EMAIL)])
    post_events(client, [make_event("e3", NOW, user_id="u_7", user_email="other@example.com")], headers=AUTH_B)

    rows = contacts(client)

    assert [(r["project_id"], r["user_id"], r["email"]) for r in rows] == [
        ("client_a", "u_42", EMAIL), ("client_b", "u_7", "other@example.com")]


def test_a_late_batch_does_not_replace_a_newer_email(client):
    post_events(client, [make_event("e1", NOW, user_email=EMAIL)])
    post_events(client, [make_event("e0", NOW - timedelta(hours=1), user_email="older@example.com")])
    assert [r["email"] for r in contacts(client)] == [EMAIL]


def test_events_without_user_email_keep_the_stored_one(client):
    post_events(client, [make_event("e1", NOW - timedelta(minutes=1), user_email=EMAIL)])
    post_events(client, [make_event("e2", NOW, user_email=None)])
    post_events(client, [make_event("e3", NOW, user_email="")])
    assert [r["email"] for r in contacts(client)] == [EMAIL]


@pytest.mark.parametrize("bad", ["not-an-email", "a@b", "a b@example.com", "x" * 250 + "@example.com"])
def test_an_invalid_user_email_is_ignored_without_dropping_the_event(client, bad):
    # A malformed address must not cost the customer the whole batch: the event is kept, the email is not.
    response = post_events(client, [make_event("e1", NOW, user_email=bad)])
    assert response.status_code == 202 and response.json() == {"accepted": 1}
    assert contacts(client) == []


def test_check_stores_the_user_email(client):
    response = client.post("/v1/check", json={"event": make_event("c1", NOW, user_email=EMAIL)}, headers=AUTH_A)
    assert response.status_code == 200
    assert [r["email"] for r in contacts(client)] == [EMAIL]


def test_the_user_email_never_reaches_the_ai_judge(tmp_path, clock):
    judge = FakeJudge(decision="block")
    with TestClient(create_app(make_settings(tmp_path, clock, judge=judge))) as client:
        seed_owner_history(client)
        post_events(client, [make_event("o1", NOW, route="/invoices/{id}", ip="200.44.10.5", user_email=EMAIL)])
        assert judge.calls, "the judge should have been asked about the new network"
        assert EMAIL not in json.dumps(judge.calls, default=str)
        decisions = client.get("/v1/decisions", headers=as_panel(client)).json()
        activity = client.get("/v1/activity", headers=as_panel(client)).json()
        assert EMAIL not in json.dumps([decisions, activity])


def test_erasing_a_user_deletes_their_email_only(client):
    owner_id = login(client, "sub-test-owner", "owner@test.example")["person"]["id"]
    post_events(client, [make_event("e1", NOW, user_email=EMAIL),
                         make_event("e2", NOW, user_id="u_7", session_id="s7", user_email="other@example.com")])

    response = client.delete("/v1/projects/client_a/users/u_42", headers=acting(owner_id))

    assert response.status_code == 200
    assert [(r["user_id"], r["email"]) for r in contacts(client)] == [("u_7", "other@example.com")]


def test_retention_purges_emails_not_seen_within_the_window(client):
    post_events(client, [make_event("e1", NOW - timedelta(days=40), user_email="stale@example.com"),
                         make_event("e2", NOW - timedelta(days=1), user_id="u_7", session_id="s7",
                                    user_email=EMAIL)])

    with db.connect(client.app.state.settings.db_path) as conn:
        purge_expired(conn, "client_a", 30, NOW)

    assert [(r["user_id"], r["email"]) for r in contacts(client)] == [("u_7", EMAIL)]
