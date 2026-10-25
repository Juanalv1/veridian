"""T3: the branded email to the blocked end user, queued on each block through the same outbox as
U17 and the webhook, with a single-use token whose hash is the only thing stored."""

import hashlib
import re
from datetime import timedelta

import pytest
from conftest import AUTH_A, NOW, TEST_OWNER_EMAIL, acting, login, make_event, make_settings, post_events
from fastapi.testclient import TestClient
from test_decisions import ATTACKER, attack, seed_owner_history

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.mailer import FakeMailer, NullMailer
from veridian_engine.notifications.webhook import FAILED, PENDING

USER_EMAIL = "ana@example.com"
BRAND = {
    "brand_name": "Acme Pay",
    "brand_logo_url": "https://cdn.acme.example/logo.png",
    "brand_color": "#1F6FEB",
    "brand_support_email": "soporte@acme.example",
    "self_unlock_enabled": True,
}
PANEL = "https://panel.test.example"


def public(host, port):
    return ["93.184.216.34"]


@pytest.fixture
def app_factory(tmp_path, clock):
    clients = []

    def factory(mailer=None, **overrides):
        fields = {"webhooks": True, "mailer": mailer if mailer is not None else FakeMailer(), "webhook_poll_s": 3600.0,
                  "webhook_resolver": public, "panel_base_url": PANEL}
        fields.update(overrides)
        test_client = TestClient(create_app(make_settings(tmp_path, clock, **fields)))
        test_client.__enter__()
        clients.append(test_client)
        return test_client

    yield factory
    for test_client in clients:
        test_client.__exit__(None, None, None)


def owner(client):
    return login(client, "sub-test-owner", TEST_OWNER_EMAIL)["person"]["id"]


def configure(client, **brand):
    response = client.patch("/v1/projects/client_a", json={**BRAND, **brand}, headers=acting(owner(client)))
    assert response.status_code == 200, response.text


def rows(client, sql="SELECT * FROM notifications WHERE purpose = 'self_unlock' ORDER BY created_at, id"):
    with db.connect(client.app.state.settings.db_path) as conn:
        return [dict(r) for r in conn.execute(sql)]


def tokens(client):
    return rows(client, "SELECT * FROM self_unlock_token ORDER BY created_at")


def run_sender(client):
    return client.app.state.email_sender.run_once()


def blocked(client, **overrides):
    seed_owner_history(client)
    assert attack(client, user_email=USER_EMAIL, **overrides).status_code == 202


def link_token(message):
    match = re.search(rf"{re.escape(PANEL)}/desbloquear/([A-Za-z0-9_-]+)", message.text)
    assert match, message.text
    return match.group(1)


# --- what is queued ---


def test_a_block_with_user_email_and_the_feature_on_queues_one_branded_email(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)

    [row] = rows(client)
    assert row["status"] == PENDING and row["kind"] == "email" and row["project_id"] == "client_a"
    assert row["recipient"] is None  # the address is read from user_contact when sending, never copied
    assert run_sender(client) == 1

    [message] = client.app.state.settings.mailer.sent
    assert message.to == USER_EMAIL and message.from_name == "Acme Pay"
    assert "Detectamos un acceso sospechoso a tu cuenta" in message.subject
    assert "Fui yo, desbloquear" in message.html and "Fui yo, desbloquear" in message.text
    assert "#1F6FEB" in message.html and "https://cdn.acme.example/logo.png" in message.html
    assert "soporte@acme.example" in message.html and "soporte@acme.example" in message.text
    assert "Veridian" not in message.subject + message.text + message.html


