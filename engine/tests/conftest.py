import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from veridian_engine.api import create_app
from veridian_engine.settings import Settings

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
ROUTES_FILE = Path(__file__).resolve().parent.parent / "routes.yaml"
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
# The stdlib scripts import their shared helpers as top-level modules, as when run directly.
sys.path.insert(0, str(SCRIPTS_DIR))

TOKEN_A = "tok_a"
TOKEN_B = "tok_b"
AUTH_A = {"Authorization": f"Bearer {TOKEN_A}"}
AUTH_B = {"Authorization": f"Bearer {TOKEN_B}"}

# The panel principal (docs/DISENO-PRODUCTO.md): service credential + actor + project.
PANEL_TOKEN = "panel-test-secret"
PANEL_AUTH = {"Authorization": f"Bearer {PANEL_TOKEN}"}
# Bootstrap owner in every test engine: on login it owns org "Demo", which holds the legacy projects.
TEST_OWNER_EMAIL = "owner@test.example"


def login(client: TestClient, sub: str, email: str, name: str | None = "Test") -> dict:
    response = client.post("/v1/people/login", json={"google_sub": sub, "email": email, "name": name}, headers=PANEL_AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def acting(person_id: str, project_id: str | None = None) -> dict:
    headers = {**PANEL_AUTH, "X-Veridian-Actor": person_id}
    if project_id is not None:
        headers["X-Veridian-Project"] = project_id
    return headers


def as_panel(client: TestClient, headers: dict = AUTH_A) -> dict:
    """Panel-principal headers reading the project that `headers` (an SDK key) belongs to, acting
    as the test owner. Data reads are panel-only; the SDK headers stay the way tests name a project.
    Headers that are not a known SDK key are returned unchanged (401 cases)."""
    token = headers.get("Authorization", "").removeprefix("Bearer ").strip()
    project_id = client.app.state.settings.tokens.get(token)
    if project_id is None:
        return headers
    person_id = getattr(client, "_test_owner_id", None)
    if person_id is None:
        person_id = login(client, "sub-test-owner", TEST_OWNER_EMAIL)["person"]["id"]
        client._test_owner_id = person_id
    return acting(person_id, project_id)


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_event(event_id: str, ts: datetime, **overrides) -> dict:
    event = {
        "id": event_id,
        "ts": iso(ts),
        "user_id": "u_42",
        "session_id": "s_owner",
        "ip": "190.202.14.7",
        "user_agent": "Chrome 141 / Windows",
        "device_id": "d_9f3",
        "method": "GET",
        "route": "/invoices",
        "status": 200,
        "bytes": 512,
    }
    event.update(overrides)
    return event


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def load_script(name: str):
    """Import a stdlib script from engine/scripts by file name (without .py)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_settings(tmp_path, clock, **overrides) -> Settings:
    fields = {
        "db_path": str(tmp_path / "veridian.db"),
        "tokens": {TOKEN_A: "client_a", TOKEN_B: "client_b"},
        "routes_file": str(ROUTES_FILE),
        "learn_delay_min": 10,
        "clock": clock,
        "panel_tokens": (PANEL_TOKEN,),
        "bootstrap_owners": frozenset({TEST_OWNER_EMAIL}),
    }
    fields.update(overrides)
    return Settings(**fields)


@pytest.fixture
def client(tmp_path, clock) -> TestClient:
    with TestClient(create_app(make_settings(tmp_path, clock))) as test_client:
        yield test_client


def post_events(client: TestClient, events: list[dict], headers: dict = AUTH_A):
    return client.post("/v1/events", json={"events": events}, headers=headers)


def days_ago(days: int, hour: int = 10) -> datetime:
    return (NOW - timedelta(days=days)).replace(hour=hour, minute=0, second=0)
