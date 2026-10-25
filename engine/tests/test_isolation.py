"""U8: isolation battery between projects (docs/DISENO-PRODUCTO.md, "Pruebas de aislamiento").

Project A is `client_a` (org Demo). The intruder owns project P in another organization and holds
P's SDK key. Every operation of the API is called by the intruder aimed at A's resources (ids in
the path, A in `X-Veridian-Project`), or at the intruder's own project; nothing of A may leak and
A's state must be the same afterwards. OPERATIONS must list every operation of the app: a new
endpoint without an entry here fails `test_every_operation_is_covered`.
"""

import pytest
from conftest import (
    AUTH_A,
    AUTH_B,
    NOW,
    PANEL_AUTH,
    TEST_OWNER_EMAIL,
    acting,
    as_panel,
    days_ago,
    login,
    make_event,
    make_settings,
    post_events,
)
from fastapi.testclient import TestClient

from veridian_engine.api import create_app
from veridian_engine.control_plane.legacy import DEMO_ORG_ID

CREATOR = "intruder@example.com"
MARK_USER, MARK_SESSION, MARK_IP = "u_alpha_secret", "s_alpha_secret", "203.0.113.77"
MARKERS = (MARK_USER, MARK_SESSION, MARK_IP)
# The intruder's own traffic into P. Same ids in two projects are covered with B below.
EVENT = make_event("iso_1", NOW, user_id="u_intruder", session_id="s_intruder", ip="198.51.100.66", method="POST",
                   route="/payouts/bank-account")

