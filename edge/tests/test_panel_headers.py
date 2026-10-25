"""Every panel response forbids framing and content sniffing: the panel lifts blocks and issues keys."""

from fastapi.testclient import TestClient

from panel.main import create_app
from tests.panel_support import test_settings


def client(**over):
    return TestClient(create_app("http://engine", "tok", settings=test_settings(**over)))


def test_pages_and_redirects_carry_the_security_headers():
    c = client()
    for path in ("/healthz", "/login", "/"):
        r = c.get(path, follow_redirects=False)
        assert r.headers["x-frame-options"] == "DENY", path
        assert r.headers["content-security-policy"] == "frame-ancestors 'none'", path
        assert r.headers["x-content-type-options"] == "nosniff", path
        assert r.headers["referrer-policy"] == "same-origin", path


def test_unconfigured_panel_still_sends_them():
    r = client(google_client_id="", google_client_secret="").get("/")
    assert r.status_code == 503 and r.headers["x-frame-options"] == "DENY"
