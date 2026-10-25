"""U7: route classification per project (route_rules), seeded from routes.yaml, replaced with PUT."""

import pytest
from conftest import AUTH_A, AUTH_B, NOW, TEST_OWNER_EMAIL, acting, days_ago, login, make_event, make_settings, post_events
from fastapi.testclient import TestClient

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.control_plane import repository
from veridian_engine.control_plane.legacy import DEMO_ORG_ID

CREATOR = "creator@example.com"
CUSTOM = [
    {"method": "POST", "route": "/transfers", "class": "critical"},
    {"method": "GET", "route": "/statements", "class": "sensitive"},
    {"method": "GET", "route": "/payouts/bank-account", "class": "normal"},
]


@pytest.fixture
def client(tmp_path, clock):
    settings = make_settings(tmp_path, clock, org_creators=frozenset({CREATOR}))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def owner(client):
    return login(client, "sub-test-owner", TEST_OWNER_EMAIL)["person"]["id"]


def put_routes(client, actor, routes, project_id="client_a"):
    return client.put(f"/v1/projects/{project_id}/routes", json={"routes": routes}, headers=acting(actor))


def member_as(client, role, sub="sub-m", email="m@example.com"):
    owner_id = owner(client)
    with db.connect(client.app.state.settings.db_path) as conn:
        repository.create_invitation(conn, actor_id=owner_id, org_id=DEMO_ORG_ID, email=email, role=role, now=NOW)
    return login(client, sub, email)["person"]["id"]


def test_existing_projects_are_seeded_from_the_template(client):
    template = client.get("/v1/routes", headers=AUTH_A).json()
    assert template["routes"][0] == {"method": "POST", "route": "/payouts/bank-account", "class": "critical"}
    assert client.get("/v1/routes", headers=AUTH_B).json() == template


def new_project(client):
    creator = login(client, "sub-creator", CREATOR)["person"]["id"]
    org = client.post("/v1/orgs", json={"name": "Acme"}, headers=acting(creator)).json()
    project = client.post(f"/v1/orgs/{org['id']}/projects", json={"name": "Shop"}, headers=acting(creator)).json()
    return creator, project["id"]


def test_a_new_project_starts_empty_not_with_the_demo_template(client):
    creator, project_id = new_project(client)
    routes = client.get("/v1/routes", headers=acting(creator, project_id)).json()
    assert routes == {"routes": [], "default": "normal"}


def test_a_new_project_stays_empty_after_a_restart(tmp_path, clock):
    settings = make_settings(tmp_path, clock, org_creators=frozenset({CREATOR}))
    with TestClient(create_app(settings)) as first:
        creator, project_id = new_project(first)
    with TestClient(create_app(settings)) as second:
        assert second.get("/v1/routes", headers=acting(creator, project_id)).json()["routes"] == []


def set_rule(client, actor, rule, project_id="client_a"):
    return client.put(f"/v1/projects/{project_id}/routes/rule", json=rule, headers=acting(actor))


def delete_rule(client, actor, method, route, project_id="client_a"):
    return client.delete(f"/v1/projects/{project_id}/routes/rule", params={"method": method, "route": route},
                         headers=acting(actor))


def test_set_rule_adds_and_changes_one_route_of_one_project(client):
    owner_id = owner(client)
    added = set_rule(client, owner_id, {"method": "POST", "route": "/transfers", "class": "sensitive"})
    assert added.status_code == 200
    assert {"method": "POST", "route": "/transfers", "class": "sensitive"} in added.json()["routes"]
    changed = set_rule(client, owner_id, {"method": "POST", "route": "/transfers", "class": "critical"}).json()
    assert {"method": "POST", "route": "/transfers", "class": "critical"} in changed["routes"]
    assert len([r for r in changed["routes"] if r["route"] == "/transfers"]) == 1
    assert changed == client.get("/v1/routes", headers=AUTH_A).json()
    assert all(r["route"] != "/transfers" for r in client.get("/v1/routes", headers=AUTH_B).json()["routes"])


def test_delete_rule_returns_the_route_to_normal(client):
    owner_id = owner(client)
    response = delete_rule(client, owner_id, "POST", "/payouts/bank-account")
    assert response.status_code == 200
    assert {"method": "POST", "route": "/payouts/bank-account", "class": "critical"} not in response.json()["routes"]
    assert client.get("/v1/routes", headers=AUTH_B).json()["routes"][0]["route"] == "/payouts/bank-account"
    assert delete_rule(client, owner_id, "POST", "/payouts/bank-account").status_code == 404


def test_rule_writes_need_admin_and_are_audited(client):
    viewer = member_as(client, "viewer", sub="s-v", email="v@example.com")
    admin = member_as(client, "admin", sub="s-a", email="a@example.com")
    rule = {"method": "POST", "route": "/transfers", "class": "critical"}
    assert set_rule(client, viewer, rule).status_code == 403
    assert delete_rule(client, viewer, "POST", "/payouts/bank-account").status_code == 403
    assert client.put("/v1/projects/client_a/routes/rule", json=rule, headers=AUTH_A).status_code == 403
    assert set_rule(client, admin, rule).status_code == 200
    assert delete_rule(client, admin, "POST", "/transfers").status_code == 200
    with db.connect(client.app.state.settings.db_path) as conn:
        rows = conn.execute("SELECT action, actor_person_id, meta FROM audit_log WHERE action LIKE 'routes.rule_%'"
                            " ORDER BY rowid").fetchall()
    assert [(r[0], r[1]) for r in rows] == [("routes.rule_set", admin), ("routes.rule_deleted", admin)]
    assert '"route": "/transfers"' in rows[0][2] and '"class": "critical"' in rows[0][2]