# Every operation of the API: (method, path template, who calls, JSON body).
#   "foreign": the intruder's panel identity, aimed at A (path ids and X-Veridian-Project = A);
#   "own": the intruder's panel identity reading their own project P;
#   "sdk": P's SDK key (the intruder holds it).
OPERATIONS = [
    ("GET", "/health", "sdk", None),
    ("POST", "/v1/events", "sdk", {"events": [EVENT]}),
    ("POST", "/v1/check", "sdk", {"event": EVENT}),
    ("GET", "/v1/blocklist", "sdk", None),
    ("GET", "/v1/routes", "sdk", None),
    ("GET", "/v1/blocklist", "foreign", None),
    ("GET", "/v1/routes", "foreign", None),
    ("GET", "/v1/summary", "foreign", None),
    ("GET", "/v1/summary", "own", None),
    ("GET", "/v1/users", "foreign", None),
    ("GET", "/v1/users", "own", None),
    ("GET", "/v1/users/{user_id}/profile", "foreign", None),
    ("GET", "/v1/users/{user_id}/profile", "own", None),
    ("GET", "/v1/decisions", "foreign", None),
    ("GET", "/v1/decisions", "own", None),
    ("GET", "/v1/activity", "foreign", None),
    ("GET", "/v1/activity", "own", None),
    ("POST", "/v1/decisions/{decision_id}/normal", "foreign", None),
    ("POST", "/v1/decisions/{decision_id}/normal", "own", None),
    ("POST", "/v1/admin/reset", "sdk", None),
    ("POST", "/v1/people/login", "own", {"google_sub": "sub-intruder", "email": CREATOR, "name": "Intruder"}),
    ("GET", "/v1/me/orgs", "own", None),
    ("POST", "/v1/orgs", "own", {"name": "Second"}),
    ("GET", "/v1/orgs/{org_id}", "foreign", None),
    ("PATCH", "/v1/orgs/{org_id}", "foreign", {"name": "Hacked"}),
    ("GET", "/v1/orgs/{org_id}/projects", "foreign", None),
    ("POST", "/v1/orgs/{org_id}/projects", "foreign", {"name": "Implant"}),
    ("GET", "/v1/projects/{project_id}", "foreign", None),
    ("PATCH", "/v1/projects/{project_id}", "foreign", {"enforcement": "observe"}),
    ("DELETE", "/v1/projects/{project_id}", "foreign", None),
    ("PUT", "/v1/projects/{project_id}/routes", "foreign", {"routes": []}),
    ("GET", "/v1/orgs/{org_id}/audit", "foreign", None),
    ("GET", "/v1/orgs/{org_id}/me", "foreign", None),
    ("PATCH", "/v1/orgs/{org_id}/me", "foreign", {"notify_email": False}),
    ("GET", "/v1/orgs/{org_id}/members", "foreign", None),
    ("PATCH", "/v1/orgs/{org_id}/members/{member_id}", "foreign", {"role": "viewer"}),
    ("DELETE", "/v1/orgs/{org_id}/members/{member_id}", "foreign", None),
    ("GET", "/v1/orgs/{org_id}/invitations", "foreign", None),
    ("POST", "/v1/orgs/{org_id}/invitations", "foreign", {"email": "spy@example.com", "role": "owner"}),
    ("DELETE", "/v1/orgs/{org_id}/invitations/{invitation_id}", "foreign", None),
    ("GET", "/v1/projects/{project_id}/api-keys", "foreign", None),
    ("POST", "/v1/projects/{project_id}/api-keys", "foreign", {"name": "spy"}),
    ("POST", "/v1/projects/{project_id}/api-keys/{key_id}/revoke", "foreign", None),
    ("GET", "/v1/projects/{project_id}/webhook", "foreign", None),
    ("PUT", "/v1/projects/{project_id}/webhook", "foreign", {"url": "https://spy.example.com/hook"}),
    ("PATCH", "/v1/projects/{project_id}/webhook", "foreign", {"enabled": False}),
    ("DELETE", "/v1/projects/{project_id}/webhook", "foreign", None),
    ("GET", "/v1/projects/{project_id}/webhook/deliveries", "foreign", None),
    ("POST", "/v1/projects/{project_id}/webhook/test", "foreign", None),
]
DENIED = {401, 403, 404}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A with a block on the marker session, an invitation and a key; the intruder with P and P's key."""
    from conftest import FakeClock

    tmp_path = tmp_path_factory.mktemp("isolation")
    settings = make_settings(tmp_path, FakeClock(NOW), org_creators=frozenset({CREATOR}))
    with TestClient(create_app(settings)) as client:
        owner = login(client, "sub-test-owner", TEST_OWNER_EMAIL)["person"]["id"]
        history = [make_event(f"h{i}", days_ago(i), user_id=MARK_USER) for i in range(1, 4)]
        post_events(client, history, headers=AUTH_A)
        attack = make_event("x1", NOW, user_id=MARK_USER, ip=MARK_IP, session_id=MARK_SESSION, method="POST",
                            route="/payouts/bank-account")
        post_events(client, [attack], headers=AUTH_A)
        [decision] = client.get("/v1/decisions", headers=as_panel(client, AUTH_A)).json()["decisions"]
        invitation = client.post(f"/v1/orgs/{DEMO_ORG_ID}/invitations", json={"email": "x@example.com", "role": "viewer"},
                                 headers=acting(owner)).json()
        key = client.post("/v1/projects/client_a/api-keys", json={"name": "ci"}, headers=acting(owner)).json()
        intruder = login(client, "sub-intruder", CREATOR, "Intruder")["person"]["id"]
        org = client.post("/v1/orgs", json={"name": "Acme"}, headers=acting(intruder)).json()
        project = client.post(f"/v1/orgs/{org['id']}/projects", json={"name": "Shop"}, headers=acting(intruder)).json()
        own_key = client.post(f"/v1/projects/{project['id']}/api-keys", json={"name": "k"},
                              headers=acting(intruder)).json()["key"]
        yield {
            "client": client, "owner": owner, "intruder": intruder, "project": project["id"],
            "sdk": {"Authorization": f"Bearer {own_key}"},
            "ids": {"project_id": "client_a", "org_id": DEMO_ORG_ID, "user_id": MARK_USER,
                    "decision_id": decision["id"], "member_id": owner, "invitation_id": invitation["id"],
                    "key_id": key["id"]},
        }


