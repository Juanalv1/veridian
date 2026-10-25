"""U17: block alert email to the owners and admins of the project's organization, through the
same outbox as the webhook, and the per-organization `notify_email` preference."""

import json
from datetime import timedelta

import pytest
from conftest import AUTH_A, NOW, TEST_OWNER_EMAIL, acting, login, make_settings
from fastapi.testclient import TestClient
from test_decisions import ATTACKER, attack, seed_owner_history

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.control_plane import repository
from veridian_engine.control_plane.legacy import DEMO_ORG_ID
from veridian_engine.decisions import create_decision
from veridian_engine.mailer import FakeMailer, NullMailer
from veridian_engine.notifications.webhook import DELIVERED, FAILED, PENDING
from veridian_engine.project_settings import set_project_flags

ADMIN_EMAIL = "admin@example.com"
VIEWER_EMAIL = "viewer@example.com"
QUIET_EMAIL = "quiet@example.com"


@pytest.fixture
def app_factory(tmp_path, clock):
    clients = []

    def factory(mailer=None, **overrides):
        fields = {"webhooks": True, "mailer": mailer if mailer is not None else FakeMailer(), "webhook_poll_s": 3600.0}
        fields.update(overrides)
        test_client = TestClient(create_app(make_settings(tmp_path, clock, **fields)))
        test_client.__enter__()
        clients.append(test_client)
        return test_client

    yield factory
    for test_client in clients:
        test_client.__exit__(None, None, None)


def members(client):
    """Owner (bootstrap), an admin, a viewer and an admin who turned the email off, in org Demo."""
    owner_id = login(client, "sub-owner", TEST_OWNER_EMAIL)["person"]["id"]
    ids = {"owner": owner_id}
    for sub, email, role in (("sub-a", ADMIN_EMAIL, "admin"), ("sub-v", VIEWER_EMAIL, "viewer"),
                             ("sub-q", QUIET_EMAIL, "admin")):
        with db.connect(client.app.state.settings.db_path) as conn:
            repository.create_invitation(conn, actor_id=owner_id, org_id=DEMO_ORG_ID, email=email, role=role, now=NOW)
        ids[email] = login(client, sub, email)["person"]["id"]
    response = client.patch(f"/v1/orgs/{DEMO_ORG_ID}/me", json={"notify_email": False}, headers=acting(ids[QUIET_EMAIL]))
    assert response.status_code == 200, response.text
    return ids


