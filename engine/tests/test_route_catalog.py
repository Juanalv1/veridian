"""Automatic route classification: the SDK reports the app's routes (contract 16), the engine
classifies the new or changed ones with the AI per project and serves them in GET /v1/routes.
An AI failure keeps the last known class; admin corrections and routes.yaml rules win."""

import pytest
from conftest import AUTH_A, AUTH_B, TEST_OWNER_EMAIL, acting, login, make_settings
from fastapi.testclient import TestClient

from veridian_engine import db
from veridian_engine.api import create_app
from veridian_engine.openapi.classify import SYSTEM_PROMPT, RouteClass

TRANSFER = {"method": "POST", "route": "/transfers", "summary": "Send money", "description": "Moves funds out."}
STATEMENTS = {"method": "GET", "route": "/statements", "summary": "Statements"}
HOME = {"method": "GET", "route": "/home"}
BANK = {"method": "POST", "route": "/payouts/bank-account", "summary": "Change the payout account"}

AI_CLASSES = {"POST /transfers": "critical", "GET /statements": "sensitive", "GET /home": "normal",
              "POST /payouts/bank-account": "normal"}


class FakeClassifier:
    """Stands in for openapi.classify: records each call, can fail like the real one degrades."""

    def __init__(self, classes=None):
        self.classes = dict(AI_CLASSES if classes is None else classes)
        self.calls: list[list[str]] = []
        self.failing = False

    def __call__(self, operations):
        self.calls.append([op.key for op in operations])
        if self.failing:
            return {op.key: RouteClass("normal", "fallback: API call failed (APIError)") for op in operations}
        return {op.key: RouteClass(self.classes.get(op.key, "normal"), f"because {op.key}") for op in operations}


@pytest.fixture
def classifier():
    return FakeClassifier()


@pytest.fixture
def client(tmp_path, clock, classifier):
    settings = make_settings(tmp_path, clock, auto_classify=True, route_classifier=classifier, classify_poll_s=3600.0)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def report(client, routes, headers=AUTH_A):
    return client.put("/v1/routes/catalog", json={"routes": routes}, headers=headers)


def classify(client):
    return client.app.state.route_classifier.run_once()


def served(client, headers=AUTH_A):
    return {f"{r['method']} {r['route']}": r["class"] for r in client.get("/v1/routes", headers=headers).json()["routes"]}


def owner(client):
    return login(client, "sub-test-owner", TEST_OWNER_EMAIL)["person"]["id"]


def catalog(client, actor, project_id="client_a"):
    response = client.get(f"/v1/projects/{project_id}/routes/catalog", headers=acting(actor))
    assert response.status_code == 200, response.text
    return {f"{r['method']} {r['route']}": r for r in response.json()["routes"]}


# --- contract 16: the report ---


def test_the_report_is_accepted_and_counts_what_needs_classifying(client):
    response = report(client, [TRANSFER, STATEMENTS, HOME])
    assert response.status_code == 200
    assert response.json() == {"received": 3, "to_classify": 3}


def test_only_new_or_changed_routes_are_classified_again(client, classifier):
    report(client, [TRANSFER, STATEMENTS])
    classify(client)
    assert report(client, [TRANSFER, STATEMENTS]).json() == {"received": 2, "to_classify": 0}
    assert classify(client) == 0

    changed = {**STATEMENTS, "description": "Now also exports every customer."}
    assert report(client, [TRANSFER, changed, HOME]).json() == {"received": 3, "to_classify": 2}
    classify(client)
    assert sorted(classifier.calls[-1]) == ["GET /home", "GET /statements"]


@pytest.mark.parametrize("body", [
    {}, {"routes": [{"method": "post", "route": "/x"}]}, {"routes": [{"method": "GET", "route": "x"}]},
    {"routes": [{"method": "GET", "route": "/x", "summary": "s" * 2001}]},
    {"routes": [HOME, HOME]},
])
def test_the_report_is_validated(client, body):
    assert client.put("/v1/routes/catalog", json=body, headers=AUTH_A).status_code == 422


def test_only_an_sdk_key_reports_routes(client):
    person = owner(client)
    assert client.put("/v1/routes/catalog", json={"routes": [HOME]}, headers=acting(person, "client_a")).status_code == 403
    assert client.put("/v1/routes/catalog", json={"routes": [HOME]}).status_code == 401


# --- classification and serving ---


def test_classified_routes_are_served_to_the_middleware(client):
    report(client, [TRANSFER, STATEMENTS, HOME])
    assert classify(client) == 3
    routes = served(client)
    assert routes["POST /transfers"] == "critical"
    assert routes["GET /statements"] == "sensitive"
    assert "GET /home" not in routes  # normal is never listed


def test_two_projects_never_see_each_others_routes(client):
    report(client, [TRANSFER])
    classify(client)
    assert "POST /transfers" not in served(client, AUTH_B)
    person = owner(client)
    assert "POST /transfers" not in catalog(client, person, "client_b")