def snapshot(world) -> dict:
    """Everything of A an intrusion could change, read as A's owner."""
    client, owner = world["client"], world["owner"]
    read = acting(owner, "client_a")
    return {
        "decisions": client.get("/v1/decisions", headers=read).json(),
        "blocklist": client.get("/v1/blocklist", headers=AUTH_A).json(),
        "routes": client.get("/v1/routes", headers=AUTH_A).json(),
        "project": client.get("/v1/projects/client_a", headers=acting(owner)).json(),
        "org": client.get(f"/v1/orgs/{DEMO_ORG_ID}", headers=acting(owner)).json(),
        "members": client.get(f"/v1/orgs/{DEMO_ORG_ID}/members", headers=acting(owner)).json(),
        "invitations": client.get(f"/v1/orgs/{DEMO_ORG_ID}/invitations", headers=acting(owner)).json(),
        "keys": client.get("/v1/projects/client_a/api-keys", headers=acting(owner)).json(),
        "webhook": client.get("/v1/projects/client_a/webhook", headers=acting(owner)).status_code,
    }


def headers_for(world, caller: str) -> dict:
    if caller == "sdk":
        return world["sdk"]
    if caller == "own":
        return acting(world["intruder"], world["project"])
    return acting(world["intruder"], "client_a")


def test_every_operation_is_covered(world):
    paths = world["client"].app.openapi()["paths"]
    actual = {(method.upper(), path) for path, ops in paths.items() for method in ops}
    assert actual == {(method, path) for method, path, _, _ in OPERATIONS}


@pytest.mark.parametrize("method,template,caller,body", OPERATIONS,
                         ids=[f"{m} {p} [{c}]" for m, p, c, _ in OPERATIONS])
def test_the_intruder_gets_nothing_of_a_and_changes_nothing(world, method, template, caller, body):
    client = world["client"]
    before = snapshot(world)
    url = template.format(**world["ids"])
    headers = headers_for(world, caller)
    if template == "/v1/people/login":
        headers = PANEL_AUTH
    response = client.request(method, url, json=body, headers=headers)
    if caller == "foreign":
        assert response.status_code in DENIED, response.text
    leaks = MARKERS + (world["ids"]["decision_id"], world["ids"]["invitation_id"], world["ids"]["key_id"])
    if template == "/v1/users/{user_id}/profile" and caller == "own":
        # The profile echoes the asked user_id; what matters is that P knows nothing of A's user.
        assert response.json()["history_days"] == 0 and response.json()["networks"] == []
        leaks = tuple(m for m in leaks if m != MARK_USER)
    for marker in leaks:
        assert marker not in response.text
    assert snapshot(world) == before


def test_same_user_and_session_ids_in_two_projects_do_not_mix(world):
    client = world["client"]
    post_events(client, [make_event(f"b{i}", days_ago(i), user_id=MARK_USER, session_id=MARK_SESSION)
                         for i in range(1, 3)], headers=AUTH_B)
    profile_b = client.get(f"/v1/users/{MARK_USER}/profile", headers=as_panel(client, AUTH_B)).json()
    profile_a = client.get(f"/v1/users/{MARK_USER}/profile", headers=as_panel(client, AUTH_A)).json()
    assert profile_b != profile_a
    assert client.get("/v1/decisions", headers=as_panel(client, AUTH_B)).json()["decisions"] == []
    assert MARK_SESSION not in client.get("/v1/blocklist", headers=AUTH_B).json()["sessions"]
    assert client.post(f"/v1/decisions/{world['ids']['decision_id']}/normal",
                       headers=as_panel(client, AUTH_B)).status_code == 404


def test_blocklist_versions_are_independent(world):
    client = world["client"]
    version_b = client.get("/v1/blocklist", headers=AUTH_B).json()["version"]
    post_events(client, [make_event("x9", NOW, ip="198.51.100.9", session_id="s_other", method="POST",
                                    route="/payouts/bank-account", user_id="u_other")], headers=AUTH_A)
    assert client.get("/v1/blocklist", headers=AUTH_B).json()["version"] == version_b
