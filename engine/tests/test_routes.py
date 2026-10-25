from conftest import AUTH_A, AUTH_B


def test_routes_lists_the_loaded_classification_critical_first(client):
    response = client.get("/v1/routes", headers=AUTH_A)
    assert response.status_code == 200
    assert response.json() == {
        "routes": [
            {"method": "POST", "route": "/payouts/bank-account", "class": "critical"},
            {"method": "GET", "route": "/invoices/export", "class": "sensitive"},
            {"method": "GET", "route": "/payouts/bank-account", "class": "sensitive"},
        ],
        "default": "normal",
    }


def test_routes_never_lists_normal_routes(client):
    classes = {r["class"] for r in client.get("/v1/routes", headers=AUTH_A).json()["routes"]}
    assert classes <= {"critical", "sensitive"}


def test_routes_is_the_same_for_every_client(client):
    assert client.get("/v1/routes", headers=AUTH_A).json() == client.get("/v1/routes", headers=AUTH_B).json()


def test_routes_requires_a_valid_token(client):
    for headers in ({}, {"Authorization": "Bearer nope"}):
        response = client.get("/v1/routes", headers=headers)
        assert response.status_code == 401
        assert response.json() == {"error": "unauthorized"}
