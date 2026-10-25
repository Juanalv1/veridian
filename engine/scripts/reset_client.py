"""Wipe the calling client's data on a Veridian engine, between demo rehearsals.

Standard library only. The engine must run with VERIDIAN_RESET_TOKEN set; otherwise (or with a
wrong secret) the endpoint answers 404. Only the project that owns the SDK token is affected:

    py -3.12 engine/scripts/reset_client.py --base-url https://<service>.onrender.com [--yes]

The secret comes from VERIDIAN_RESET_TOKEN (environment or .env) or --reset-token. Without --yes
it asks you to type the base URL to confirm. Neither token is ever printed.
"""

import argparse
import os
import sys
import urllib.error

from engine_client import add_connection_args, load_dotenv, request


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument(
        "--reset-token", default=os.environ.get("VERIDIAN_RESET_TOKEN"), help="rehearsal secret; never printed"
    )
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("set VERIDIAN_TOKEN or pass --token")
    if not args.reset_token:
        parser.error("set VERIDIAN_RESET_TOKEN or pass --reset-token")

    if not args.yes:
        print(f"This deletes every event, decision and block of this token's client on {args.base_url}.")
        typed = input("Type the base URL to confirm: ").strip()
        if typed.rstrip("/") != args.base_url.rstrip("/"):
            print("Not confirmed; nothing was deleted.")
            return 1

    try:
        status, body = request(
            args.base_url, args.token, "POST", "/v1/admin/reset", headers={"X-Veridian-Reset-Token": args.reset_token}
        )
    except urllib.error.URLError as error:
        print(f"cannot reach {args.base_url}: {error.reason}", file=sys.stderr)
        return 1
    if status == 404:
        print("reset disabled or reset token wrong (404).", file=sys.stderr)
        return 1
    if status != 200:
        print(f"reset failed: {status} {body}", file=sys.stderr)
        return 1
    print(f"Reset done. Blocklist is empty at version {body['version']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
