"""Contract 13: the webhook endpoints of a project (panel only, admin+)."""

import json

import pytest
from conftest import AUTH_A, NOW, TEST_OWNER_EMAIL, acting, days_ago, login, make_event, make_settings, post_events
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.control_plane import repository
from veridian_engine.control_plane.legacy import DEMO_ORG_ID
from veridian_engine.notifications.secret_box import KEY_ENV
from veridian_engine.notifications.webhook import verify

URL = "https://hooks.example.com/veridian"
BASE = "/v1/projects/client_a/webhook"
CREATOR = "creator@example.com"


def public(host, port):
    return ["93.184.216.34"]


def private(host, port):
    return ["10.0.0.7"]


class FakeTransport:
    def __init__(self, status: int = 200):
        self.status = status
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return self.status


@pytest.fixture
def transport():
    return FakeTransport()


@pytest.fixture
def secret_key(monkeypatch):
    monkeypatch.setenv(KEY_ENV, Fernet.generate_key().decode())


@pytest.fixture
def client(tmp_path, clock, transport, secret_key):
    settings = make_settings(tmp_path, clock, webhook_transport=transport, webhook_resolver=public,
                             org_creators=frozenset({CREATOR}))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def owner(client):
    return login(client, "sub-test-owner", TEST_OWNER_EMAIL)["person"]["id"]


def member_as(client, role, sub="sub-m", email="m@example.com"):
    owner_id = owner(client)
    with db.connect(client.app.state.settings.db_path) as conn:
        repository.create_invitation(conn, actor_id=owner_id, org_id=DEMO_ORG_ID, email=email, role=role, now=NOW)
    return login(client, sub, email)["person"]["id"]


