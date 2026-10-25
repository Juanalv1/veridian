from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from veridian_engine.api import create_app
from veridian_engine.settings import Settings

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
ROUTES_FILE = Path(__file__).resolve().parent.parent / "routes.yaml"

TOKEN_A = "tok_a"
TOKEN_B = "tok_b"
AUTH_A = {"Authorization": f"Bearer {TOKEN_A}"}
AUTH_B = {"Authorization": f"Bearer {TOKEN_B}"}


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


@pytest.fixture
def client(tmp_path, clock) -> TestClient:
    settings = Settings(
        db_path=str(tmp_path / "veridian.db"),
        tokens={TOKEN_A: "client_a", TOKEN_B: "client_b"},
        routes_file=str(ROUTES_FILE),
        learn_delay_min=10,
        clock=clock,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def post_events(client: TestClient, events: list[dict], headers: dict = AUTH_A):
    return client.post("/v1/events", json={"events": events}, headers=headers)


def days_ago(days: int, hour: int = 10) -> datetime:
    return (NOW - timedelta(days=days)).replace(hour=hour, minute=0, second=0)