def test_an_ai_failure_keeps_the_last_known_class_and_retries_on_the_next_report(client, classifier):
    report(client, [TRANSFER])
    classify(client)
    classifier.failing = True
    report(client, [{**TRANSFER, "description": "Moves funds out, now in bulk."}])
    classify(client)
    assert served(client)["POST /transfers"] == "critical"
    person = owner(client)
    row = catalog(client, person)["POST /transfers"]
    assert row["status"] == "failed" and row["last_error"].startswith("fallback:")

    assert classify(client) == 0  # no retry loop: the next report retries
    classifier.failing = False
    assert report(client, [{**TRANSFER, "description": "Moves funds out, now in bulk."}]).json()["to_classify"] == 1
    classify(client)
    assert catalog(client, person)["POST /transfers"]["status"] == "classified"


def test_a_new_route_the_ai_could_not_classify_stays_normal_and_pending(client, classifier):
    classifier.failing = True
    report(client, [TRANSFER])
    classify(client)
    assert "POST /transfers" not in served(client)
    row = catalog(client, owner(client))["POST /transfers"]
    assert row["class"] == "normal" and row["status"] == "failed" and row["source"] is None


def test_the_template_rules_are_never_overwritten_by_the_ai(client):
    # routes.yaml says critical; the fake AI says normal.
    report(client, [BANK])
    classify(client)
    assert served(client)["POST /payouts/bank-account"] == "critical"
    row = catalog(client, owner(client))["POST /payouts/bank-account"]
    assert row["source"] == "template" and row["ai_class"] == "normal"


def test_an_admin_correction_survives_the_next_classification(client, classifier):
    person = owner(client)
    report(client, [TRANSFER, STATEMENTS])
    classify(client)
    corrected = [{"method": "POST", "route": "/transfers", "class": "sensitive"}]
    assert client.put("/v1/projects/client_a/routes", json={"routes": corrected},
                      headers=acting(person)).status_code == 200

    changed = [{**TRANSFER, "summary": "Send money now"}, {**STATEMENTS, "summary": "All statements"}]
    report(client, changed)
    classify(client)

    routes = served(client)
    assert routes["POST /transfers"] == "sensitive"
    # Left out of the admin's list: the admin said normal, and the AI does not bring it back.
    assert "GET /statements" not in routes
    rows = catalog(client, person)
    assert rows["POST /transfers"]["source"] == "admin" and rows["GET /statements"]["source"] == "admin"


def test_a_route_reported_after_the_admin_edit_is_still_classified(client):
    person = owner(client)
    client.put("/v1/projects/client_a/routes", json={"routes": []}, headers=acting(person))
    report(client, [TRANSFER])
    classify(client)
    assert served(client)["POST /transfers"] == "critical"


def test_the_catalog_shows_reason_status_and_source(client):
    report(client, [TRANSFER, HOME])
    person = owner(client)
    before = catalog(client, person)["POST /transfers"]
    assert before["status"] == "pending" and before["class"] == "normal"
    classify(client)
    after = catalog(client, person)["POST /transfers"]
    assert after["status"] == "classified" and after["class"] == "critical" and after["source"] == "ai"
    assert after["ai_reason"] == "because POST /transfers" and after["summary"] == "Send money"
    assert set(after) == {"method", "route", "summary", "status", "class", "source", "ai_class", "ai_reason",
                          "last_error", "classified_at"}


def test_the_catalog_needs_the_panel_and_membership(client):
    assert client.get("/v1/projects/client_a/routes/catalog", headers=AUTH_A).status_code == 403
    stranger = login(client, "sub-s", "stranger@example.com")["person"]["id"]
    assert client.get("/v1/projects/client_a/routes/catalog", headers=acting(stranger)).status_code == 403


def test_a_route_left_out_of_a_report_leaves_the_catalog_but_keeps_its_rule(client):
    report(client, [TRANSFER, HOME])
    classify(client)
    report(client, [HOME])
    assert "POST /transfers" not in catalog(client, owner(client))
    assert served(client)["POST /transfers"] == "critical"


# --- worker ---


def test_the_classifier_lives_with_the_app(client):
    assert client.app.state.route_classifier.is_alive()


def test_auto_classification_off_starts_no_worker_but_still_takes_reports(tmp_path, clock):
    with TestClient(create_app(make_settings(tmp_path, clock))) as off:
        assert off.app.state.route_classifier is None
        assert report(off, [HOME]).status_code == 200


def test_the_classifier_reads_only_method_route_and_text(client, classifier):
    """Contract 16 carries no code; the classifier gets nothing else either."""
    seen = []
    client.app.state.route_classifier._classify = lambda ops: seen.extend(ops) or classifier(ops)
    report(client, [TRANSFER])
    classify(client)
    [op] = seen
    assert (op.method, op.path, op.summary, op.description) == ("POST", "/transfers", "Send money", "Moves funds out.")


# --- prompt ---


def test_the_prompt_does_not_make_login_critical():
    assert "login and credential" not in SYSTEM_PROMPT
    assert "Logging in" in SYSTEM_PROMPT