def audit(client, action):
    with db.connect(client.app.state.settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM audit_log WHERE action = ? ORDER BY seq", (action,)).fetchall()
    return [dict(row) for row in rows]


def configure(client, admin, url=URL):
    response = client.put(BASE, json={"url": url}, headers=acting(admin))
    assert response.status_code == 200, response.text
    return response.json()


def attack(client):
    """Learn the owner, then an attacker hits a critical route from a new network: one block."""
    post_events(client, [make_event(f"h{i}", days_ago(i)) for i in range(1, 4)], headers=AUTH_A)
    hit = make_event("x1", NOW, ip="203.0.113.9", session_id="s_att", method="POST", route="/payouts/bank-account")
    post_events(client, [hit], headers=AUTH_A)


# --- PUT / GET ---


def test_an_admin_sets_the_webhook_and_sees_the_secret_only_once(client):
    admin = member_as(client, "admin")
    created = configure(client, admin)
    assert set(created) == {"url", "enabled", "created_at", "secret"}
    assert created["url"] == URL and created["enabled"] is True
    assert created["secret"].startswith("whsec_")

    shown = client.get(BASE, headers=acting(admin))
    assert shown.status_code == 200
    assert shown.json() == {"webhook": {"url": URL, "enabled": True, "created_at": created["created_at"]}}
    assert created["secret"] not in shown.text

    [row] = audit(client, "webhook.set")
    assert row["actor_person_id"] == admin and row["project_id"] == "client_a"
    assert json.loads(row["meta"]) == {"url": URL}


def test_setting_the_webhook_again_rotates_the_secret(client):
    admin = member_as(client, "admin")
    first = configure(client, admin)
    second = configure(client, admin, url="https://hooks.example.com/other")
    assert second["secret"] != first["secret"]
    assert client.get(BASE, headers=acting(admin)).json()["webhook"]["url"] == "https://hooks.example.com/other"


def test_without_a_webhook_get_answers_null(client):
    admin = member_as(client, "admin")
    response = client.get(BASE, headers=acting(admin))
    assert response.status_code == 200 and response.json() == {"webhook": None}


@pytest.mark.parametrize("url", ["http://hooks.example.com/v", "https://user:pw@hooks.example.com/v",
                                 "https://10.0.0.1/v", "not a url"])
def test_an_unsafe_url_is_refused_and_nothing_is_stored(client, url):
    admin = member_as(client, "admin")
    response = client.put(BASE, json={"url": url}, headers=acting(admin))
    assert response.status_code == 422
    assert response.json()["error"] == "unsafe_url"
    assert client.get(BASE, headers=acting(admin)).json() == {"webhook": None}
    assert audit(client, "webhook.set") == []


def test_a_host_that_resolves_to_a_private_address_is_refused(tmp_path, clock, transport, secret_key):
    settings = make_settings(tmp_path, clock, webhook_transport=transport, webhook_resolver=private)
    with TestClient(create_app(settings)) as c:
        admin = member_as(c, "admin")
        response = c.put(BASE, json={"url": URL}, headers=acting(admin))
        assert response.status_code == 422 and response.json()["error"] == "unsafe_url"


@pytest.mark.parametrize("body", [{}, {"url": ""}, {"url": "https://h.example.com/" + "x" * 2048}])
def test_put_validates_the_body(client, body):
    admin = member_as(client, "admin")
    assert client.put(BASE, json=body, headers=acting(admin)).status_code == 422


def test_without_the_secret_key_put_answers_503_and_stores_nothing(client, monkeypatch):
    monkeypatch.delenv(KEY_ENV)
    admin = member_as(client, "admin")
    response = client.put(BASE, json={"url": URL}, headers=acting(admin))
    assert response.status_code == 503 and response.json() == {"error": "secret_key_missing"}
    assert client.get(BASE, headers=acting(admin)).json() == {"webhook": None}


# --- PATCH / DELETE ---


def test_an_admin_disables_and_enables_the_webhook(client):
    admin = member_as(client, "admin")
    configure(client, admin)
    off = client.patch(BASE, json={"enabled": False}, headers=acting(admin))
    assert off.status_code == 200 and off.json()["webhook"]["enabled"] is False
    attack(client)
    deliveries = client.get(f"{BASE}/deliveries", headers=acting(admin)).json()["deliveries"]
    assert deliveries == []
    on = client.patch(BASE, json={"enabled": True}, headers=acting(admin))
    assert on.json()["webhook"]["enabled"] is True
    assert [json.loads(r["meta"]) for r in audit(client, "webhook.enable")] == [{"enabled": False}, {"enabled": True}]


def test_patch_without_a_webhook_is_404(client):
    admin = member_as(client, "admin")
    response = client.patch(BASE, json={"enabled": False}, headers=acting(admin))
    assert response.status_code == 404 and response.json() == {"error": "not_found"}
    assert client.patch(BASE, json={}, headers=acting(admin)).status_code == 422


def test_an_admin_removes_the_webhook(client):
    admin = member_as(client, "admin")
    configure(client, admin)
    response = client.delete(BASE, headers=acting(admin))
    assert response.status_code == 204
    assert client.get(BASE, headers=acting(admin)).json() == {"webhook": None}
    assert len(audit(client, "webhook.delete")) == 1
    again = client.delete(BASE, headers=acting(admin))
    assert again.status_code == 404 and again.json() == {"error": "not_found"}


# --- deliveries ---


def test_deliveries_list_the_queued_block_notifications(client):
    admin = member_as(client, "admin")
    configure(client, admin)
    attack(client)
    response = client.get(f"{BASE}/deliveries", headers=acting(admin))
    assert response.status_code == 200
    [delivery] = response.json()["deliveries"]
    assert set(delivery) == {"id", "kind", "decision_id", "status", "attempts", "next_attempt_at", "last_error",
                             "created_at", "updated_at"}
    assert delivery["kind"] == "webhook" and delivery["status"] == "pending"


def test_deliveries_are_scoped_to_the_project_in_the_path(client):
    admin = member_as(client, "admin")
    configure(client, admin)
    attack(client)
    other = client.get("/v1/projects/client_b/webhook/deliveries", headers=acting(admin))
    assert other.status_code == 200 and other.json() == {"deliveries": []}


@pytest.mark.parametrize("limit", [0, 201, "x"])
def test_deliveries_validate_the_limit(client, limit):
    admin = member_as(client, "admin")
    assert client.get(f"{BASE}/deliveries?limit={limit}", headers=acting(admin)).status_code == 422


# --- test send ---


def test_the_test_send_posts_a_signed_test_event(client, transport, clock):
    admin = member_as(client, "admin")
    secret = configure(client, admin)["secret"]
    response = client.post(f"{BASE}/test", headers=acting(admin))
    assert response.status_code == 200
    assert response.json() == {"delivered": True, "status_code": 200, "error": None}

    [request] = transport.requests
    assert request.url == URL
    assert request.headers["X-Veridian-Event"] == "webhook.test"
    assert verify(secret, request.headers, request.body, clock())
    body = json.loads(request.body)
    assert body["type"] == "webhook.test" and body["project"]["id"] == "client_a"
    assert body["id"] == request.headers["X-Veridian-Delivery"]
    assert len(audit(client, "webhook.test")) == 1


def test_a_failing_receiver_is_reported_not_raised(client, transport):
    transport.status = 500
    admin = member_as(client, "admin")
    configure(client, admin)
    response = client.post(f"{BASE}/test", headers=acting(admin))
    assert response.status_code == 200
    assert response.json() == {"delivered": False, "status_code": 500, "error": "HTTP 500"}


def test_the_test_send_is_not_queued_as_a_delivery(client):
    admin = member_as(client, "admin")
    configure(client, admin)
    client.post(f"{BASE}/test", headers=acting(admin))
    assert client.get(f"{BASE}/deliveries", headers=acting(admin)).json() == {"deliveries": []}


def test_the_test_send_without_a_webhook_is_404(client, transport):
    admin = member_as(client, "admin")
    response = client.post(f"{BASE}/test", headers=acting(admin))
    assert response.status_code == 404 and response.json() == {"error": "not_found"}
    assert transport.requests == []


def test_the_test_send_without_the_secret_key_answers_503(client, transport, monkeypatch):
    admin = member_as(client, "admin")
    configure(client, admin)
    monkeypatch.delenv(KEY_ENV)
    response = client.post(f"{BASE}/test", headers=acting(admin))
    assert response.status_code == 503 and response.json() == {"error": "secret_key_missing"}
    assert transport.requests == []


# --- who may call what ---

CALLS = [("get", BASE, None), ("put", BASE, {"url": URL}), ("patch", BASE, {"enabled": False}),
         ("delete", BASE, None), ("get", f"{BASE}/deliveries", None), ("post", f"{BASE}/test", None)]


@pytest.mark.parametrize("method,path,body", CALLS)
def test_a_viewer_cannot_use_the_webhook_endpoints(client, method, path, body):
    viewer = member_as(client, "viewer")
    kwargs = {"json": body} if body is not None else {}
    assert client.request(method, path, headers=acting(viewer), **kwargs).status_code == 403


@pytest.mark.parametrize("method,path,body", CALLS)
def test_the_sdk_key_cannot_use_the_webhook_endpoints(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert client.request(method, path, headers=AUTH_A, **kwargs).status_code == 403


def test_an_admin_of_another_org_cannot_touch_this_webhook(client):
    creator = login(client, "sub-creator", CREATOR)["person"]["id"]
    assert client.get(BASE, headers=acting(creator)).status_code == 403
    assert client.put(BASE, json={"url": URL}, headers=acting(creator)).status_code == 403
