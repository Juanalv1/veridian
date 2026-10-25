"""T2: brand and self-unlock settings of a project, read and changed on the project endpoints."""

import json

import pytest
from conftest import NOW, TEST_OWNER_EMAIL, acting, login, make_settings
from fastapi.testclient import TestClient

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.control_plane import repository
from veridian_engine.control_plane.legacy import DEMO_ORG_ID

PUBLIC_IP = "93.184.216.34"


def resolver(host, port):
    return ["10.0.0.5"] if host.startswith("internal") else [PUBLIC_IP]


@pytest.fixture
def client(tmp_path, clock):
    with TestClient(create_app(make_settings(tmp_path, clock, webhook_resolver=resolver))) as test_client:
        yield test_client


def owner(client):
    return login(client, "sub-test-owner", TEST_OWNER_EMAIL)["person"]["id"]


def member_as(client, role, sub="sub-m", email="m@example.com"):
    with db.connect(client.app.state.settings.db_path) as conn:
        repository.create_invitation(conn, actor_id=owner(client), org_id=DEMO_ORG_ID, email=email, role=role, now=NOW)
    return login(client, sub, email)["person"]["id"]


def patch(client, person_id, **body):
    return client.patch("/v1/projects/client_a", json=body, headers=acting(person_id))


BRAND = {
    "brand_name": "Acme",
    "brand_logo_url": "https://cdn.acme.example/logo.png",
    "brand_color": "#1F6FEB",
    "brand_support_url": "https://acme.example/ayuda",
    "brand_support_email": "soporte@acme.example",
    "self_unlock_enabled": True,
}


def test_a_project_starts_without_brand_and_with_self_unlock_off(client):
    project = client.get("/v1/projects/client_a", headers=acting(owner(client))).json()
    assert {key: project[key] for key in BRAND} == {
        "brand_name": None, "brand_logo_url": None, "brand_color": None, "brand_support_url": None,
        "brand_support_email": None, "self_unlock_enabled": False}


def test_an_admin_sets_the_brand_and_the_next_read_returns_it(client):
    admin = member_as(client, "admin")
    response = patch(client, admin, **BRAND)
    assert response.status_code == 200, response.text
    assert {key: response.json()[key] for key in BRAND} == BRAND
    assert client.get("/v1/projects/client_a", headers=acting(admin)).json()["brand_color"] == "#1F6FEB"


def test_only_the_sent_fields_change_and_null_or_blank_clears_a_brand_field(client):
    admin = member_as(client, "admin")
    patch(client, admin, **BRAND)
    response = patch(client, admin, brand_name="Acme Pay", brand_logo_url=None, brand_support_url="  ")
    body = response.json()
    assert body["brand_name"] == "Acme Pay"
    assert body["brand_logo_url"] is None and body["brand_support_url"] is None
    assert body["brand_color"] == "#1F6FEB" and body["self_unlock_enabled"] is True


def test_a_viewer_cannot_change_the_brand(client):
    viewer = member_as(client, "viewer")
    assert patch(client, viewer, brand_name="Acme").status_code == 403
    assert client.get("/v1/projects/client_a", headers=acting(owner(client))).json()["brand_name"] is None


@pytest.mark.parametrize("url", ["http://cdn.acme.example/logo.png", "https://internal.acme.example/logo.png",
                                 "https://127.0.0.1/logo.png", "https://user:pw@cdn.acme.example/logo.png",
                                 "javascript:alert(1)", "https://"])
def test_an_unsafe_logo_url_is_rejected_like_a_webhook_url(client, url):
    response = patch(client, owner(client), brand_logo_url=url)
    assert response.status_code == 422
    assert response.json()["error"] == "unsafe_url"
    assert client.get("/v1/projects/client_a", headers=acting(owner(client))).json()["brand_logo_url"] is None


@pytest.mark.parametrize("field,value", [("brand_color", "red"), ("brand_color", "#12345"), ("brand_color", "#GGGGGG"),
                                         ("brand_color", "#fff; background:url(x)"),
                                         ("brand_support_url", "http://acme.example"),
                                         ("brand_support_url", "javascript:alert(1)"),
                                         ("brand_support_email", "not-an-email"), ("brand_name", "x" * 81)])
def test_invalid_brand_values_are_rejected(client, field, value):
    assert patch(client, owner(client), **{field: value}).status_code == 422


def test_a_brand_change_is_audited_like_other_project_settings(client):
    patch(client, owner(client), **BRAND)
    with db.connect(client.app.state.settings.db_path) as conn:
        [row] = conn.execute("SELECT * FROM audit_log WHERE action = 'project.update'").fetchall()
    assert row["project_id"] == "client_a" and json.loads(row["meta"])["self_unlock_enabled"] is True
