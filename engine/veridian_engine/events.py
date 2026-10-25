import sqlite3
from datetime import timezone
from ipaddress import IPv4Address, IPv6Address

from pydantic import AwareDatetime, BaseModel, Field

MAX_BATCH = 1000


class Event(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    ts: AwareDatetime
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    ip: IPv4Address | IPv6Address
    user_agent: str = Field(default="", max_length=512)
    device_id: str | None = Field(default="", max_length=128)
    method: str = Field(min_length=1, max_length=16)
    route: str = Field(min_length=1, max_length=512, pattern=r"^/")
    status: int = Field(ge=100, le=599)
    bytes: int = Field(ge=0)


class EventBatch(BaseModel):
    events: list[Event] = Field(max_length=MAX_BATCH)


def format_ts(value) -> str:
    """Normalize to a fixed-width UTC string so text comparison matches time order."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def insert_events(conn: sqlite3.Connection, client_id: str, events: list[Event]) -> list[Event]:
    """Store events, ignoring ids already seen for this client. Return the new ones."""
    inserted = []
    for event in events:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO events (client_id, id, ts, user_id, session_id, ip, user_agent,"
            " device_id, method, route, status, bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                client_id,
                event.id,
                format_ts(event.ts),
                event.user_id,
                event.session_id,
                str(event.ip),
                event.user_agent,
                event.device_id or "",
                event.method.upper(),
                event.route,
                event.status,
                event.bytes,
            ),
        )
        if cursor.rowcount == 1:
            inserted.append(event)
    return inserted
