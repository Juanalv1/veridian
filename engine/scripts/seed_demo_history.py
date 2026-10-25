"""Preload a demo presenter's history on a Veridian engine.

Standard library only. Sends one session per day and hour, weekends included, from the
given IP (measure the event wifi that morning) and routes, old enough to be learned:

    py -3.12 engine/scripts/seed_demo_history.py --base-url https://<service>.onrender.com \\
        --user-id u_presenter --ip 200.44.32.17 --hours 13,14,15,16 [--device-id <cookie>]

--hours are UTC. Then prints how many events were accepted and the resulting profile.
"""

import argparse
import json
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from engine_client import add_connection_args, load_dotenv, request

DEFAULT_ROUTES = "POST /login,GET /invoices,GET /invoices/{id}"
MAX_BATCH = 1000  # the engine rejects larger batches
STEP_MINUTES = 3  # gap between requests inside a session
LEARN_DELAY_MIN = 10  # the engine's default; events newer than this are not learned yet
USER_AGENT = "Chrome 141 / Windows"


def parse_hours(raw: str) -> list[int]:
    hours = sorted({int(part) for part in raw.split(",") if part.strip()})
    if not hours or any(hour < 0 or hour > 23 for hour in hours):
        raise ValueError(f"hours must be UTC hours from 0 to 23: {raw!r}")
    return hours


def parse_routes(raw: str) -> list[tuple[str, str]]:
    """Parse "POST /login,GET /invoices" into [("POST", "/login"), ("GET", "/invoices")]."""
    routes = []
    for part in raw.split(","):
        method, _, route = part.strip().partition(" ")
        route = route.strip()
        if not method.isalpha() or not route.startswith("/"):
            raise ValueError(f"route must look like 'METHOD /route': {part.strip()!r}")
        routes.append((method.upper(), route))
    return routes


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_history(
    user_id: str,
    ip: str,
    device_id: str,
    days: int,
    hours: list[int],
    routes: list[tuple[str, str]],
    now: datetime,
    run_id: str,
    learn_delay_min: int = LEARN_DELAY_MIN,
) -> list[dict]:
    """One session per day (every day, weekends included) and hour, skipping anything too recent to learn."""
    cutoff = now - timedelta(minutes=learn_delay_min)
    events = []
    for day in range(1, days + 1):
        for hour in hours:
            start = (now - timedelta(days=day)).replace(hour=hour, minute=0, second=0, microsecond=0)
            session_id = f"s_seed_{run_id}_{day}_{hour}"
            for step, (method, route) in enumerate(routes):
                ts = start + timedelta(minutes=step * STEP_MINUTES)
                if ts > cutoff:
                    continue
                events.append(
                    {
                        "id": f"e_seed_{run_id}_{day}_{hour}_{step}",
                        "ts": iso(ts),
                        "user_id": user_id,
                        "session_id": session_id,
                        "ip": ip,
                        "user_agent": USER_AGENT,
                        "device_id": device_id,
                        "method": method,
                        "route": route,
                        "status": 200,
                        "bytes": 512,
                    }
                )
    return events


def batches(events: list[dict], size: int = MAX_BATCH) -> list[list[dict]]:
    return [events[start : start + size] for start in range(0, len(events), size)]


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--ip", required=True, help="the event wifi IP")
    parser.add_argument("--device-id", default="", help="the middleware's device cookie, if known")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--hours", required=True, help="UTC hours, e.g. 13,14,15,16")
    parser.add_argument("--routes", default=DEFAULT_ROUTES, help='e.g. "POST /login,GET /invoices"')
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("set VERIDIAN_TOKEN or pass --token")
    try:
        hours, routes = parse_hours(args.hours), parse_routes(args.routes)
    except ValueError as error:
        parser.error(str(error))

    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%d%H%M%S")  # unique ids per run, so reruns are not deduplicated
    events = build_history(args.user_id, args.ip, args.device_id, args.days, hours, routes, now, run_id)
    try:
        accepted = 0
        for batch in batches(events):
            status, body = request(args.base_url, args.token, "POST", "/v1/events", {"events": batch})
            if status != 202:
                print(f"batch rejected: {status} {body}", file=sys.stderr)
                return 1
            accepted += body["accepted"]
        print(f"sent {len(events)} events, accepted {accepted}")
        status, body = request(args.base_url, args.token, "GET", f"/v1/users/{quote(args.user_id, safe='')}/profile")
        print(f"\nGET profile -> {status}")
        print(json.dumps(body, indent=2, ensure_ascii=False))
    except urllib.error.URLError as error:
        print(f"cannot reach {args.base_url}: {error.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
