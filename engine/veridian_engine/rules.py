import sqlite3
from dataclasses import dataclass
from datetime import timezone

from .decisions import bump_version, create_decision, session_has_block
from .events import Event, format_ts
from .profile import RATE_WINDOW, build_profile, is_blocked, network_of
from .route_classes import RouteClasses, route_key
from .settings import Settings

RATIONALE = (
    "La ruta crítica {route_key} nunca la usó este usuario y el pedido vino de una red "
    "nueva ({network}). Se bloquean la sesión y la IP, no el usuario."
)

RATE_RATIONALE = (
    "Ráfaga de {rate} pedidos por minuto, frente a un máximo habitual de {usual}: es el ritmo de "
    "un script, no de una persona. Se bloquea la sesión{ip_scope}."
)

GUARDED_CLASSES = ("critical", "sensitive")


@dataclass
class Deviation:
    """An event that differs from the user's profile, to be judged by the AI."""

    project_id: str
    event: Event
    signals: dict
    profile: dict
    block_id: str | None = None  # set when the critical rule already blocked synchronously


def session_events(conn: sqlite3.Connection, project_id: str, event: Event) -> list[sqlite3.Row]:
    """Ordered events of the session, up to and including the event."""
    return conn.execute(
        "SELECT id, method, route FROM events WHERE project_id = ? AND session_id = ? AND ts <= ? ORDER BY ts, id",
        (project_id, event.session_id, format_ts(event.ts)),
    ).fetchall()


def rate_per_min(conn: sqlite3.Connection, project_id: str, event: Event) -> int:
    """Events of the session in the 60 s up to and including the event."""
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE project_id = ? AND session_id = ? AND ts > ? AND ts <= ?",
        (project_id, event.session_id, format_ts(event.ts - RATE_WINDOW), format_ts(event.ts)),
    ).fetchone()
    return row[0]


def previous_route_key(rows: list[sqlite3.Row], event: Event) -> str | None:
    """The route key just before the event in its session, if any."""
    ids = [row["id"] for row in rows]
    index = ids.index(event.id) if event.id in ids else len(ids)
    if index == 0:
        return None
    previous = rows[index - 1]
    return route_key(previous["method"], previous["route"])


def evaluate_event(
    conn: sqlite3.Connection, settings: Settings, route_classes: RouteClasses, project_id: str, event: Event
) -> Deviation | None:
    """Detect a deviation in a newly ingested event and apply the critical rule.

    The critical rule blocks synchronously. Every deviation is returned so the AI can judge it.
    """
    if session_has_block(conn, project_id, event.session_id):
        return None

    route_class = route_classes.classify(event.method, event.route)
    # A session never vouches for itself: events delivered late (the SDK flushing after an outage)
    # would otherwise be learned by the time they are judged.
    profile = build_profile(conn, project_id, event.user_id, settings.clock(), settings.learn_delay_min,
                            own_session=event.session_id, own_ts=event.ts)
    key = route_key(event.method, event.route)
    network = network_of(str(event.ip))
    new_network = network not in profile.networks
    new_device = bool(event.device_id) and event.device_id not in profile.devices
    route_never_used = key not in profile.routes
    rate = rate_per_min(conn, project_id, event)
    rate_spike = rate > max(settings.rate_factor * profile.usual_max_rate_per_min, settings.rate_floor)
    # The owner's own network and device together: a first use of a critical route is the owner
    # doing something new, not someone else (the AI blocked the owner changing the bank account
    # 3 of 3 times in production). Without a device id the device is unknown, so it is still
    # judged. Sensitive routes (bulk export) are still judged in the background.
    known_place = not new_network and bool(event.device_id) and not new_device
    first_guarded_use = (route_never_used and route_class in GUARDED_CLASSES
                         and not (route_class == "critical" and known_place))
    # A rate spike is a deviation even on the owner's network and device: scripts and malware.
    if not (new_network or new_device or rate_spike or first_guarded_use):
        return None

    rows = session_events(conn, project_id, event)
    previous = previous_route_key(rows, event)
    hour = event.ts.astimezone(timezone.utc).hour
    signals = {
        "new_network": new_network,
        "new_device": new_device,
        "method": event.method,
        "route": event.route,
        "route_class": route_class,
        "route_never_used": route_never_used,
        "path": [row["route"] for row in rows],
        "rate_per_min": rate,
        "usual_max_rate_per_min": profile.usual_max_rate_per_min,
        "rate_spike": rate_spike,
        # Context for the judge only: on their own they never make a deviation.
        "new_transition": previous is not None and (previous, key) not in profile.transitions,
        "unusual_hour": bool(profile.hours) and hour not in profile.hours,
    }
    deviation = Deviation(
        project_id=project_id,
        event=event,
        signals=signals,
        profile=profile.to_dict(blocked=is_blocked(conn, project_id, event.user_id)),
    )
    if route_class == "critical" and route_never_used and new_network:
        block_ip, rationale = str(event.ip), RATIONALE.format(route_key=key, network=network)
    elif rate_spike:
        # A script on the owner's own network shares the owner's IP: block only its session there.
        block_ip = str(event.ip) if new_network else ""
        rationale = RATE_RATIONALE.format(
            rate=rate, usual=profile.usual_max_rate_per_min, ip_scope=" y la IP" if new_network else ""
        )
    else:
        return deviation
    deviation.block_id = create_decision(
        conn,
        project_id,
        ts=settings.clock(),
        user_id=event.user_id,
        session_id=event.session_id,
        ip=block_ip,
        decision="block",
        signals=signals,
        rationale=rationale,
        event_ids=[event.id],
    )
    bump_version(conn, project_id)
    return deviation