def email_rows(client):
    with db.connect(client.app.state.settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM notifications WHERE kind = 'email' ORDER BY recipient").fetchall()
    return [dict(row) for row in rows]


def run_email_sender(client):
    return client.app.state.email_sender.run_once()


# --- enqueue ---


def test_a_block_queues_one_email_per_notified_owner_and_admin(app_factory):
    client = app_factory()
    members(client)
    seed_owner_history(client)

    attack(client)

    rows = email_rows(client)
    assert [row["recipient"] for row in rows] == [ADMIN_EMAIL, TEST_OWNER_EMAIL]
    assert all(row["status"] == PENDING and row["project_id"] == "client_a" for row in rows)


def test_emails_do_not_need_a_webhook(app_factory):
    client = app_factory()
    members(client)
    seed_owner_history(client)
    attack(client)
    with db.connect(client.app.state.settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM webhook_endpoint").fetchone()[0] == 0
    assert len(email_rows(client)) == 2


def test_decisions_other_than_block_queue_no_email(app_factory):
    client = app_factory()
    members(client)
    with db.connect(client.app.state.settings.db_path) as conn:
        for kind in ("alert", "step_up", "review"):
            create_decision(conn, "client_a", ts=NOW, user_id="u_42", session_id=f"s_{kind}", ip="", decision=kind,
                            signals={}, rationale="test", event_ids=[])
    assert email_rows(client) == []


def test_a_project_without_notified_members_queues_nothing(app_factory):
    client = app_factory()
    seed_owner_history(client)
    attack(client)
    assert email_rows(client) == []


# --- sending ---


def test_the_sender_mails_each_recipient_without_the_ip(app_factory):
    mailer = FakeMailer()
    client = app_factory(mailer)
    members(client)
    seed_owner_history(client)
    attack(client)

    assert run_email_sender(client) == 2

    assert sorted(message.to for message in mailer.sent) == [ADMIN_EMAIL, TEST_OWNER_EMAIL]
    message = mailer.sent[0]
    assert "client_a" in message.subject or "client_a" in message.text
    assert "u_42" in message.text
    assert "POST /payouts/bank-account" in message.text
    assert ATTACKER["ip"] not in message.subject + message.text
    assert "observe" not in message.text
    assert all(row["status"] == DELIVERED for row in email_rows(client))
    assert run_email_sender(client) == 0
    assert len(mailer.sent) == 2


def test_an_observed_block_says_it_was_not_applied(app_factory):
    mailer = FakeMailer()
    client = app_factory(mailer)
    members(client)
    with db.connect(client.app.state.settings.db_path) as conn:
        set_project_flags(conn, "client_a", enforcement="observe")
    seed_owner_history(client)
    attack(client)
    run_email_sender(client)
    assert all("modo observe" in message.text for message in mailer.sent)


def test_a_failing_address_is_retried_alone(app_factory, clock):
    mailer = FakeMailer(fail_for={ADMIN_EMAIL})
    client = app_factory(mailer)
    members(client)
    seed_owner_history(client)
    attack(client)

    run_email_sender(client)

    by_recipient = {row["recipient"]: row for row in email_rows(client)}
    assert by_recipient[TEST_OWNER_EMAIL]["status"] == DELIVERED
    failed = by_recipient[ADMIN_EMAIL]
    assert failed["status"] == PENDING and failed["attempts"] == 1 and failed["last_error"]

    mailer.fail_for.clear()
    clock.now = NOW + timedelta(hours=1)
    run_email_sender(client)
    assert [message.to for message in mailer.sent] == [TEST_OWNER_EMAIL, ADMIN_EMAIL]


def test_an_admin_who_opts_out_after_the_block_is_not_mailed(app_factory):
    mailer = FakeMailer()
    client = app_factory(mailer)
    ids = members(client)
    seed_owner_history(client)
    attack(client)
    client.patch(f"/v1/orgs/{DEMO_ORG_ID}/me", json={"notify_email": False}, headers=acting(ids[ADMIN_EMAIL]))

    run_email_sender(client)

    assert [message.to for message in mailer.sent] == [TEST_OWNER_EMAIL]
    by_recipient = {row["recipient"]: row for row in email_rows(client)}
    assert by_recipient[ADMIN_EMAIL]["status"] == FAILED


def test_without_a_provider_the_email_is_marked_failed_not_retried(app_factory):
    client = app_factory(NullMailer())
    members(client)
    seed_owner_history(client)
    attack(client)

    run_email_sender(client)

    rows = email_rows(client)
    assert all(row["status"] == FAILED and row["last_error"] == "no email provider configured" for row in rows)
    assert all(row["attempts"] == 0 for row in rows)


def test_webhook_deliveries_never_list_the_emails(app_factory):
    client = app_factory()
    ids = members(client)
    seed_owner_history(client)
    attack(client)
    response = client.get("/v1/projects/client_a/webhook/deliveries", headers=acting(ids["owner"]))
    assert response.status_code == 200 and response.json() == {"deliveries": []}


def test_the_email_sender_lives_with_the_app(app_factory):
    client = app_factory()
    assert client.app.state.email_sender.is_alive()


def test_notifications_off_start_no_email_sender(tmp_path, clock):
    with TestClient(create_app(make_settings(tmp_path, clock, webhooks=False))) as client:
        assert client.app.state.email_sender is None


# --- preference ---


def test_a_member_reads_and_changes_their_own_email_preference(app_factory):
    client = app_factory()
    ids = members(client)
    viewer = ids[VIEWER_EMAIL]
    mine = client.get(f"/v1/orgs/{DEMO_ORG_ID}/me", headers=acting(viewer))
    assert mine.status_code == 200
    assert mine.json() == {"org_id": DEMO_ORG_ID, "role": "viewer", "notify_email": True}

    changed = client.patch(f"/v1/orgs/{DEMO_ORG_ID}/me", json={"notify_email": False}, headers=acting(viewer))
    assert changed.json() == {"org_id": DEMO_ORG_ID, "role": "viewer", "notify_email": False}
    assert client.get(f"/v1/orgs/{DEMO_ORG_ID}/me", headers=acting(viewer)).json()["notify_email"] is False
    # Only the caller's own row changes.
    assert client.get(f"/v1/orgs/{DEMO_ORG_ID}/me", headers=acting(ids[ADMIN_EMAIL])).json()["notify_email"] is True

    with db.connect(client.app.state.settings.db_path) as conn:
        [row] = conn.execute("SELECT * FROM audit_log WHERE action = 'member.notify_email'"
                             " AND actor_person_id = ?", (viewer,)).fetchall()
    assert json.loads(row["meta"]) == {"notify_email": False}


def test_the_preference_needs_membership_and_the_panel(app_factory):
    client = app_factory()
    stranger = login(client, "sub-s", "stranger@example.com")["person"]["id"]
    assert client.get(f"/v1/orgs/{DEMO_ORG_ID}/me", headers=acting(stranger)).status_code == 403
    assert client.patch(f"/v1/orgs/{DEMO_ORG_ID}/me", json={"notify_email": False},
                        headers=acting(stranger)).status_code == 403
    assert client.get(f"/v1/orgs/{DEMO_ORG_ID}/me", headers=AUTH_A).status_code == 403


@pytest.mark.parametrize("body", [{}, {"notify_email": "maybe"}])
def test_the_preference_validates_the_body(app_factory, body):
    client = app_factory()
    ids = members(client)
    assert client.patch(f"/v1/orgs/{DEMO_ORG_ID}/me", json=body, headers=acting(ids["owner"])).status_code == 422
