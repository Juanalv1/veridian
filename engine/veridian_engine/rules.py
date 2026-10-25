import sqlite3

from .decisions import bump_version, create_decision, session_has_block
from .events import Event, format_ts
from .profile import build_profile, network_of
from .route_classes import RouteClasses, route_key
from .settings import Settings

RATIONALE = (
    "Critical route {route_key} was never used by this user and the request came from "
    "an unseen network ({network}). Blocking the session and IP, not the user."
)


def session_path(conn: sqlite3.Connection, client_id: str, event: Event) -> list[str]:
    """Ordered routes of the session, up to and including the event."""
    rows = conn.execute(
        "SELECT route FROM events WHERE client_id = ? AND session_id = ? AND ts <= ? ORDER BY ts, id",
        (client_id, event.session_id, format_ts(event.ts)),
    )
    return [row["route"] for row in rows]


def evaluate_event(
    conn: sqlite3.Connection, settings: Settings, route_classes: RouteClasses, client_id: str, event: Event
) -> str | None:
    """Apply the first-milestone rule to a newly ingested event. Return the decision id if it blocks."""
    route_class = route_classes.classify(event.method, event.route)
    if route_class != "critical" or session_has_block(conn, client_id, event.session_id):
        return None

    profile = build_profile(conn, client_id, event.user_id, settings.clock(), settings.learn_delay_min)
    key = route_key(event.method, event.route)
    network = network_of(str(event.ip))
    if key in profile.routes or network in profile.networks:
        return None

    decision_id = create_decision(
        conn,
        client_id,
        ts=settings.clock(),
        user_id=event.user_id,
        session_id=event.session_id,
        ip=str(event.ip),
        decision="block",
        signals={
            "new_network": True,
            "new_device": not event.device_id or event.device_id not in profile.devices,
            "route": event.route,
            "route_class": route_class,
            "route_never_used": True,
            "path": session_path(conn, client_id, event),
        },
        rationale=RATIONALE.format(route_key=key, network=network),
        event_ids=[event.id],
    )
    bump_version(conn, client_id)
    return decision_id
