"""Fake engine: self-unlock by email (docs/DESBLOQUEO-POR-CORREO.md). Brand fields on the project, `user_email`
in events and check, the SDK signals (`self_unlock_sessions`, `self_unlock`) and the public token endpoints.
Same states and errors as the real engine; the mail itself is simulated (an in-memory outbox)."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from fake_engine import create_app
from tests.test_fake_engine_accounts import FUTURE, OWNER, PANEL, SDK, SDK_B, acting, bearer, login, org_of, project_of

SEED = [{"user_id": "u_42", "ips": ["190.202.14.7"], "routes": ["GET /invoices"]}]
EMAIL = "ana@example.com"


def attack(n=1, email=EMAIL, ip="203.0.113.9", **over):
    event = {"id": f"e_atk{n}", "ts": "2026-10-26T10:14:03Z", "user_id": "u_42", "session_id": f"s_atk{n}", "ip": ip,
             "user_agent": "Chrome 141 / Windows", "method": "POST", "route": "/payouts/bank-account",
             "status": 200, "bytes": 1}
    if email is not None:
        event["user_email"] = email
    return {**event, **over}


@pytest.fixture
def now():
    return {"t": 1000.0}


@pytest.fixture
def engine(now):
    return create_app(tokens={SDK, SDK_B}, seed=SEED, learn_delay=0, panel_tokens={PANEL}, bootstrap_owners={OWNER},
                      clock=lambda: now["t"])


@pytest.fixture
def client(engine):
    return TestClient(engine)


@pytest.fixture
def pid(engine):
    return project_of(engine, SDK)


@pytest.fixture
def owner(client):
    return login(client, sub="s-own", email=OWNER, name="Olga")


def patch(client, person, pid, **body):
    return client.patch(f"/v1/projects/{pid}", json=body, headers=acting(person))


def enable(client, owner, pid, **brand):
    r = patch(client, owner, pid, self_unlock_enabled=True, brand_name="Acme", **brand)
    assert r.status_code == 200, r.text
    return r.json()


def check(client, event, token=SDK):
    return client.post("/v1/check", json={"event": event}, headers=bearer(token))


def blocklist(client, token=SDK):
    return client.get("/v1/blocklist", headers=bearer(token)).json()


def unlock_token(engine):
    """The plain token of the newest simulated email."""
    return engine.state.outbox[-1]["token"]


def page(client, token, method="get", auth=None):
    headers = {**bearer(PANEL), "X-Veridian-Unlock-Token": token} if auth is None else auth
    return getattr(client, method)("/v1/self-unlock", headers=headers)


def blocked(client, engine, owner, pid):
    enable(client, owner, pid)
    r = check(client, attack())
    assert r.json()["verdict"] == "block"
    return r.json()["decision_id"]


# --- project brand fields -----------------------------------------------------------------------------------------


def test_a_project_starts_without_brand_and_with_the_feature_off(client, owner, pid):
    p = client.get(f"/v1/projects/{pid}", headers=acting(owner)).json()
    for key in ("brand_name", "brand_logo_url", "brand_color", "brand_support_url", "brand_support_email"):
        assert p[key] is None
    assert p["self_unlock_enabled"] is False


def test_patch_round_trips_the_brand_and_changes_only_what_was_sent(client, owner, pid):
    r = patch(client, owner, pid, brand_name=" Acme ", brand_logo_url="https://cdn.acme.com/logo.png",
              brand_color="#1A2B3C", brand_support_url="https://acme.com/help", brand_support_email="help@acme.com",
              self_unlock_enabled=True)
    assert r.status_code == 200
    p = client.get(f"/v1/projects/{pid}", headers=acting(owner)).json()
    assert (p["brand_name"], p["brand_color"], p["self_unlock_enabled"]) == ("Acme", "#1A2B3C", True)
    assert p["brand_logo_url"] == "https://cdn.acme.com/logo.png" and p["brand_support_email"] == "help@acme.com"
    r = patch(client, owner, pid, brand_color="#FFFFFF")
    assert r.json()["brand_name"] == "Acme" and r.json()["brand_color"] == "#FFFFFF" and r.json()["self_unlock_enabled"]
    listed = client.get(f"/v1/orgs/{org_of(client.app, SDK)}/projects", headers=acting(owner)).json()["projects"]
    assert listed[0]["brand_name"] == "Acme" and listed[0]["self_unlock_enabled"] is True


def test_null_or_blank_clears_a_brand_field(client, owner, pid):
    patch(client, owner, pid, brand_name="Acme", brand_color="#1A2B3C", brand_support_url="https://acme.com/h")
    r = patch(client, owner, pid, brand_name=None, brand_color="", brand_support_url="  ")
    assert r.status_code == 200
    assert r.json()["brand_name"] is None and r.json()["brand_color"] is None and r.json()["brand_support_url"] is None


@pytest.mark.parametrize("body", [
    {"brand_name": "x" * 81}, {"brand_color": "blue"}, {"brand_color": "#12345"}, {"brand_color": "#GGGGGG"},
    {"brand_support_url": "http://acme.com"}, {"brand_support_email": "not-an-email"},
])
def test_invalid_brand_values_are_422_and_change_nothing(client, owner, pid, body):
    assert patch(client, owner, pid, **body).status_code == 422
    assert client.get(f"/v1/projects/{pid}", headers=acting(owner)).json()["brand_name"] is None


@pytest.mark.parametrize("url", [
    "http://cdn.acme.com/l.png", "https://localhost/l.png", "https://127.0.0.1/l.png", "https://10.0.0.5/l.png",
    "https://192.168.1.2/l.png", "https://169.254.169.254/l.png", "https://user:pw@acme.com/l.png", "ftp://acme.com/l.png",
])
def test_an_unsafe_logo_url_is_refused(client, owner, pid, url):
    r = patch(client, owner, pid, brand_logo_url=url)
    assert r.status_code == 422 and r.json()["error"] == "unsafe_url"
    assert client.get(f"/v1/projects/{pid}", headers=acting(owner)).json()["brand_logo_url"] is None


def test_a_viewer_cannot_change_the_brand_but_can_read_it(client, engine, owner, pid):
    patch(client, owner, pid, brand_name="Acme")
    engine.state.orgs[org_of(engine, SDK)].invite("v@example.com", "viewer", FUTURE)
    viewer = login(client, sub="s-v", email="v@example.com")
    assert patch(client, viewer, pid, brand_name="Hacked").status_code == 403
    assert client.get(f"/v1/projects/{pid}", headers=acting(viewer)).json()["brand_name"] == "Acme"


def test_a_brand_change_is_audited_as_project_update(client, engine, owner, pid):
    patch(client, owner, pid, brand_name="Acme")
    entries = client.get(f"/v1/orgs/{org_of(engine, SDK)}/audit", headers=acting(owner)).json()["entries"]
    assert entries[0]["action"] == "project.update" and entries[0]["project_id"] == pid


# --- user_email, the SDK signals and the simulated mail ---------------------------------------------------------


def test_check_signals_self_unlock_only_when_the_user_gets_the_email(client, engine, owner, pid):
    enable(client, owner, pid)
    r = check(client, attack(1)).json()
    assert r["verdict"] == "block" and r["self_unlock"] is True
    assert [m["to"] for m in engine.state.outbox] == [EMAIL]


def test_without_email_or_with_the_feature_off_there_is_no_signal_and_no_mail(client, engine, owner, pid):
    r = check(client, attack(1, email=None)).json()
    assert r["verdict"] == "block" and "self_unlock" not in r
    assert check(client, attack(2)).json().get("self_unlock") is None  # feature still off
    assert engine.state.outbox == []
    assert "self_unlock_sessions" not in blocklist(client)


def test_blocklist_lists_the_sessions_that_get_the_email(client, engine, owner, pid):
    enable(client, owner, pid)
    check(client, attack(1))
    assert blocklist(client)["self_unlock_sessions"] == ["s_atk1"]


def test_the_last_user_email_wins(client, engine, owner, pid):
    enable(client, owner, pid)
    client.post("/v1/events", json={"events": [attack(9, ip="190.202.14.7", route="/invoices", method="GET",
                                                        user_email="old@example.com", session_id="s_ok")]},
                headers=bearer(SDK))
    client.post("/v1/events", json={"events": [attack(8, ip="190.202.14.7", route="/invoices", method="GET",
                                                        user_email="new@example.com", session_id="s_ok")]},
                headers=bearer(SDK))
    check(client, attack(1, email=None))
    assert [m["to"] for m in engine.state.outbox] == ["new@example.com"]


def test_two_blocks_of_the_same_user_within_15_minutes_send_one_mail(client, engine, owner, pid, now):
    enable(client, owner, pid)
    check(client, attack(1))
    check(client, attack(2))
    assert len(engine.state.outbox) == 1
    now["t"] += 15 * 60
    check(client, attack(3))
    assert len(engine.state.outbox) == 2


def test_the_simulated_mail_keeps_only_the_hash_of_the_token_in_the_registry(client, engine, owner, pid):
    enable(client, owner, pid)
    check(client, attack())
    token = unlock_token(engine)
    assert hashlib.sha256(token.encode()).hexdigest() in engine.state.unlock_tokens
    assert token not in engine.state.unlock_tokens


# --- the public token endpoints -----------------------------------------------------------------------------------


def test_get_shows_the_page_data_and_never_unlocks(client, engine, owner, pid):
    enable(client, owner, pid, brand_color="#1A2B3C")
    d = check(client, attack()).json()["decision_id"]
    version = blocklist(client)["version"]
    r = page(client, unlock_token(engine))
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "valid" and body["brand"]["name"] == "Acme" and body["brand"]["color"] == "#1A2B3C"
    assert body["access"] == {"time": "26 de octubre de 2026, 10:14 UTC", "device": "Chrome en Windows"}
    assert "expires_at" in body
    for _ in range(3):
        page(client, unlock_token(engine))
    assert blocklist(client)["version"] == version and blocklist(client)["sessions"] == ["s_atk1"]
    decision = next(x for x in client.get("/v1/decisions", headers=acting(owner, pid)).json()["decisions"] if x["id"] == d)
    assert decision["status"] == "open"
    assert not any(e["action"] == "decision.self_unlocked" for e in
                   client.get(f"/v1/orgs/{org_of(engine, SDK)}/audit", headers=acting(owner)).json()["entries"])


def test_the_page_data_has_no_ip_user_or_email(client, engine, owner, pid):
    enable(client, owner, pid)
    check(client, attack())
    raw = page(client, unlock_token(engine)).text
    for secret in ("203.0.113.9", "u_42", EMAIL, "s_atk1"):
        assert secret not in raw


def test_the_brand_name_falls_back_to_the_project_name(client, engine, owner, pid):
    patch(client, owner, pid, self_unlock_enabled=True)
    check(client, attack())
    brand = page(client, unlock_token(engine)).json()["brand"]
    assert brand["name"] == "Demo 1" and brand["logo_url"] is None and brand["support_email"] is None


def test_post_unlocks_bumps_the_version_and_audits_it(client, engine, owner, pid):
    d = blocked(client, engine, owner, pid)
    before = blocklist(client)["version"]
    r = page(client, unlock_token(engine), "post")
    assert r.status_code == 200 and r.json()["unlocked"] is True and r.json()["brand"]["name"] == "Acme"
    after = blocklist(client)
    assert after["version"] == before + 1 and after["sessions"] == [] and after["ips"] == []
    assert "self_unlock_sessions" not in after or after["self_unlock_sessions"] == []
    decision = next(x for x in client.get("/v1/decisions", headers=acting(owner, pid)).json()["decisions"] if x["id"] == d)
    assert decision["status"] == "marked_normal"
    entry = next(e for e in client.get(f"/v1/orgs/{org_of(engine, SDK)}/audit", headers=acting(owner)).json()["entries"]
                 if e["action"] == "decision.self_unlocked")
    assert entry["actor_kind"] == "system" and entry["actor_person_id"] is None and entry["actor_name"] is None
    assert entry["target_type"] == "decision" and entry["target_id"] == d and entry["meta"] == {"via": "email"}


def test_a_token_is_single_use(client, engine, owner, pid):
    blocked(client, engine, owner, pid)
    token = unlock_token(engine)
    assert page(client, token, "post").status_code == 200
    version = blocklist(client)["version"]
    r = page(client, token, "post")
    assert r.status_code == 409 and r.json() == {"error": "used"}
    assert page(client, token).json()["state"] == "used"
    assert blocklist(client)["version"] == version


def test_an_expired_token_does_not_unlock(client, engine, owner, pid):
    blocked(client, engine, owner, pid)
    token = unlock_token(engine)
    engine.state.unlock_tokens[hashlib.sha256(token.encode()).hexdigest()]["expires_at"] = "2000-01-01T00:00:00Z"
    assert page(client, token).json()["state"] == "expired"
    r = page(client, token, "post")
    assert r.status_code == 409 and r.json() == {"error": "expired"}
    assert blocklist(client)["sessions"] == ["s_atk1"]


def test_a_token_of_a_decision_an_admin_already_marked_normal_is_closed(client, engine, owner, pid):
    d = blocked(client, engine, owner, pid)
    client.post(f"/v1/decisions/{d}/normal", headers=acting(owner, pid))
    token = unlock_token(engine)
    assert page(client, token).json()["state"] == "closed"
    r = page(client, token, "post")
    assert r.status_code == 409 and r.json() == {"error": "closed"}


def test_an_unknown_or_missing_token_is_404(client):
    for token in ("nope", ""):
        for method in ("get", "post"):
            r = page(client, token, method)
            assert r.status_code == 404 and r.json() == {"error": "not_found"}


def test_only_the_panel_credential_reaches_the_token_endpoints(client, engine, owner, pid):
    blocked(client, engine, owner, pid)
    token = unlock_token(engine)
    sdk = {**bearer(SDK), "X-Veridian-Unlock-Token": token}
    assert page(client, token, auth=sdk).status_code == 403
    assert page(client, token, "post", auth=sdk).status_code == 403
    assert page(client, token, auth={"X-Veridian-Unlock-Token": token}).status_code == 401
    assert client.get(f"/v1/self-unlock?token={token}", headers=bearer(PANEL)).status_code == 404  # never from the URL
    assert blocklist(client)["sessions"] == ["s_atk1"]
