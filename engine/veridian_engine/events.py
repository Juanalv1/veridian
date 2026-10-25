import re
import sqlite3
from datetime import timezone
from ipaddress import IPv4Address, IPv6Address
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, BeforeValidator, Field, ValidationError

MAX_BATCH = 1000
EMAIL_PATTERN = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$")
MAX_EMAIL_LENGTH = 254


def _clean_email(value):
    """A usable address, or None. A malformed one is dropped instead of failing the whole batch:
    the event matters more to the customer than the optional address."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.match(value):
        return None
    return value


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
    # Personal data of the end user, used only to email them (self-unlock). Never sent to the AI judge.
    user_email: Annotated[str | None, BeforeValidator(_clean_email)] = None


class EventBatch(BaseModel):
    # Items stay raw here and are validated one by one (`valid_events`): a single bad event
    # must not 422 the batch, because the SDKs keep re-sending a batch until it gets a 202.
    events: list[Any] = Field(max_length=MAX_BATCH)


def valid_events(items: list[Any]) -> tuple[list[Event], int]:
    """The items that are valid events, and how many were rejected."""
    valid = []
    for item in items:
        try:
            valid.append(Event.model_validate(item))
        except ValidationError:
            pass
    return valid, len(items) - len(valid)


def format_ts(value) -> str:
    """Normalize to a fixed-width UTC string so text comparison matches time order."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def insert_events(conn: sqlite3.Connection, project_id: str, events: list[Event]) -> list[Event]:
    """Store events, ignoring ids already seen for this client. Return the new ones."""
    inserted = []
    for event in events:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO events (project_id, id, ts, user_id, session_id, ip, user_agent,"
            " device_id, method, route, status, bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
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
            if event.user_email:
                remember_user_email(conn, project_id, event.user_id, event.user_email, format_ts(event.ts))
    return inserted


def remember_user_email(conn: sqlite3.Connection, project_id: str, user_id: str, email: str, ts: str) -> None:
    """Keep only the latest address per (project, user). `ts` is the event's time, so a late
    batch never replaces a newer address."""
    conn.execute(
        "INSERT INTO user_contact (project_id, user_id, email, updated_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (project_id, user_id) DO UPDATE SET email = excluded.email, updated_at = excluded.updated_at"
        " WHERE excluded.updated_at >= user_contact.updated_at",
        (project_id, user_id, email, ts),
    )
