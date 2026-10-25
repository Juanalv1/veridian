import json
from pathlib import Path
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


# --- prepare_demo: one-step demo preparation (HANDOFF, Dev B item 5) ---

from datetime import datetime as _dt  # noqa: E402

from conftest import PANEL_TOKEN, TEST_OWNER_EMAIL, login, make_event  # noqa: E402

from veridian_engine import db as _db  # noqa: E402
from veridian_engine.control_plane import repository as _repository  # noqa: E402
from veridian_engine.control_plane.legacy import DEMO_ORG_ID  # noqa: E402

PRESENTER = "u_42"


def via(client):
    """The scripts' `request`, served by the test app instead of the network."""
    def request(base_url, token, method, path, body=None, headers=None):
        response = client.request(method, path, json=body, headers={"Authorization": f"Bearer {token}", **(headers or {})})
        return response.status_code, (response.json() if response.content else None)
    return request


def rehearsal_owner(client, email="ensayo@veridian.demo"):
    """What the team does once in the panel: invite the rehearsal email as owner of Demo."""
    owner_id = login(client, "sub-test-owner", TEST_OWNER_EMAIL)["person"]["id"]
    with _db.connect(client.app.state.settings.db_path) as conn:
        _repository.create_invitation(conn, actor_id=owner_id, org_id=DEMO_ORG_ID, email=email, role="owner", now=NOW)


def config(tmp_path, **overrides):
    prepare = load_script("prepare_demo")
    values = {"base_url": "http://engine", "sdk_token": "tok_a", "panel_token": PANEL_TOKEN, "project_id": "client_a",
              "user_id": PRESENTER, "actor_email": "ensayo@veridian.demo", "state_file": str(tmp_path / "presenter.json")}
    values.update(overrides)
    return prepare, prepare.Config(**values)


def test_calibration_takes_every_ip_and_device_of_the_presenter_and_nobody_else():
    prepare = load_script("prepare_demo")
    since = _dt(2026, 10, 26, 11, 50, tzinfo=timezone.utc)
    activity = [
        {"ts": "2026-10-26T11:55:00Z", "user_id": PRESENTER, "ip": "190.202.14.7", "device_id": "d_mac"},
        {"ts": "2026-10-26T11:57:00Z", "user_id": PRESENTER, "ip": "186.24.9.10", "device_id": "d_mac"},  # CGNAT
        {"ts": "2026-10-26T11:58:00Z", "user_id": PRESENTER, "ip": "186.24.9.10", "device_id": ""},
        {"ts": "2026-10-26T11:56:00Z", "user_id": "u_other", "ip": "203.0.113.9", "device_id": "d_other"},
        {"ts": "2026-10-26T11:40:00Z", "user_id": PRESENTER, "ip": "200.1.1.1", "device_id": "d_old"},  # too old
    ]
    calibration = prepare.calibrate(activity, PRESENTER, since)
    assert calibration == {"user_id": PRESENTER, "ips": ["186.24.9.10", "190.202.14.7"], "devices": ["d_mac"]}


def test_calibration_without_presenter_traffic_fails_clearly(client, tmp_path, capsys):
    rehearsal_owner(client)
    prepare, cfg = config(tmp_path)
    code = prepare.run_calibration(cfg, minutes=10, request=via(client), now=NOW)
    assert code == 1
    assert "no access from u_42" in capsys.readouterr().err


def test_calibration_reads_the_activity_and_saves_it(client, tmp_path):
    rehearsal_owner(client)
    post_events(client, [make_event("p1", NOW - timedelta(minutes=2), ip="190.202.14.7", device_id="d_mac"),
                         make_event("p2", NOW - timedelta(minutes=1), ip="186.24.9.10", device_id="d_mac")])
    prepare, cfg = config(tmp_path)
    assert prepare.run_calibration(cfg, minutes=10, request=via(client), now=NOW) == 0
    saved = json.loads(Path(cfg.state_file).read_text())
    assert saved["ips"] == ["186.24.9.10", "190.202.14.7"] and saved["devices"] == ["d_mac"]


def test_preparation_leaves_a_clean_profile_without_the_attacker(client, tmp_path, capsys):
    rehearsal_owner(client)
    # A previous rehearsal: the presenter's traffic and an attacker who got blocked.
    post_events(client, [make_event("old1", NOW - timedelta(days=2), ip="190.202.14.7")])
    post_events(client, [make_event("x1", NOW, ip="203.0.113.9", session_id="s_evil", device_id="d_evil",
                                    method="POST", route="/payouts/bank-account")])
    prepare, cfg = config(tmp_path)
    Path(cfg.state_file).write_text(json.dumps({"user_id": PRESENTER, "ips": ["186.24.9.10", "190.202.14.7"],
                                                "devices": ["d_mac"]}))

    assert prepare.run_preparation(cfg, request=via(client), now=NOW) == 0

    out = capsys.readouterr().out
    assert "listo: 2 redes, 1 dispositivo, 14 días" in out
    profile = client.get(f"/v1/users/{PRESENTER}/profile", headers=as_panel(client, AUTH_A)).json()
    assert profile["networks"] == ["186.24.9.0/24", "190.202.14.0/24"]
    assert profile["devices"] == ["d_mac"] and profile["state"] == "normal" and profile["history_days"] == 14
    assert client.get("/v1/decisions", headers=as_panel(client, AUTH_A)).json() == {"decisions": []}
    blocklist = client.get("/v1/blocklist", headers=AUTH_A).json()
    assert blocklist["sessions"] == [] and blocklist["ips"] == [] and blocklist["version"] >= 2
    for secret in ("tok_a", PANEL_TOKEN):
        assert secret not in out


def test_preparation_needs_a_calibration_first(client, tmp_path, capsys):
    prepare, cfg = config(tmp_path)
    assert prepare.run_preparation(cfg, request=via(client), now=NOW) == 1
    assert "--calibrar" in capsys.readouterr().err


def test_without_the_owner_invitation_the_script_says_what_to_do(client, tmp_path, capsys):
    prepare, cfg = config(tmp_path)
    Path(cfg.state_file).write_text(json.dumps({"user_id": PRESENTER, "ips": ["190.202.14.7"], "devices": []}))
    assert prepare.run_preparation(cfg, request=via(client), now=NOW) == 1
    assert "ensayo@veridian.demo" in capsys.readouterr().err
