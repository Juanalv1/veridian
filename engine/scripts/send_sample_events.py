"""Send a sample owner history and an attacker event to a running Veridian engine.

Standard library only, so it runs without the engine's virtualenv:

    VERIDIAN_TOKEN=dev python engine/scripts/send_sample_events.py [--base-url http://localhost:8000]

It sends 14 days of owner activity (old timestamps, owner's /24, invoices and viewing
the bank account), then an attacker session from a new network that changes the bank
account, and prints the blocklist and the decisions.
"""

import argparse
import json
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

from engine_client import add_connection_args, load_dotenv, request

USER_ID = "u_42"
OWNER_IP = "190.202.14.7"
OWNER_DEVICE = "d_owner"
ATTACKER_IP = "203.0.113.9"
ATTACKER_DEVICE = "d_attacker"
HISTORY_DAYS = 14


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def event(event_id, ts, session_id, ip, device_id, method, route, user_agent="Chrome 141 / Windows"):
    return {
        "id": event_id,
        "ts": iso(ts),
        "user_id": USER_ID,
        "session_id": session_id,
        "ip": ip,
        "user_agent": user_agent,
        "device_id": device_id,
        "method": method,
        "route": route,
        "status": 200,
        "bytes": 512,
    }


def owner_history(now: datetime, run_id: str) -> list[dict]:
    """One session per day for the last 14 days, from the owner's network and device."""
    owner_routes = [
        ("GET", "/login"),
        ("POST", "/login"),
        ("GET", "/invoices"),
        ("GET", "/invoices/{id}"),
        ("GET", "/payouts/bank-account"),
        ("POST", "/logout"),
    ]
    events = []
    for day in range(1, HISTORY_DAYS + 1):
        start = (now - timedelta(days=day)).replace(hour=9, minute=0, second=0, microsecond=0)
        session_id = f"s_owner_{run_id}_{day}"
        for step, (method, route) in enumerate(owner_routes):
            ts = start + timedelta(minutes=step * 5)
            events.append(event(f"e_{run_id}_h{day}_{step}", ts, session_id, OWNER_IP, OWNER_DEVICE, method, route))
    return events


def attacker_events(now: datetime, run_id: str) -> list[dict]:
    """A new session from an unseen network that goes straight to changing the bank account."""
    session_id = f"s_attacker_{run_id}"
    steps = [("GET", "/login"), ("POST", "/login"), ("POST", "/payouts/bank-account")]
    return [
        event(
            f"e_{run_id}_a{step}",
            now - timedelta(seconds=(len(steps) - 1 - step) * 10),
            session_id,
            ATTACKER_IP,
            ATTACKER_DEVICE,
            method,
            route,
            user_agent="Firefox 130 / Linux",
        )
        for step, (method, route) in enumerate(steps)
    ]


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    args = parser.parse_args()
    if not args.token:
        parser.error("set VERIDIAN_TOKEN or pass --token")

    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%d%H%M%S")  # unique ids per run, so reruns are not deduplicated

    try:
        status, body = request(args.base_url, args.token, "POST", "/v1/events", {"events": owner_history(now, run_id)})
        print(f"owner history: {status} {body}")
        status, body = request(args.base_url, args.token, "POST", "/v1/events", {"events": attacker_events(now, run_id)})
        print(f"attacker: {status} {body}")
        for path in ("/v1/blocklist", "/v1/decisions?limit=5"):
            status, body = request(args.base_url, args.token, "GET", path)
            print(f"\nGET {path} -> {status}")
            print(json.dumps(body, indent=2, ensure_ascii=False))
    except urllib.error.URLError as error:
        print(f"cannot reach {args.base_url}: {error.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
