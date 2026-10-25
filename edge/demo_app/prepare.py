"""One-tap demo preparation, the in-app version of engine/scripts/prepare_demo.py.

Erases the demo project's data, seeds the presenter's history from the phone that pressed
the button (its IP and Veridian device cookie, no calibration file), checks the profile and
then empties the running middleware's blocklist, so no restart is needed. The seed shape
matches engine/scripts/seed_demo_history.py: 14 days at the current UTC hour +-1.
"""

import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

DAYS = 14
ROUTES = [("POST", "/login"), ("GET", "/invoices"), ("GET", "/invoices/{id}")]
MAX_BATCH = 1000  # the engine rejects larger batches
STEP_MINUTES = 3  # gap between requests inside a session
LEARN_DELAY_MIN = 10  # events newer than this are not learned yet
USER_AGENT = "Chrome 141 / Windows"
TIMEOUT = 30.0  # the engine on Render may be waking up


class PrepareError(Exception):
    """A step failed; the message is safe to show (it never carries a secret)."""


@dataclass
class Settings:
    panel_token: str
    project_id: str = "demo"
    user_id: str = "u_42"
    actor_email: str = "ensayo@veridian.demo"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def network_of(ip: str) -> str:
    prefix = 64 if ":" in ip else 24
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def presenter_history(user_id: str, ip: str, device_id: str, now: datetime) -> list[dict]:
    """One session per day and hour (current UTC hour +-1), skipping anything too recent to learn."""
    hours = sorted({(now.hour + delta) % 24 for delta in (-1, 0, 1)})
    run_id = now.strftime("%Y%m%d%H%M%S")
    cutoff = now - timedelta(minutes=LEARN_DELAY_MIN)
    events = []
    for day in range(1, DAYS + 1):
        for hour in hours:
            start = (now - timedelta(days=day)).replace(hour=hour, minute=0, second=0, microsecond=0)
            for step, (method, route) in enumerate(ROUTES):
                ts = start + timedelta(minutes=step * STEP_MINUTES)
                if ts > cutoff:
                    continue
                events.append({
                    "id": f"e_seed_{run_id}_{day}_{hour}_{step}",
                    "ts": _iso(ts),
                    "user_id": user_id,
                    "session_id": f"s_seed_{run_id}_{day}_{hour}",
                    "ip": ip,
                    "user_agent": USER_AGENT,
                    "device_id": device_id,
                    "method": method,
                    "route": route,
                    "status": 200,
                    "bytes": 512,
                })
    return events


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


async def prepare(middleware, settings: Settings, ip: str, device_id: str,
                  now: datetime | None = None) -> str:
    """Run the preparation against the middleware's engine; returns the "Listo: ..." line."""
    now = now or datetime.now(timezone.utc)
    client: httpx.AsyncClient = middleware.client
    base = middleware.base_url
    panel = {"Authorization": f"Bearer {settings.panel_token}"}
    sdk = {"Authorization": f"Bearer {middleware.token}"}
    project = quote(settings.project_id, safe="")

    async def call(method: str, path: str, headers: dict, **kwargs) -> httpx.Response:
        try:
            return await client.request(method, f"{base}{path}", headers=headers, timeout=TIMEOUT, **kwargs)
        except httpx.HTTPError as error:
            raise PrepareError(f"No se pudo hablar con el motor ({type(error).__name__}).") from None

    r = await call("POST", "/v1/people/login", panel, json={
        "google_sub": f"veridian-rehearsal:{settings.actor_email}", "email": settings.actor_email,
        "name": "Ensayo de demo"})
    if r.status_code != 200:
        raise PrepareError(f"El motor rechazó la credencial del panel ({r.status_code}): revisa VERIDIAN_PANEL_TOKEN.")
    actor = {**panel, "X-Veridian-Actor": r.json()["person"]["id"]}
    r = await call("GET", f"/v1/projects/{project}", actor)
    if r.status_code != 200 or r.json().get("role") != "owner":
        raise PrepareError(f"{settings.actor_email} no es owner de la organización del proyecto. "
                           f"Invítalo como owner en el panel (Miembros) y vuelve a intentar.")

    r = await call("DELETE", f"/v1/projects/{project}/data", actor)
    if r.status_code != 200:
        raise PrepareError(f"No se pudieron borrar los datos del proyecto ({r.status_code}).")

    events = presenter_history(settings.user_id, ip, device_id, now)
    for start in range(0, len(events), MAX_BATCH):
        r = await call("POST", "/v1/events", sdk, json={"events": events[start:start + MAX_BATCH]})
        if r.status_code != 202:
            raise PrepareError(f"El motor rechazó el historial ({r.status_code}): revisa VERIDIAN_TOKEN.")

    r = await call("GET", f"/v1/users/{quote(settings.user_id, safe='')}/profile",
                   {**actor, "X-Veridian-Project": settings.project_id})
    if r.status_code != 200:
        raise PrepareError(f"No se pudo leer el perfil ({r.status_code}).")
    profile = r.json()
    problems = []
    if profile.get("networks") != [network_of(ip)]:
        problems.append(f"redes {profile.get('networks')}")
    if device_id not in profile.get("devices", []):
        problems.append("falta el dispositivo")
    if profile.get("history_days", 0) < DAYS or profile.get("state") != "normal":
        problems.append(f"{profile.get('history_days')} días, estado {profile.get('state')}")
    if problems:
        raise PrepareError("El perfil no quedó listo: " + "; ".join(problems))

    # Forget the previous rehearsal's blocks and reload the (now empty) list in full.
    middleware.blocked = {"users": set(), "sessions": set(), "ips": set()}
    middleware.self_unlock_sessions = set()
    middleware.blocklist_version = None
    await middleware.refresh_blocklist()

    return (f"Listo: {_plural(len(profile['networks']), 'red', 'redes')}, "
            f"{_plural(len(profile['devices']), 'dispositivo', 'dispositivos')}, {profile['history_days']} días")
