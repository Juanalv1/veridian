"""/healthz: Render's health check. It answers 200 even when the panel is not configured, so a missing
secret shows as a 503 on the pages instead of failing every deploy, and it reveals nothing."""

from fastapi.testclient import TestClient

from panel.main import create_app
from tests.panel_support import test_settings


def client(**over):
    return TestClient(create_app("http://engine", "tok", settings=test_settings(**over)))


def test_healthz_is_ok_when_configured():
    r = client().get("/healthz")
    assert r.status_code == 200 and r.text == "ok"


def test_healthz_is_ok_without_configuration_while_the_pages_stay_closed():
    c = client(google_client_id="", google_client_secret="")
    assert c.get("/healthz").status_code == 200
    assert c.get("/").status_code == 503


def test_healthz_needs_no_session_and_is_never_cached():
    r = client().get("/healthz", follow_redirects=False)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
