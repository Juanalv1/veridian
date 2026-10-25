"""In-memory stand-in for the Veridian engine.

Honors the five contracts in docs/REPARTO.md and mirrors Dev 1's first
milestone rule: a `critical` route the user never used, hit from a /24 never
seen for that user, produces a `block` decision and blocks its session and IP.
No AI, no persistence.
"""

import ipaddress
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

DEV_TOKEN = "dev-token"

# Mirrors the routes table in docs/REPARTO.md, keyed "METHOD /template".
DEFAULT_ROUTES: dict[str, str] = {
    "GET /login": "normal",
    "POST /login": "normal",
    "POST /logout": "normal",
    "GET /invoices": "normal",
    "GET /invoices/{id}": "normal",
    "GET /invoices/export": "sensitive",
    "GET /payouts/bank-account": "sensitive",
    "POST /payouts/bank-account": "critical",
}

# Owner history for the local demo (VERIDIAN_SEED=demo). 127.0.0.1 is the
# address the owner has when browsing the demo app on this machine.
DEMO_SEED = [
    {
        "user_id": "u_42",
        "ips": ["127.0.0.1"],
        "routes": [
            "POST /login",
            "GET /invoices",
            "GET /invoices/{id}",
            "GET /payouts/bank-account",
        ],
    }
]


class Unauthorized(Exception):
    pass


class EventIn(BaseModel):
    id: str
    ts: str
    user_id: str
    session_id: str | None = None
    ip: str
    user_agent: str | None = None
    device_id: str | None = None
    method: str
    route: str
    status: int
    bytes: int


class EventsIn(BaseModel):
    events: list[EventIn]


