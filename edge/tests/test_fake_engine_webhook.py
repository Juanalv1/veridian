"""Fake engine: the project webhook (contract 13). Panel only, admin or owner; the signing secret leaves the engine
once (the PUT); unsafe URLs are refused without storing anything; a block in a project with an enabled webhook
records a delivery; the test send is simulated (no network) and every change is audited without the secret."""

import re

import pytest
from fastapi.testclient import TestClient

from fake_engine import create_app
from tests.test_fake_engine_accounts import FUTURE, OWNER, PANEL, SDK, SDK_B, acting, bearer, login, org_of, project_of

URL = "https://example.com/veridian/webhook"
SEED = [{"user_id": "u_42", "ips": ["190.202.14.7"], "routes": ["GET /invoices"]}]
ATTACK = {"id": "e_atk", "ts": "2026-10-26T10:14:03Z", "user_id": "u_42", "session_id": "s_atk", "ip": "203.0.113.9",
          "method": "POST", "route": "/payouts/bank-account", "status": 200, "bytes": 1}


@pytest.fixture
def engine():
    return create_app(tokens={SDK, SDK_B}, seed=SEED, learn_delay=0, panel_tokens={PANEL}, bootstrap_owners={OWNER})


@pytest.fixture
def client(engine):
    return TestClient(engine)


@pytest.fixture
def pid(engine):
    return project_of(engine, SDK)


@pytest.fixture
def owner(client):
    return login(client, sub="s-own", email=OWNER, name="Olga")


def person_in(client, engine, role, sub):
    engine.state.orgs[org_of(engine, SDK)].invite(f"{sub}@example.com", role, FUTURE)
    return login(client, sub=sub, email=f"{sub}@example.com", name=sub.capitalize())


def base(pid):
    return f"/v1/projects/{pid}/webhook"


def put(client, person, pid, url=URL):
    return client.put(base(pid), json={"url": url}, headers=acting(person))


def test_no_webhook_reads_null(client, pid, owner):
    r = client.get(base(pid), headers=acting(owner))
    assert r.status_code == 200 and r.json() == {"webhook": None}


def test_put_creates_enabled_and_returns_the_secret_once(client, pid, owner):
    r = put(client, owner, pid)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"url", "enabled", "created_at", "secret"} and body["url"] == URL and body["enabled"] is True
    assert re.fullmatch(r"whsec_[A-Za-z0-9_-]{20,}", body["secret"])
    got = client.get(base(pid), headers=acting(owner)).json()["webhook"]
    assert set(got) == {"url", "enabled", "created_at"} and body["secret"] not in str(got)


def test_put_again_replaces_the_url_and_the_secret(client, pid, owner):
    first = put(client, owner, pid).json()
    second = put(client, owner, pid, "https://example.org/hook").json()
    assert second["secret"] != first["secret"] and second["url"] == "https://example.org/hook"


@pytest.mark.parametrize("url", ["http://example.com/hook", "https://user:pw@example.com/hook", "https://localhost/hook",
                                 "https://printer.local/hook", "https://127.0.0.1/hook", "https://10.0.0.5/hook",
                                 "https://192.168.1.1/hook", "https://169.254.169.254/latest", "https://[::1]/hook",
                                 "https://example.com/" + "x" * 2048, "not a url", "https:///nohost"])
def test_unsafe_urls_are_refused_and_nothing_is_stored(client, pid, owner, url):
    r = put(client, owner, pid, url)
    assert r.status_code == 422 and r.json()["error"] == "unsafe_url" and r.json()["detail"]
    assert client.get(base(pid), headers=acting(owner)).json() == {"webhook": None}


def test_patch_toggles_and_404_without_webhook(client, pid, owner):
    assert client.patch(base(pid), json={"enabled": False}, headers=acting(owner)).json() == {"error": "not_found"}
    put(client, owner, pid)
    r = client.patch(base(pid), json={"enabled": False}, headers=acting(owner))
    assert r.status_code == 200 and r.json()["webhook"]["enabled"] is False and "secret" not in r.json()["webhook"]


