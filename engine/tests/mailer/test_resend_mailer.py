"""T5: ResendMailer sends through Resend's HTTP API (HTTP is mocked) and is selected by configuration."""

import json
import logging

import httpx
import pytest

from veridian_engine.mailer import NullMailer
from veridian_engine.resend_mailer import RESEND_URL, ResendMailer, mailer_from_env

KEY = "re_test_secret_value"


def mailer_with(handler, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ResendMailer(KEY, "seguridad@mail.example.com", client=client, **kwargs)


def capture():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "em_1"})

    return seen, handler


def test_send_posts_the_documented_request_shape():
    seen, handler = capture()
    mailer_with(handler).send("ana@example.com", "Asunto", "texto", html="<p>hola</p>", from_name="Acme Pay")

    [request] = seen
    assert request.method == "POST" and str(request.url) == RESEND_URL == "https://api.resend.com/emails"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "from": "Acme Pay <seguridad@mail.example.com>",
        "to": ["ana@example.com"],
        "subject": "Asunto",
        "html": "<p>hola</p>",
        "text": "texto",
    }


def test_a_text_only_message_has_no_html_and_a_default_sender_name():
    seen, handler = capture()
    mailer_with(handler).send("ana@example.com", "Asunto", "texto")
    body = json.loads(seen[0].content)
    assert "html" not in body and body["from"] == "Veridian <seguridad@mail.example.com>"


def test_the_visible_name_cannot_inject_headers_or_addresses():
    seen, handler = capture()
    mailer_with(handler).send("ana@example.com", "S", "t", from_name='Acme\r\nBcc: x@evil.test" <evil@x.test>, Co')
    sender = json.loads(seen[0].content)["from"]
    assert "\r" not in sender and "\n" not in sender
    assert sender.endswith("<seguridad@mail.example.com>") and sender.count("<") == 1 and sender.count(",") == 0


def test_a_blank_visible_name_falls_back_to_the_default():
    seen, handler = capture()
    mailer_with(handler).send("ana@example.com", "S", "t", from_name='  <>"  ')
    assert json.loads(seen[0].content)["from"] == "Veridian <seguridad@mail.example.com>"


def test_a_provider_error_raises_without_the_key_or_the_response_body():
    def handler(request):
        return httpx.Response(422, json={"message": "invalid to: ana@example.com"})

    with pytest.raises(RuntimeError) as error:
        mailer_with(handler).send("ana@example.com", "S", "t")
    assert "422" in str(error.value)
    assert KEY not in str(error.value) and "ana@example.com" not in str(error.value)


def test_a_network_error_raises_a_clean_error():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(RuntimeError) as error:
        mailer_with(handler).send("ana@example.com", "S", "t")
    assert KEY not in str(error.value)


def test_the_key_is_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    _, handler = capture()
    mailer_with(handler).send("ana@example.com", "S", "t")
    assert KEY not in caplog.text and KEY not in repr(mailer_with(handler))


def test_configured_with_both_variables_the_resend_mailer_is_selected():
    mailer = mailer_from_env({"RESEND_API_KEY": KEY, "VERIDIAN_MAIL_FROM": "seguridad@mail.example.com"})
    assert isinstance(mailer, ResendMailer)


@pytest.mark.parametrize("env", [{}, {"RESEND_API_KEY": KEY}, {"VERIDIAN_MAIL_FROM": "a@b.example"},
                                 {"RESEND_API_KEY": " ", "VERIDIAN_MAIL_FROM": "a@b.example"},
                                 {"RESEND_API_KEY": KEY, "VERIDIAN_MAIL_FROM": "not an address"}])
def test_otherwise_the_null_mailer_stays(env):
    assert isinstance(mailer_from_env(env), NullMailer)


def test_settings_from_env_picks_the_mailer(monkeypatch):
    from veridian_engine.settings import Settings

    monkeypatch.setenv("VERIDIAN_TOKEN", "t")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("VERIDIAN_MAIL_FROM", raising=False)
    assert isinstance(Settings.from_env().mailer, NullMailer)
    monkeypatch.setenv("RESEND_API_KEY", KEY)
    monkeypatch.setenv("VERIDIAN_MAIL_FROM", "seguridad@mail.example.com")
    assert isinstance(Settings.from_env().mailer, ResendMailer)
