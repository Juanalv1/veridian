"""Prepare the live demo in one step (HANDOFF, Dev B item 5).

Standard library only. Configuration comes from engine/.env (or the environment):

    VERIDIAN_BASE_URL      the engine, e.g. https://veridian-engine.onrender.com
    VERIDIAN_TOKEN         the demo project's SDK key (sends the history)
    VERIDIAN_PANEL_TOKEN   the panel credential (reads activity and profile, erases data)
    DEMO_PROJECT_ID        default "demo"
    DEMO_USER_ID           the presenter's user id in the demo app, default "u_42"
    DEMO_ACTOR_EMAIL       default "ensayo@veridian.demo"; invite it once as owner of the
                           Demo organization (panel > Miembros). The script signs in as it.
    RENDER_API_KEY + DEMO_RENDER_SERVICE_ID   optional: restart the demo app at the end, so
                           its middleware drops a stale blocklist at once.

Two steps:

    py -3.12 engine/scripts/prepare_demo.py --calibrar [--minutos 10]
        Browse the demo app as the presenter for a few minutes first (event wifi, own laptop).
        Takes every IP and device of the presenter in the last minutes from the activity, so a
        rotating IP (CGNAT) is covered without copying IPs by hand. Saved in
        engine/.demo-presenter.json (git-ignored).

    py -3.12 engine/scripts/prepare_demo.py
        Erases the project's data (DELETE /v1/projects/{id}/data), seeds 14 days of history with
        the calibrated IPs and devices at the current UTC hour +-1, checks the profile and the
        empty blocklist, and prints one line: "listo: 3 redes, 1 dispositivo, 14 días".

No secret is ever printed.
"""

import argparse
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from engine_client import load_dotenv, request as http_request
from seed_demo_history import DEFAULT_ROUTES, batches, build_history, parse_routes

DAYS = 14
STATE_FILE = Path(__file__).resolve().parent.parent / ".demo-presenter.json"


