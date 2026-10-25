import sqlite3
from dataclasses import dataclass

from .decisions import bump_version, create_decision, session_has_block
from .events import Event, format_ts
from .profile import build_profile, is_blocked, network_of
from .route_classes import RouteClasses, route_key
from .settings import Settings

RATIONALE = (
    "La ruta crítica {route_key} nunca la usó este usuario y el pedido vino de una red "
    "nueva ({network}). Se bloquean la sesión y la IP, no el usuario."
)

GUARDED_CLASSES = ("critical", "sensitive")


@dataclass
class Deviation:
    """An event that differs from the user's profile, to be judged by the AI."""

    client_id: str
    event: Event
    signals: dict
    profile: dict
    block_id: str | None = None  # set when the critical rule already blocked synchronously


def session_path(conn: sqlite3.Connection, client_id: str, event: Event) -> list[str]:
    """Ordered routes of the session, up to and including the event."""
    rows = conn.execute(
        "SELECT route FROM events WHERE client_id = ? AND session_id = ? AND ts <= ? ORDER BY ts, id",
        (client_id, event.session_id, format_ts(event.ts)),
    )
    return [row["route"] for row in rows]


def evaluate_event(
    conn: sqlite3.Connection, settings: Settings, route_classes: RouteClasses, client_id: str, event: Event
) -> Deviation | None:
    """Detect a deviation in a newly ingested event and apply the critical rule.

    The critical rule blocks synchronously. Every deviation is returned so the AI can judge it.
    """
    if session_has_block(conn, client_id, event.session_id):
        return None

    route_class = route_classes.classify(event.method, event.route)
    profile = build_profile(conn, client_id, event.user_id, settings.clock(), settings.learn_delay_min)
    key = route_key(event.method, event.route)
    network = network_of(str(event.ip))
    new_network = network not in profile.networks
    new_device = bool(event.device_id) and event.device_id not in profile.devices
    route_never_used = key not in profile.routes
    if not (new_network or new_device or (route_never_used and route_class in GUARDED_CLASSES)):
        return None

    signals = {
        "new_network": new_network,
        "new_device": new_device,
        "route": event.route,
        "route_class": route_class,
        "route_never_used": route_never_used,
        "path": session_path(conn, client_id, event),
    }
    deviation = Deviation(
        client_id=client_id,
        event=event,
        signals=signals,
        profile=profile.to_dict(blocked=is_blocked(conn, client_id, event.user_id)),
    )
    if route_class == "critical" and route_never_used and new_network:
        deviation.block_id = create_decision(
            conn,
            client_id,
            ts=settings.clock(),
            user_id=event.user_id,
            session_id=event.session_id,
            ip=str(event.ip),
            decision="block",
            signals=signals,
            rationale=RATIONALE.format(route_key=key, network=network),
            event_ids=[event.id],
        )
        bump_version(conn, client_id)
    return deviation
