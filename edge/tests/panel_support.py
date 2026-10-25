"""Shared helpers: a panel logged in through a fake Google and a fake engine.

`stub_client` answers `POST /v1/people/login` itself and rejects any other call that does not carry
the panel's service credential, the actor and the project, so every canned-engine test also checks
what the panel sends.
"""

import base64
import inspect
import json
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from panel.config import Settings
from panel.main import create_app

PANEL_TOKEN = "tok-panel"
SESSION_SECRET = "s" * 32
PERSON_ID = "per_stub"
PROJECT_ID = "p_stub"
ORG_ID = "org_stub"
EMAIL = "ana@example.com"


def test_settings(**over) -> Settings:
    base = dict(google_client_id="gid", google_client_secret="gsecret", session_secret=SESSION_SECRET,
                public_url="", engine_url="http://engine", panel_token=PANEL_TOKEN)
    base.update(over)
    return Settings(**base)


test_settings.__test__ = False  # not a pytest test


def google_transport(sub="g-1", email=EMAIL, name="Ana", verified=True, token_status=200):
    """Fake accounts.google.com / openidconnect.googleapis.com. A `code` of the form `sub:<x>` signs in as `<x>`."""
    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "boom"})
            code = parse_qs(request.content.decode()).get("code", [""])[0]
            return httpx.Response(200, json={"access_token": f"at-{code}"})
        code = request.headers["authorization"].removeprefix("Bearer at-")
        who = code.removeprefix("sub:") if code.startswith("sub:") else sub
        profile = {"sub": who, "email": email, "name": name, "picture": "", "email_verified": verified}
        return httpx.Response(200, json=profile)
    return httpx.MockTransport(handler)


def login_answer(projects=((PROJECT_ID, "Demo", "owner"),), person=PERSON_ID, email=EMAIL, orgs=None):
    """The login answer. `projects` are (id, name, role) triples placed in one org; `orgs` overrides it
    with a list of (org_id, role, [(project_id, name), ...])."""
    if orgs is None:
        orgs = [(ORG_ID, projects[0][2], [(i, n) for i, n, _ in projects])] if projects else []
    return {"person": {"id": person, "email": email, "name": "Ana"},
            "orgs": [{"id": o, "name": o, "role": r, "projects": [{"id": i, "name": n} for i, n in ps]}
                     for o, r, ps in orgs]}


def stub_client(handler, projects=((PROJECT_ID, "Demo", "owner"),), logins=None, orgs=None):
    """AsyncClient over a canned engine. Login is answered here, not by `handler`."""
    async def wrapper(request):
        if request.url.path == "/v1/people/login":
            if logins is not None:
                logins.append(request)
            return httpx.Response(200, json=login_answer(projects, orgs=orgs))
        h = request.headers
        if (h.get("authorization") != f"Bearer {PANEL_TOKEN}" or h.get("x-veridian-actor") != PERSON_ID
                or h.get("x-veridian-project") != PROJECT_ID):
            return httpx.Response(403, json={"error": "forbidden"})
        out = handler(request)
        return await out if inspect.isawaitable(out) else out
    return httpx.AsyncClient(transport=httpx.MockTransport(wrapper))


def session_of(client, secret=SESSION_SECRET) -> dict:
    cookie = next((c.value for c in client.cookies.jar if c.name == "session"), None)
    if cookie is None:
        return {}
    data = TimestampSigner(secret).unsign(cookie.encode())
    return json.loads(base64.b64decode(data))


class PanelClient(TestClient):
    """A TestClient whose POSTs carry the panel's CSRF defences, unless `csrf=False`."""

    secret = SESSION_SECRET
    origin = "http://testserver"

    def post(self, url, *args, csrf=True, **kwargs):
        if csrf:
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("Origin", self.origin)
            headers.setdefault("X-CSRF-Token", session_of(self, self.secret).get("csrf", ""))
            kwargs["headers"] = headers
        return super().post(url, *args, **kwargs)


def sign_in(client, code="c", **params):
    """Run /auth/google then /auth/callback; returns the callback response."""
    r = client.get("/auth/google")
    got = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    return client.get("/auth/callback", params={"code": code, "state": got, **params})


def make_panel(http_client, *, settings=None, token=PANEL_TOKEN, login=True, transport=None, cookies=None, **kw):
    settings = settings or test_settings()
    app = create_app("http://engine", token, http_client=http_client, settings=settings,
                     google_transport=transport or google_transport(), **kw)
    client = PanelClient(app, follow_redirects=False, cookies=cookies,
                         base_url=settings.public_url or "http://testserver")
    client.secret, client.origin = settings.session_secret, settings.public_url or "http://testserver"
    if login:
        r = sign_in(client)
        assert r.status_code == 303, r.text
    return client


def engine_panel_headers(engine_app, email=EMAIL, sub="g-1"):
    """Headers a panel would send to a real fake_engine app for the person `sub` (logs them in)."""
    body = {"google_sub": sub, "email": email, "name": "Ana"}
    c = TestClient(engine_app)
    r = c.post("/v1/people/login", json=body, headers={"Authorization": f"Bearer {PANEL_TOKEN}"}).json()
    return {"Authorization": f"Bearer {PANEL_TOKEN}", "X-Veridian-Actor": r["person"]["id"],
            "X-Veridian-Project": r["orgs"][0]["projects"][0]["id"]}
