"""Shared stdlib helpers for the scripts that talk to a running Veridian engine."""

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def load_dotenv() -> None:
    """Fill unset variables from the nearest .env above the cwd (stdlib only, like the scripts)."""
    for folder in (Path.cwd(), *Path.cwd().parents):
        dotenv = folder / ".env"
        if dotenv.is_file():
            break
    else:
        return
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    """--base-url and --token, defaulting to VERIDIAN_BASE_URL and VERIDIAN_TOKEN."""
    parser.add_argument("--base-url", default=os.environ.get("VERIDIAN_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.environ.get("VERIDIAN_TOKEN"), help="never printed")


def request(
    base_url: str, token: str, method: str, path: str, body: dict | None = None, headers: dict | None = None
):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")
