from dataclasses import dataclass

from veridian_engine.mailer import (
    FakeMailer,
    NullMailer,
    build_block_alert,
    notify_admins_of_block,
    select_recipients,
)

BLOCK = {
    "id": "d_311",
    "ts": "2026-10-26T10:14:05Z",
    "user_id": "u_42",
    "route": "POST /payouts/bank-account",
    "ip": "203.0.113.77",
    "decision": "block",
    "rationale": "New device and country on a sensitive route.",
}

MEMBERS = [
    {"email": "owner@example.com", "role": "owner", "notify_email": 1},
    {"email": "admin@example.com", "role": "admin", "notify_email": True},
    {"email": "quiet-admin@example.com", "role": "admin", "notify_email": 0},
    {"email": "viewer@example.com", "role": "viewer", "notify_email": 1},
]


@dataclass
class MemberRow:
    email: str
    role: str
    notify_email: bool


def notify(mailer, members=MEMBERS, decision=BLOCK):
    return notify_admins_of_block(
        mailer,
        members,
        project_name="Mi app",
        decision=decision,
        panel_url="https://panel.example/decisiones/d_311",
    )


def test_one_mail_per_eligible_admin():
    mailer = FakeMailer()

    sent = notify(mailer)

    assert sent == 2
    assert [message.to for message in mailer.sent] == ["owner@example.com", "admin@example.com"]


def test_viewer_and_opted_out_admin_are_excluded():
    recipients = select_recipients(MEMBERS)

    assert "viewer@example.com" not in recipients
    assert "quiet-admin@example.com" not in recipients


def test_recipients_accept_dataclass_members():
    rows = [MemberRow("a@example.com", "admin", True), MemberRow("v@example.com", "viewer", True)]

    assert select_recipients(rows) == ["a@example.com"]


def test_body_has_context_but_never_the_ip():
    subject, text = build_block_alert(
        project_name="Mi app", decision=BLOCK, panel_url="https://panel.example/decisiones/d_311"
    )

    assert "203.0.113.77" not in subject
    assert "203.0.113.77" not in text
    for expected in ("Mi app", "u_42", "POST /payouts/bank-account", "2026-10-26T10:14:05Z",
                     BLOCK["rationale"], "https://panel.example/decisiones/d_311"):
        assert expected in text
    assert "Mi app" in subject


def test_sent_messages_never_contain_the_ip():
    mailer = FakeMailer()

    notify(mailer)

    assert all("203.0.113.77" not in m.subject + m.text for m in mailer.sent)


def test_send_failure_is_swallowed_and_others_still_sent():
    mailer = FakeMailer(fail_for={"owner@example.com"})

    sent = notify(mailer)

    assert sent == 1
    assert [message.to for message in mailer.sent] == ["admin@example.com"]


def test_non_block_decision_sends_nothing():
    mailer = FakeMailer()

    sent = notify(mailer, decision={**BLOCK, "decision": "alert"})

    assert sent == 0
    assert mailer.sent == []


def test_null_mailer_accepts_and_discards():
    assert notify(NullMailer()) == 2


def test_senders_accept_html_and_a_visible_name_and_the_fake_records_them():
    fake = FakeMailer()
    fake.send("a@example.com", "Asunto", "texto")
    fake.send("b@example.com", "Asunto", "texto", html="<p>hola</p>", from_name="Acme")

    assert (fake.sent[0].html, fake.sent[0].from_name) == (None, None)
    assert (fake.sent[1].html, fake.sent[1].from_name) == ("<p>hola</p>", "Acme")
    NullMailer().send("a@example.com", "Asunto", "texto", html="<p>hola</p>", from_name="Acme")