def test_the_link_points_to_the_panel_and_only_the_token_hash_is_stored(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    run_sender(client)

    [message] = client.app.state.settings.mailer.sent
    token = link_token(message)
    assert f"{PANEL}/desbloquear/{token}" in message.html
    [stored] = tokens(client)
    assert stored["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert stored["project_id"] == "client_a" and stored["user_id"] == "u_42" and stored["used_at"] is None
    assert stored["decision_id"].startswith("d_")
    expires = stored["expires_at"]
    assert expires == (NOW + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.connect(client.app.state.settings.db_path) as conn:
        dump = "\n".join(str(tuple(r)) for t in ("self_unlock_token", "notifications", "user_contact", "decisions")
                         for r in conn.execute(f"SELECT * FROM {t}"))
    assert token not in dump


def test_the_default_panel_url_is_used_when_none_is_configured(app_factory):
    client = app_factory(panel_base_url="")
    configure(client)
    blocked(client)
    run_sender(client)
    [message] = client.app.state.settings.mailer.sent
    assert "https://veridian-panel.onrender.com/desbloquear/" in message.text


def test_no_email_without_user_email(app_factory):
    client = app_factory()
    configure(client)
    seed_owner_history(client)
    attack(client)
    assert rows(client) == []


def test_no_email_when_the_feature_is_off(app_factory):
    client = app_factory()
    configure(client, self_unlock_enabled=False)
    blocked(client)
    assert rows(client) == []


def test_no_email_when_the_project_only_observes(app_factory):
    client = app_factory()
    configure(client)
    client.patch("/v1/projects/client_a", json={"enforcement": "observe"}, headers=acting(owner(client)))
    blocked(client)
    assert rows(client) == []


def test_a_block_decided_by_check_also_queues_the_email(app_factory):
    client = app_factory()
    configure(client)
    seed_owner_history(client, method="POST", route="/payouts/bank-account")
    event = make_event("c1", NOW, method="POST", route="/payouts/bank-account", user_email=USER_EMAIL,
                       **{**ATTACKER, "device_id": "d_evil"})
    # Same user, new network and device on a critical route that the owner never used: rule block.
    seed = client.post("/v1/check", json={"event": {**event, "route": "/payouts/other"}}, headers=AUTH_A)
    assert seed.json()["verdict"] == "block"
    assert len(rows(client)) == 1


def test_two_blocks_of_the_same_user_within_15_minutes_send_one_email(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    attack(client, prefix="b", session_id="s_evil2", ip="203.0.113.10", user_email=USER_EMAIL)
    assert len(rows(client)) == 1
    run_sender(client)
    assert len(client.app.state.settings.mailer.sent) == 1


def test_a_block_after_the_interval_sends_another_email(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    later = NOW + timedelta(minutes=16)
    post_events(client, [make_event("late1", later, session_id="s_evil3", ip="203.0.113.11", device_id="d_evil",
                                    method="POST", route="/payouts/bank-account", user_email=USER_EMAIL)])
    assert len(rows(client)) == 2


def test_another_user_is_not_limited_by_the_first(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    post_events(client, [make_event(f"o{day}", NOW - timedelta(days=day), user_id="u_7", session_id="s7")
                         for day in range(1, 4)])
    post_events(client, [make_event("x7", NOW, user_id="u_7", session_id="s_evil7", ip="203.0.113.12",
                                    method="POST", route="/payouts/bank-account", user_email="bea@example.com")])
    assert len(rows(client)) == 2


# --- what is sent ---


def test_neither_the_email_nor_its_subject_carry_a_full_ip(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    run_sender(client)
    [message] = client.app.state.settings.mailer.sent
    everything = message.subject + message.text + message.html
    for ip in ("203.0.113.9", "190.202.14.7", "203.0.113"):
        assert ip not in everything


def test_the_email_shows_when_and_from_which_device(app_factory):
    client = app_factory()
    configure(client)
    seed_owner_history(client)
    attack(client, user_email=USER_EMAIL, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0 Safari/537")
    run_sender(client)
    [message] = client.app.state.settings.mailer.sent
    assert "26 de octubre de 2026" in message.text and "12:00 UTC" in message.text
    assert "Chrome en Windows" in message.text and "Chrome en Windows" in message.html


def test_brand_values_are_escaped_in_the_html(app_factory):
    client = app_factory()
    configure(client, brand_name="<script>alert(1)</script> & Co", brand_support_url="https://acme.example/a?x=1&y=2")
    blocked(client)
    run_sender(client)
    [message] = client.app.state.settings.mailer.sent
    assert "<script>" not in message.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; Co" in message.html
    assert "x=1&amp;y=2" in message.html


def test_without_a_brand_name_the_project_name_is_used(app_factory):
    client = app_factory()
    configure(client, brand_name=None, brand_logo_url=None, brand_color=None, brand_support_email=None)
    blocked(client)
    run_sender(client)
    [message] = client.app.state.settings.mailer.sent
    assert message.from_name == "client_a"
    assert "<img" not in message.html


# --- the worker ---


def test_without_a_provider_the_email_fails_and_issues_no_token(app_factory):
    client = app_factory(mailer=NullMailer())
    configure(client)
    blocked(client)
    run_sender(client)
    [row] = rows(client)
    assert row["status"] == FAILED and row["last_error"] == "no email provider configured"
    assert tokens(client) == []


def test_a_decision_marked_normal_before_sending_is_not_mailed(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    decision_id = rows(client, "SELECT id FROM decisions")[0]["id"]
    client.post(f"/v1/decisions/{decision_id}/normal", headers=acting(owner(client), "client_a"))
    run_sender(client)
    [row] = rows(client)
    assert row["status"] == FAILED and row["last_error"] == "decision no longer open"
    assert client.app.state.settings.mailer.sent == [] and tokens(client) == []


def test_turning_the_feature_off_before_sending_cancels_the_email(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    configure(client, self_unlock_enabled=False)
    run_sender(client)
    assert rows(client)[0]["last_error"] == "self-unlock is off"
    assert client.app.state.settings.mailer.sent == []


def test_an_erased_address_is_not_mailed(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    with db.connect(client.app.state.settings.db_path) as conn:
        conn.execute("DELETE FROM user_contact")
    run_sender(client)
    assert rows(client)[0]["last_error"] == "no user email"
    assert client.app.state.settings.mailer.sent == []


def test_a_retry_replaces_the_unused_token_of_the_failed_attempt(app_factory, clock):
    mailer = FakeMailer(fail_for={USER_EMAIL})
    client = app_factory(mailer=mailer)
    configure(client)
    blocked(client)
    run_sender(client)
    [first] = tokens(client)
    assert rows(client)[0]["attempts"] == 1

    mailer.fail_for.clear()
    clock.now += timedelta(minutes=2)
    run_sender(client)

    [second] = tokens(client)
    assert second["token_hash"] != first["token_hash"]
    assert link_token(mailer.sent[0]) and hashlib.sha256(link_token(mailer.sent[0]).encode()).hexdigest() == second["token_hash"]


def test_erasing_the_user_deletes_their_tokens(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    run_sender(client)
    assert len(tokens(client)) == 1
    client.delete("/v1/projects/client_a/users/u_42", headers=acting(owner(client)))
    assert tokens(client) == []


# --- what the middleware learns ---


def test_the_blocklist_and_the_check_say_when_self_unlock_applies(app_factory):
    client = app_factory()
    configure(client)
    blocked(client)
    listed = client.get("/v1/blocklist", headers=AUTH_A).json()
    assert listed["sessions"] == ["s_evil"] and listed["self_unlock_sessions"] == ["s_evil"]

    configure(client, self_unlock_enabled=False)
    assert "self_unlock_sessions" not in client.get("/v1/blocklist", headers=AUTH_A).json()


def test_a_blocked_user_without_email_is_not_listed_for_self_unlock(app_factory):
    client = app_factory()
    configure(client)
    seed_owner_history(client)
    attack(client)
    assert client.get("/v1/blocklist", headers=AUTH_A).json()["self_unlock_sessions"] == []