def test_delete_is_204_then_404(client, pid, owner):
    put(client, owner, pid)
    assert client.delete(base(pid), headers=acting(owner)).status_code == 204
    r = client.delete(base(pid), headers=acting(owner))
    assert r.status_code == 404 and r.json() == {"error": "not_found"}
    assert client.get(base(pid), headers=acting(owner)).json() == {"webhook": None}


def test_the_test_send_is_simulated_overridable_and_not_a_delivery(client, engine, pid, owner):
    assert client.post(f"{base(pid)}/test", headers=acting(owner)).status_code == 404
    put(client, owner, pid)
    r = client.post(f"{base(pid)}/test", headers=acting(owner))
    assert r.status_code == 200 and r.json() == {"delivered": True, "status_code": 200, "error": None}
    engine.state.webhook_test_result = {"delivered": False, "status_code": None, "error": "timeout"}
    assert client.post(f"{base(pid)}/test", headers=acting(owner)).json()["error"] == "timeout"
    assert client.get(f"{base(pid)}/deliveries", headers=acting(owner)).json() == {"deliveries": []}


def test_a_block_with_an_enabled_webhook_records_a_delivery(client, pid, owner):
    put(client, owner, pid)
    assert client.post("/v1/check", json={"event": ATTACK}, headers=bearer(SDK)).json()["verdict"] == "block"
    rows = client.get(f"{base(pid)}/deliveries", headers=acting(owner)).json()["deliveries"]
    assert len(rows) == 1
    d = rows[0]
    assert set(d) == {"id", "kind", "decision_id", "status", "attempts", "next_attempt_at", "last_error",
                      "created_at", "updated_at"}
    assert d["kind"] == "block" and d["decision_id"] == "d_1" and d["status"] == "delivered" and d["attempts"] == 1


def test_a_block_with_a_paused_webhook_records_nothing(client, pid, owner):
    put(client, owner, pid)
    client.patch(base(pid), json={"enabled": False}, headers=acting(owner))
    client.post("/v1/check", json={"event": ATTACK}, headers=bearer(SDK))
    assert client.get(f"{base(pid)}/deliveries", headers=acting(owner)).json() == {"deliveries": []}


def test_deliveries_limit_is_bounded(client, pid, owner):
    for limit in (0, 201):
        assert client.get(f"{base(pid)}/deliveries", params={"limit": limit}, headers=acting(owner)).status_code == 422


def test_a_viewer_and_an_sdk_key_are_refused(client, engine, pid, owner):
    viewer = person_in(client, engine, "viewer", "vie")
    for method, path, body in (("get", base(pid), None), ("put", base(pid), {"url": URL}),
                               ("patch", base(pid), {"enabled": True}), ("delete", base(pid), None),
                               ("get", f"{base(pid)}/deliveries", None), ("post", f"{base(pid)}/test", None)):
        kw = {"json": body} if body is not None else {}
        r = client.request(method.upper(), path, headers=acting(viewer), **kw)
        assert r.status_code == 403 and r.json() == {"error": "forbidden"}, (method, path)
        assert client.request(method.upper(), path, headers=bearer(SDK), **kw).status_code in (401, 403)
    admin = person_in(client, engine, "admin", "adm")
    assert put(client, admin, pid).status_code == 200


def test_another_projects_webhook_is_out_of_reach(client, engine, owner):
    other = project_of(engine, SDK_B)
    assert put(client, owner, project_of(engine, SDK)).status_code == 200
    # Olga owns both seeded orgs as bootstrap owner; the webhook of A is not B's.
    assert client.get(base(other), headers=acting(owner)).json() == {"webhook": None}


def test_changes_are_audited_without_the_secret(client, engine, pid, owner):
    secret = put(client, owner, pid).json()["secret"]
    client.patch(base(pid), json={"enabled": False}, headers=acting(owner))
    client.post(f"{base(pid)}/test", headers=acting(owner))
    client.delete(base(pid), headers=acting(owner))
    entries = client.get(f"/v1/orgs/{org_of(engine, SDK)}/audit", headers=acting(owner)).json()["entries"]
    actions = [e["action"] for e in entries if e["action"].startswith("webhook.")]
    assert actions == ["webhook.delete", "webhook.test", "webhook.enable", "webhook.set"]
    assert next(e for e in entries if e["action"] == "webhook.set")["meta"] == {"url": URL}
    assert secret not in str(entries)
