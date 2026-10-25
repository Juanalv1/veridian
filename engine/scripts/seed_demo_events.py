"""Send a few days of normal traffic for three demo users to an engine, so the panel's Usuarios page has data.

    VERIDIAN_ENGINE_URL=https://... VERIDIAN_API_KEY=... python engine/scripts/seed_demo_events.py

Needs the project's SDK API key (the one the middleware uses), not the panel token.
"""
import json
import os
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

URL = os.environ.get("VERIDIAN_ENGINE_URL", "").rstrip("/")
KEY = os.environ.get("VERIDIAN_API_KEY", "")
if not URL or not KEY:
    sys.exit("Set VERIDIAN_ENGINE_URL and VERIDIAN_API_KEY")

USERS = [
    ("demo_ana", "190.120.10.5", "Mozilla/5.0 (Windows NT 10.0) Chrome/126", "dev_ana"),
    ("demo_luis", "181.50.20.8", "Mozilla/5.0 (Macintosh) Safari/17", "dev_luis"),
    ("demo_sofia", "200.35.7.2", "Mozilla/5.0 (X11; Linux) Firefox/127", "dev_sofia"),
]
ROUTES = [("GET", "/invoices"), ("GET", "/invoices/42"), ("GET", "/profile"), ("GET", "/dashboard")]

rng = random.Random(7)
now = datetime.now(timezone.utc)
events = []
for day in range(3, 0, -1):
    for user_id, ip, agent, device in USERS:
        start = now - timedelta(days=day, hours=rng.randint(0, 3))
        for i in range(12):
            method, route = rng.choice(ROUTES)
            events.append({
                "id": f"seed-{user_id}-{day}-{i}",
                "ts": (start + timedelta(minutes=i * 4)).isoformat(),
                "user_id": user_id, "session_id": f"s-{user_id}-{day}", "ip": ip,
                "user_agent": agent, "device_id": device, "method": method, "route": route,
                "status": 200, "bytes": rng.randint(500, 9000),
            })

request = urllib.request.Request(
    f"{URL}/v1/events", data=json.dumps({"events": events}).encode(), method="POST",
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=60) as response:
    print(response.status, response.read().decode())