@dataclass
class Config:
    base_url: str
    sdk_token: str
    panel_token: str
    project_id: str = "demo"
    user_id: str = "u_42"
    actor_email: str = "ensayo@veridian.demo"
    state_file: str = str(STATE_FILE)

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            base_url=env("VERIDIAN_BASE_URL", "https://veridian-engine.onrender.com"),
            sdk_token=env("VERIDIAN_TOKEN", ""),
            panel_token=env("VERIDIAN_PANEL_TOKEN", ""),
            project_id=env("DEMO_PROJECT_ID", "demo"),
            user_id=env("DEMO_USER_ID", "u_42"),
            actor_email=env("DEMO_ACTOR_EMAIL", "ensayo@veridian.demo"),
        )


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def network_of(ip: str) -> str:
    prefix = 64 if ":" in ip else 24
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def calibrate(activity: list[dict], user_id: str, since: datetime) -> dict:
    """Every IP and device of `user_id` in the activity at or after `since`; nobody else's."""
    ips, devices = set(), set()
    for row in activity:
        ts = datetime.strptime(row["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if row.get("user_id") != user_id or ts < since:
            continue
        ips.add(row["ip"])
        if row.get("device_id"):
            devices.add(row["device_id"])
    return {"user_id": user_id, "ips": sorted(ips), "devices": sorted(devices)}


def sign_in(cfg: Config, request) -> tuple[str | None, str | None]:
    """Sign in as the rehearsal person (accepts its invitation). Returns (person id, error)."""
    status, body = request(cfg.base_url, cfg.panel_token, "POST", "/v1/people/login",
                           {"google_sub": f"veridian-rehearsal:{cfg.actor_email}", "email": cfg.actor_email,
                            "name": "Ensayo de demo"})
    if status != 200:
        return None, f"cannot sign in to the engine as the panel ({status}): check VERIDIAN_PANEL_TOKEN."
    person = body["person"]["id"]
    status, project = request(cfg.base_url, cfg.panel_token, "GET", f"/v1/projects/{quote(cfg.project_id)}",
                              headers={"X-Veridian-Actor": person})
    if status != 200 or project.get("role") != "owner":
        return None, (f"{cfg.actor_email} is not owner of the project's organization. In the panel, "
                      f"Miembros > Invitar, invite {cfg.actor_email} as owner, then run this again.")
    return person, None


def panel_headers(cfg: Config, person: str) -> dict:
    return {"X-Veridian-Actor": person, "X-Veridian-Project": cfg.project_id}


def run_calibration(cfg: Config, minutes: int, request=http_request, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    person, error = sign_in(cfg, request)
    if error:
        return fail(error)
    status, body = request(cfg.base_url, cfg.panel_token, "GET", "/v1/activity?limit=200",
                           headers=panel_headers(cfg, person))
    if status != 200:
        return fail(f"cannot read the activity ({status}).")
    calibration = calibrate(body["activity"], cfg.user_id, now - timedelta(minutes=minutes))
    if not calibration["ips"]:
        return fail(f"no access from {cfg.user_id} in the last {minutes} minutes: browse the demo app as the "
                    "presenter first, on the event wifi, then run --calibrar again.")
    Path(cfg.state_file).write_text(json.dumps({**calibration, "calibrated_at": now.isoformat()}, indent=2))
    print(f"calibrado: {len(calibration['ips'])} IP en {len({network_of(ip) for ip in calibration['ips']})} redes, "
          f"{len(calibration['devices'])} dispositivo(s). Guardado en {cfg.state_file}")
    return 0


def presenter_history(calibration: dict, now: datetime, run_id: str) -> list[dict]:
    """14 days at the current UTC hour +-1, one session per day, hour, IP and device."""
    hours = sorted({(now.hour + delta) % 24 for delta in (-1, 0, 1)})
    routes = parse_routes(DEFAULT_ROUTES)
    events = []
    for i, ip in enumerate(calibration["ips"]):
        for j, device in enumerate(calibration["devices"] or [""]):
            events += build_history(calibration["user_id"], ip, device, DAYS, hours, routes, now, f"{run_id}_{i}_{j}")
    return events


def plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def run_preparation(cfg: Config, request=http_request, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    try:
        calibration = json.loads(Path(cfg.state_file).read_text())
    except FileNotFoundError:
        return fail(f"no calibration in {cfg.state_file}: run prepare_demo.py --calibrar first.")
    person, error = sign_in(cfg, request)
    if error:
        return fail(error)

    status, body = request(cfg.base_url, cfg.panel_token, "DELETE", f"/v1/projects/{quote(cfg.project_id)}/data",
                           headers={"X-Veridian-Actor": person})
    if status != 200:
        return fail(f"cannot erase the project's data ({status}).")

    events = presenter_history(calibration, now, now.strftime("%Y%m%d%H%M%S"))
    for batch in batches(events):
        status, body = request(cfg.base_url, cfg.sdk_token, "POST", "/v1/events", {"events": batch})
        if status != 202:
            return fail(f"history batch rejected ({status}): check VERIDIAN_TOKEN.")

    status, profile = request(cfg.base_url, cfg.panel_token, "GET",
                              f"/v1/users/{quote(cfg.user_id, safe='')}/profile", headers=panel_headers(cfg, person))
    if status != 200:
        return fail(f"cannot read the profile ({status}).")
    expected_networks = {network_of(ip) for ip in calibration["ips"]}
    problems = []
    if set(profile["networks"]) != expected_networks:
        problems.append(f"networks {profile['networks']} instead of {sorted(expected_networks)}")
    if not set(calibration["devices"]) <= set(profile["devices"]):
        problems.append(f"devices {profile['devices']} miss {calibration['devices']}")
    if profile["history_days"] < DAYS or profile["state"] != "normal":
        problems.append(f"{profile['history_days']} days, state {profile['state']}")
    status, blocklist = request(cfg.base_url, cfg.sdk_token, "GET", "/v1/blocklist")
    if status != 200 or blocklist.get("sessions") or blocklist.get("ips") or blocklist.get("users"):
        problems.append("the blocklist is not empty")
    if problems:
        return fail("the profile is not ready: " + "; ".join(problems))

    restart_demo_app()
    print(f"listo: {plural(len(expected_networks), 'red', 'redes')}, "
          f"{plural(len(profile['devices']), 'dispositivo', 'dispositivos')}, {profile['history_days']} días")
    return 0


def restart_demo_app() -> None:
    """Optional: restart the demo app on Render so its middleware reloads the (now empty) blocklist
    at once. A rehearsal once kept blocking the previous attacker until the app was restarted."""
    key, service = os.environ.get("RENDER_API_KEY", ""), os.environ.get("DEMO_RENDER_SERVICE_ID", "")
    if not key or not service:
        return
    req = urllib.request.Request(f"https://api.render.com/v1/services/{service}/restart", method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20):
            print("app de demo reiniciada")
    except urllib.error.URLError as error:
        print(f"could not restart the demo app: {getattr(error, 'reason', error)}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calibrar", action="store_true", help="take the presenter's IPs and devices")
    parser.add_argument("--minutos", type=int, default=10, help="how far back --calibrar looks")
    args = parser.parse_args(argv)
    cfg = Config.from_env()
    if not cfg.sdk_token or not cfg.panel_token:
        parser.error("set VERIDIAN_TOKEN and VERIDIAN_PANEL_TOKEN (engine/.env)")
    try:
        return run_calibration(cfg, args.minutos) if args.calibrar else run_preparation(cfg)
    except urllib.error.URLError as error:
        return fail(f"cannot reach {cfg.base_url}: {error.reason}")


if __name__ == "__main__":
    sys.exit(main())
