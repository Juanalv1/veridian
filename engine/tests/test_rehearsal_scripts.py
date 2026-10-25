from datetime import datetime, timedelta, timezone

import pytest

from conftest import AUTH_A, NOW, load_script, post_events, as_panel

ROUTES = "POST /login,GET /invoices,GET /invoices/{id}"


def test_seed_history_builds_the_presenters_profile(client):
    seed = load_script("seed_demo_history")
    events = seed.build_history(
        user_id="u_presenter",
        ip="200.44.32.17",
        device_id="d_presenter",
        days=14,
        hours=seed.parse_hours("13,14,15,16"),
        routes=seed.parse_routes(ROUTES),
        now=NOW,
        run_id="t1",
        learn_delay_min=10,
    )
    for batch in seed.batches(events):
        assert len(batch) <= 1000
        assert post_events(client, batch).json() == {"accepted": len(batch)}

    profile = client.get("/v1/users/u_presenter/profile", headers=as_panel(client, AUTH_A)).json()
    assert profile["networks"] == ["200.44.32.0/24"]
    assert profile["hours"] == [13, 14, 15, 16]
    assert profile["routes"] == ["GET /invoices", "GET /invoices/{id}", "POST /login"]
    assert profile["devices"] == ["d_presenter"]
    assert profile["history_days"] == 14
    assert client.get("/v1/decisions", headers=as_panel(client, AUTH_A)).json() == {"decisions": []}


def test_seed_history_covers_weekends_and_is_old_enough_to_learn():
    seed = load_script("seed_demo_history")
    now = datetime(2026, 10, 26, 0, 5, tzinfo=timezone.utc)  # just past midnight: yesterday is recent
    events = seed.build_history(
        user_id="u_presenter",
        ip="200.44.32.17",
        device_id="",
        days=14,
        hours=[23],
        routes=seed.parse_routes(ROUTES),
        now=now,
        run_id="t2",
        learn_delay_min=10,
    )
    stamps = [datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) for e in events]
    assert all(ts <= now - timedelta(minutes=10) for ts in stamps)
    assert {ts.weekday() for ts in stamps} >= {5, 6}
    assert len({e["id"] for e in events}) == len(events)
    assert all(e["device_id"] == "" for e in events)
    other_run = seed.build_history("u_presenter", "200.44.32.17", "", 14, [13], seed.parse_routes(ROUTES), now, "t3", 10)
    assert not {e["id"] for e in events} & {e["id"] for e in other_run}


def test_seed_parsers_reject_bad_input():
    seed = load_script("seed_demo_history")
    assert seed.parse_routes("post /login, GET /invoices/{id}") == [("POST", "/login"), ("GET", "/invoices/{id}")]
    with pytest.raises(ValueError):
        seed.parse_routes("GET invoices")
    with pytest.raises(ValueError):
        seed.parse_hours("9,24")


def test_reset_script_refuses_without_confirmation(monkeypatch, capsys):
    reset = load_script("reset_client")
    calls = []
    monkeypatch.setattr(reset, "request", lambda *args, **kwargs: calls.append(args) or (200, {}))
    monkeypatch.setattr("builtins.input", lambda prompt="": "https://wrong.example")

    code = reset.main(["--base-url", "https://engine.example", "--token", "secret-token", "--reset-token", "rs"])

    assert code == 1
    assert calls == []
    assert "secret-token" not in capsys.readouterr().out


def test_reset_script_resets_with_yes(monkeypatch, capsys):
    reset = load_script("reset_client")
    calls = []
    monkeypatch.setattr(
        reset, "request", lambda *args, **kwargs: calls.append(args) or (200, {"reset": True, "version": 4})
    )

    code = reset.main(
        ["--base-url", "https://engine.example", "--token", "secret-token", "--reset-token", "rehearsal-secret", "--yes"]
    )

    assert code == 0
    assert calls == [("https://engine.example", "secret-token", "POST", "/v1/admin/reset")]
    out = capsys.readouterr().out
    assert "secret-token" not in out
    assert "rehearsal-secret" not in out
    assert "version 4" in out


def test_reset_script_sends_the_reset_header_and_reports_404(monkeypatch, capsys):
    reset = load_script("reset_client")
    sent = []
    monkeypatch.setattr(reset, "request", lambda *args, **kwargs: sent.append(kwargs) or (404, "not found"))

    code = reset.main(
        ["--base-url", "https://engine.example", "--token", "secret-token", "--reset-token", "rehearsal-secret", "--yes"]
    )

    assert code == 1
    assert sent == [{"headers": {"X-Veridian-Reset-Token": "rehearsal-secret"}}]
    captured = capsys.readouterr()
    assert "reset disabled or reset token wrong" in captured.err
    assert "rehearsal-secret" not in captured.out + captured.err


def test_reset_script_requires_the_reset_token(monkeypatch):
    reset = load_script("reset_client")
    monkeypatch.delenv("VERIDIAN_RESET_TOKEN", raising=False)
    monkeypatch.setattr(reset, "load_dotenv", lambda: None)
    with pytest.raises(SystemExit):
        reset.main(["--base-url", "https://engine.example", "--token", "secret-token", "--yes"])