def network_of(ip: str) -> str:
    """/24 for IPv4 (/48 for IPv6); unparsable values map to themselves."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    prefix = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def verdict_of(decision: dict) -> str:
    """Contract 5 verdict of a decision: normal | review | block."""
    if decision.get("status") == "marked_normal":
        return "normal"
    return {"block": "block", "alert": "review", "step_up": "review"}.get(decision.get("decision"), "normal")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Profile:
    networks: set[str] = field(default_factory=set)
    devices: set[str] = field(default_factory=set)
    route_keys: set[str] = field(default_factory=set)  # "METHOD /template"
    hours: set[int] = field(default_factory=set)

    def learn(self, event: EventIn) -> None:
        self.networks.add(network_of(event.ip))
        if event.device_id:
            self.devices.add(event.device_id)
        self.route_keys.add(f"{event.method} {event.route}")
        try:
            self.hours.add(int(event.ts[11:13]))
        except ValueError:
            pass


@dataclass
class ClientState:
    """Everything one client (one token) owns; clients are isolated."""

    routes: dict[str, str]
    profiles: dict[str, Profile] = field(default_factory=dict)
    seen_event_ids: set[str] = field(default_factory=set)
    version: int = 0
    sessions: list[str] = field(default_factory=list)
    ips: list[str] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)  # oldest first
    pending_events: dict[str, EventIn] = field(default_factory=dict)
    log: list[EventIn] = field(default_factory=list)  # every accepted event, oldest first
    normal: set[str] = field(default_factory=set)
    blocked_users: set[str] = field(default_factory=set)
    # Events are learned only after `learn_delay` seconds, like the real engine
    # ("events older than N minutes that were not flagged"). Otherwise an
    # attacker's own login would whitelist their network before they act.
    learn_delay: float = 0.0
    clock: object = time.monotonic
    pending_learn: deque = field(default_factory=deque)

    def _learn_later(self, event: EventIn) -> None:
        self.pending_learn.append((self.clock(), event))
        self.apply_pending()

    def apply_pending(self) -> None:
        now = self.clock()
        while self.pending_learn and now - self.pending_learn[0][0] >= self.learn_delay:
            _, event = self.pending_learn.popleft()
            self.profile(event.user_id).learn(event)

    def profile(self, user_id: str) -> Profile:
        return self.profiles.setdefault(user_id, Profile())

    def seed(self, user_id: str, ips=(), routes=(), devices=()) -> None:
        p = self.profile(user_id)
        p.networks.update(network_of(i) for i in ips)
        p.route_keys.update(routes)
        p.devices.update(devices)

    def ingest(self, event: EventIn) -> None:
        self.log.append(event)
        if event.status == 403:
            # Rejected by the middleware: the app never saw it, so it says
            # nothing about the user. Never learned, never judged.
            return
        self.apply_pending()
        profile = self.profile(event.user_id)
        key = f"{event.method} {event.route}"
        route_class = self.routes.get(key, "normal")
        net = network_of(event.ip)
        new_network = net not in profile.networks
        never_used = key not in profile.route_keys
        # A user with no history cannot be judged: learn instead of blocking.
        has_history = bool(profile.networks)
        if route_class == "critical" and never_used and new_network and has_history:
            self._block(event, route_class)
        else:
            self._learn_later(event)

    def _block(self, event: EventIn, route_class: str) -> None:
        if event.session_id and event.session_id not in self.sessions:
            self.sessions.append(event.session_id)
        if event.ip not in self.ips:
            self.ips.append(event.ip)
        self.version += 1
        self.blocked_users.add(event.user_id)
        decision_id = f"d_{len(self.decisions) + 1}"
        self.pending_events[decision_id] = event
        self.decisions.append(
            {
                "id": decision_id,
                "ts": now_iso(),
                "user_id": event.user_id,
                "decision": "block",
                "signals": {
                    "new_network": True,
                    "new_device": event.device_id not in self.profile(event.user_id).devices,
                    "route": event.route,
                    "route_class": route_class,
                    "route_never_used": True,
                    "path": [],
                },
                "rationale": (
                    "Red nunca vista y fue directo a una acción crítica "
                    f"({event.method} {event.route}) que este usuario nunca hace."
                ),
                "event_ids": [event.id],
                "status": "open",
                "access": {
                    "ts": event.ts,
                    "ip": event.ip,
                    "device_id": event.device_id,
                    "session_id": event.session_id,
                    "method": event.method,
                    "route": event.route,
                },
            }
        )

    def activity(self, limit: int) -> list[dict]:
        """Newest-first accesses with their verdict and HTTP status (contract 5).

        An access belongs to a block decision when, from the access that
        triggered it (inclusive), it is in the blocked session (any status: the
        request in flight before the middleware's next poll was served with 200)
        or comes from the blocked IP, even in another session. This fake engine
        always blocks the IP, and the session when the trigger has one. Marking
        the decision normal turns all of them back to `normal` (verdict_of).
        """
        index = {e.id: i for i, e in enumerate(self.log)}
        # (log index of the trigger, session, ip, decision), oldest first
        blocks = []
        for d in self.decisions:
            access = d.get("access") or {}
            if d["decision"] == "block" and d["event_ids"][0] in index:
                blocks.append((index[d["event_ids"][0]], access.get("session_id"), access.get("ip"), d))
        items = []
        for i in range(len(self.log) - 1, max(len(self.log) - 1 - limit, -1), -1):
            e = self.log[i]
            decision = next(
                (d for at, sid, ip, d in reversed(blocks)
                 if at <= i and ((sid and e.session_id == sid) or (ip and e.ip == ip))),
                None,
            )
            items.append(
                {
                    "event_id": e.id,
                    "ts": e.ts,
                    "user_id": e.user_id,
                    "session_id": e.session_id,
                    "ip": e.ip,
                    "device_id": e.device_id,
                    "method": e.method,
                    "route": e.route,
                    "verdict": verdict_of(decision) if decision else "normal",
                    "status": e.status,
                    "decision_id": decision["id"] if decision else None,
                }
            )
        return items

    def mark_normal(self, decision_id: str) -> dict | None:
        decision = next((d for d in self.decisions if d["id"] == decision_id), None)
        if decision is None:
            return None
        if decision_id in self.normal:
            return decision
        self.normal.add(decision_id)
        decision["status"] = "marked_normal"
        event = self.pending_events[decision_id]
        self.profile(event.user_id).learn(event)
        changed = False
        if event.session_id in self.sessions:
            self.sessions.remove(event.session_id)
            changed = True
        if event.ip in self.ips:
            self.ips.remove(event.ip)
            changed = True
        if changed:
            self.version += 1
        still_blocked = any(
            d["user_id"] == event.user_id and d["id"] not in self.normal for d in self.decisions
        )
        if not still_blocked:
            self.blocked_users.discard(event.user_id)
        return decision


def _tokens_from_env() -> set[str]:
    tokens = {t.strip() for t in os.environ.get("VERIDIAN_TOKENS", "").split(",") if t.strip()}
    if os.environ.get("VERIDIAN_ALLOW_DEV_TOKEN") == "1":
        tokens.add(DEV_TOKEN)
    return tokens


def _seed_from_env() -> list[dict]:
    raw = os.environ.get("VERIDIAN_SEED", "").strip()
    if not raw:
        return []
    if raw == "demo":
        return DEMO_SEED
    return json.loads(raw)


def create_app(
    tokens: set[str] | None = None,
    seed: list[dict] | None = None,
    routes: dict[str, str] | None = None,
    learn_delay: float | None = None,
    clock=time.monotonic,
) -> FastAPI:
    """Build the fake engine. With no arguments it reads the environment:

    VERIDIAN_TOKENS         comma-separated accepted tokens (none -> all 401)
    VERIDIAN_ALLOW_DEV_TOKEN=1   additionally accept the local "dev-token"
    VERIDIAN_LEARN_DELAY    seconds before an unflagged event enters the profile (default 300)
    VERIDIAN_SEED           "demo" or a JSON list of {user_id, ips, routes, devices}
    """
    accepted = set(tokens) if tokens is not None else _tokens_from_env()
    seed_data = seed if seed is not None else _seed_from_env()
    route_map = dict(routes) if routes is not None else dict(DEFAULT_ROUTES)
    delay = learn_delay if learn_delay is not None else float(os.environ.get("VERIDIAN_LEARN_DELAY", "300"))
    states: dict[str, ClientState] = {}

    app = FastAPI(title="Veridian fake engine")

    @app.exception_handler(Unauthorized)
    async def _unauthorized(_: Request, __: Unauthorized):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    def state_for(authorization: Annotated[str | None, Header()] = None) -> ClientState:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme != "Bearer" or token not in accepted:
            raise Unauthorized()
        if token not in states:
            st = ClientState(routes=route_map, learn_delay=delay, clock=clock)
            for item in seed_data:
                st.seed(item["user_id"], item.get("ips", ()), item.get("routes", ()), item.get("devices", ()))
            states[token] = st
        return states[token]

    State = Annotated[ClientState, Depends(state_for)]

    @app.post("/v1/events", status_code=202)
    def post_events(body: EventsIn, st: State):
        accepted_count = 0
        for event in body.events:
            if event.id in st.seen_event_ids:
                continue
            st.seen_event_ids.add(event.id)
            accepted_count += 1
            st.ingest(event)
        return {"accepted": accepted_count}

    @app.get("/v1/blocklist")
    def get_blocklist(st: State, since: int | None = None):
        if since is not None and since == st.version:
            return Response(status_code=304)
        return {"version": st.version, "users": [], "sessions": list(st.sessions), "ips": list(st.ips)}

    @app.get("/v1/decisions")
    def get_decisions(st: State, limit: Annotated[int, Query(ge=1, le=500)] = 50):
        return {"decisions": list(reversed(st.decisions))[:limit]}

    @app.get("/v1/activity")
    def get_activity(st: State, limit: Annotated[int, Query(ge=1, le=200)] = 50):
        return {"activity": st.activity(limit)}

    @app.post("/v1/decisions/{decision_id}/normal")
    def post_normal(decision_id: str, st: State):
        decision = st.mark_normal(decision_id)
        if decision is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return decision

    @app.get("/v1/users/{user_id}/profile")
    def get_profile(user_id: str, st: State):
        st.apply_pending()
        p = st.profile(user_id)
        hours = [min(p.hours), max(p.hours)] if p.hours else []
        return {
            "user_id": user_id,
            "history_days": 14,
            "networks": sorted(p.networks),
            "devices": sorted(p.devices),
            "hours": hours,
            "routes": sorted(p.route_keys),
            "state": "blocked" if user_id in st.blocked_users else "normal",
        }

    return app


app = create_app()