def test_a_member_of_another_org_cannot_set_rules(client):
    creator, _ = new_project(client)
    assert set_rule(client, creator, {"method": "POST", "route": "/x", "class": "critical"}).status_code == 403
    assert delete_rule(client, creator, "POST", "/payouts/bank-account").status_code == 403


@pytest.mark.parametrize("rule", [
    {"method": "POST", "route": "/x", "class": "deadly"},
    {"method": "post", "route": "/x", "class": "critical"},
    {"method": "POST", "route": "x y", "class": "critical"},
])
def test_set_rule_validates(client, rule):
    assert set_rule(client, owner(client), rule).status_code == 422


def test_put_replaces_the_routes_of_one_project_only(client):
    owner_id = owner(client)
    response = put_routes(client, owner_id, CUSTOM)
    assert response.status_code == 200
    expected = {"routes": [CUSTOM[0], CUSTOM[1]], "default": "normal"}
    assert response.json() == expected
    assert client.get("/v1/routes", headers=AUTH_A).json() == expected
    assert client.get("/v1/routes", headers=AUTH_B).json()["routes"][0]["route"] == "/payouts/bank-account"


def test_the_block_rule_reads_the_project_classification(client):
    owner_id = owner(client)
    put_routes(client, owner_id, CUSTOM)
    for headers in (AUTH_A, AUTH_B):
        post_events(client, [make_event(f"h{i}", days_ago(i)) for i in range(1, 4)], headers=headers)
    for headers in (AUTH_A, AUTH_B):
        attack = make_event("x1", NOW, ip="203.0.113.9", session_id="s_att", method="POST", route="/transfers")
        post_events(client, [attack], headers=headers)
    blocked_a = client.get("/v1/blocklist", headers=AUTH_A).json()["sessions"]
    blocked_b = client.get("/v1/blocklist", headers=AUTH_B).json()["sessions"]
    assert "s_att" in blocked_a and "s_att" not in blocked_b


def test_check_reads_the_project_classification(client):
    owner_id = owner(client)
    put_routes(client, owner_id, CUSTOM)
    post_events(client, [make_event(f"h{i}", days_ago(i)) for i in range(1, 4)], headers=AUTH_A)
    event = make_event("c1", NOW, ip="203.0.113.9", session_id="s_att", method="POST", route="/transfers")
    assert client.post("/v1/check", json={"event": event}, headers=AUTH_A).json()["verdict"] == "block"
    # A new network on a route that the template calls critical but this project calls normal.
    old_critical = make_event("c2", NOW, ip="198.51.100.7", session_id="s_new", method="POST", route="/payouts/bank-account")
    assert client.post("/v1/check", json={"event": old_critical}, headers=AUTH_A).json()["verdict"] == "allow"


def test_an_empty_list_stays_empty_after_a_restart(tmp_path, clock):
    settings = make_settings(tmp_path, clock)
    with TestClient(create_app(settings)) as first:
        assert put_routes(first, owner(first), []).status_code == 200
    with TestClient(create_app(settings)) as second:
        assert second.get("/v1/routes", headers=AUTH_A).json() == {"routes": [], "default": "normal"}


def test_only_an_admin_replaces_routes_and_it_is_audited(client):
    viewer = member_as(client, "viewer", sub="s-v", email="v@example.com")
    admin = member_as(client, "admin", sub="s-a", email="a@example.com")
    assert put_routes(client, viewer, CUSTOM).status_code == 403
    assert client.put("/v1/projects/client_a/routes", json={"routes": CUSTOM}, headers=AUTH_A).status_code == 403
    assert put_routes(client, admin, CUSTOM).status_code == 200
    with db.connect(client.app.state.settings.db_path) as conn:
        [row] = conn.execute("SELECT actor_person_id, project_id, meta FROM audit_log WHERE action = 'routes.replaced'")
    assert (row[0], row[1]) == (admin, "client_a") and '"count": 3' in row[2]


def test_a_member_of_another_org_cannot_read_or_replace_routes(client):
    creator = login(client, "sub-creator", CREATOR)["person"]["id"]
    assert client.get("/v1/routes", headers=acting(creator, "client_a")).status_code == 403
    assert put_routes(client, creator, CUSTOM).status_code == 403


@pytest.mark.parametrize("routes", [
    [{"method": "POST", "route": "/x", "class": "deadly"}],
    [{"method": "post", "route": "/x", "class": "critical"}],
    [{"method": "POST", "route": "x", "class": "critical"}],
    [{"method": "POST", "route": "/x y", "class": "critical"}],
    [{"method": "POST", "route": "/x", "class": "critical"}, {"method": "POST", "route": "/x", "class": "normal"}],
    [{"method": "POST", "class": "critical"}],
])
def test_put_validates_every_rule(client, routes):
    owner_id = owner(client)
    assert put_routes(client, owner_id, routes).status_code == 422
    assert client.get("/v1/routes", headers=AUTH_A).json()["routes"][0]["route"] == "/payouts/bank-account"
