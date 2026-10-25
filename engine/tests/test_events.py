from conftest import AUTH_A, NOW, make_event, post_events


def test_missing_token_returns_401(client):
    response = client.post("/v1/events", json={"events": [make_event("e1", NOW)]})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_bad_token_returns_401(client):
    response = post_events(client, [make_event("e1", NOW)], headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_valid_batch_is_accepted(client):
    events = [make_event("e1", NOW), make_event("e2", NOW, route="/invoices/{id}")]
    response = post_events(client, events)
    assert response.status_code == 202
    assert response.json() == {"accepted": 2}


def test_invalid_body_returns_422(client):
    assert post_events(client, [{"id": "e1"}]).status_code == 422
    assert client.post("/v1/events", json={"nope": []}, headers=AUTH_A).status_code == 422


def test_invalid_ip_or_naive_ts_returns_422(client):
    assert post_events(client, [make_event("e1", NOW, ip="not-an-ip")]).status_code == 422
    naive = make_event("e1", NOW)
    naive["ts"] = "2026-10-26T10:00:00"
    assert post_events(client, [naive]).status_code == 422


def test_resending_a_batch_counts_only_new_events(client):
    assert post_events(client, [make_event("e1", NOW), make_event("e2", NOW)]).json() == {"accepted": 2}
    response = post_events(client, [make_event("e1", NOW), make_event("e2", NOW), make_event("e3", NOW)])
    assert response.status_code == 202
    assert response.json() == {"accepted": 1}


def test_same_event_id_is_independent_per_client(client):
    from conftest import AUTH_B

    assert post_events(client, [make_event("e1", NOW)]).json() == {"accepted": 1}
    assert post_events(client, [make_event("e1", NOW)], headers=AUTH_B).json() == {"accepted": 1}


def test_device_id_may_be_empty(client):
    response = post_events(client, [make_event("e1", NOW, device_id="")])
    assert response.status_code == 202
