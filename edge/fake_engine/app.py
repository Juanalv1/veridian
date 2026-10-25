"""In-memory stand-in for the Veridian engine.

Honors the contracts in docs/REPARTO.md (including 8, routes, and 9, the
synchronous check of critical routes) and mirrors Dev 1's first
milestone rule: a `critical` route the user never used, hit from a /24 never
seen for that user, produces a `block` decision and blocks its session and IP.
No AI, no persistence.
"""

import hashlib
import hmac
import ipaddress
import itertools
import json
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StringConstraints

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


class Forbidden(Exception):
    pass


class ProjectRequired(Exception):
    pass


class NotFound(Exception):
    pass


class Conflict(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


# Roles of docs/DISENO-PRODUCTO.md, weakest first. They live at the organization level only.
ROLE_RANK = {"viewer": 0, "admin": 1, "owner": 2}
Role = Literal["viewer", "admin", "owner"]


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


class CheckIn(BaseModel):
    event: EventIn


class LoginIn(BaseModel):
    google_sub: str = Field(min_length=1)
    email: str = Field(min_length=3)
    name: str | None = None


class MemberPatch(BaseModel):
    role: Role


class InvitationIn(BaseModel):
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Role


class ApiKeyIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    retention_days: int | None = Field(default=None, ge=1)
    enforcement: Literal["observe", "enforce"] | None = None
    ai_enabled: bool | None = None


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


def parse_iso(value: str) -> datetime:
    """ISO 8601 UTC ("...Z" or with an offset); a naive value is taken as UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso_of(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    ip_blocked: set[str] = field(default_factory=set)  # decision ids whose block lists the IP too
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
            # The IP is blocked only because the network is new (always, for this rule).
            self._block(event, route_class, block_ip=new_network)
        else:
            self._learn_later(event)

    def check(self, event: EventIn) -> dict:
        """Contract 9: ingest like POST /v1/events (first write per id wins), then answer
        `block` when this event started a block or its session or IP is on the blocklist."""
        before = len(self.decisions)
        if event.id not in self.seen_event_ids:
            self.seen_event_ids.add(event.id)
            self.ingest(event)
        created = self.decisions[before:]
        decision = created[-1] if created else self._active_block_for(event)
        return {"verdict": "block" if decision else "allow", "decision_id": decision["id"] if decision else None}

    def _active_block_for(self, event: EventIn) -> dict | None:
        for d in reversed(self.open_blocks()):
            access = d.get("access") or {}
            same_session = event.session_id and event.session_id == access.get("session_id")
            same_ip = d["id"] in self.ip_blocked and event.ip == access.get("ip")
            if same_session or same_ip:
                return d
        return None

    def _block(self, event: EventIn, route_class: str, block_ip: bool = True) -> None:
        """Block the session; the IP too only when `block_ip` (new network). A block from a
        known network is session-only: the IP is the owner's."""
        if event.session_id and event.session_id not in self.sessions:
            self.sessions.append(event.session_id)
        if block_ip and event.ip not in self.ips:
            self.ips.append(event.ip)
        self.version += 1
        self.blocked_users.add(event.user_id)
        decision_id = f"d_{len(self.decisions) + 1}"
        if block_ip:
            self.ip_blocked.add(decision_id)
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
        or comes from the blocked IP (only when the block lists it), even in another
        session. Marking
        the decision normal turns all of them back to `normal` (verdict_of).
        """
        index = {e.id: i for i, e in enumerate(self.log)}
        # (log index of the trigger, session, ip, decision), oldest first
        blocks = []
        for d in self.decisions:
            access = d.get("access") or {}
            if d["decision"] == "block" and d["event_ids"][0] in index:
                ip = access.get("ip") if d["id"] in self.ip_blocked else None
                blocks.append((index[d["event_ids"][0]], access.get("session_id"), ip, d))
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

    def open_blocks(self, user_id: str | None = None) -> list[dict]:
        return [d for d in self.decisions
                if d["decision"] == "block" and d["status"] == "open" and (user_id is None or d["user_id"] == user_id)]

    def summary(self, since: datetime, until: datetime) -> dict:
        """Contract 6. `reviewed_by_ai` pretends to be the engine's judge: one model call per
        session that got a decision in the range (the real engine judges once per session)."""
        events = [e for e in self.log if since <= parse_iso(e.ts) < until]
        decisions = [d for d in self.decisions if since <= parse_iso(d["ts"]) < until]
        counts = {"allow": 0, "alert": 0, "step_up": 0, "block": 0}
        judged = set()
        for d in decisions:
            counts[d["decision"]] = counts.get(d["decision"], 0) + 1
            event = self.pending_events.get(d["id"])
            judged.add((d["user_id"], event.session_id if event and event.session_id else d["id"]))
        hours: dict[datetime, dict] = {}
        for e in events:
            h = parse_iso(e.ts).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            hours.setdefault(h, {"hour": iso_of(h), "accesses": 0, "blocks": 0})["accesses"] += 1
        for d in decisions:
            if d["decision"] != "block":
                continue
            h = parse_iso(d["ts"]).replace(minute=0, second=0, microsecond=0)
            if h in hours:
                hours[h]["blocks"] += 1
        return {
            "since": iso_of(since),
            "until": iso_of(until),
            "accesses": len(events),
            "users": len({e.user_id for e in events}),
            "reviewed_by_ai": len(judged),
            "decisions": counts,
            "blocks_active": {"decisions": len(self.open_blocks()), "sessions": len(self.sessions), "ips": len(self.ips)},
            "by_hour": [hours[h] for h in sorted(hours)],
        }

    def users(self) -> list[dict]:
        """Contract 7: blocked users first, then newest `last_seen` first."""
        self.apply_pending()
        ids = set(self.profiles) | {e.user_id for e in self.log} | {d["user_id"] for d in self.decisions}
        rows = []
        for uid in ids:
            p = self.profile(uid)
            seen = [e.ts for e in self.log if e.user_id == uid]
            last = max(seen, key=parse_iso) if seen else None
            opened = len(self.open_blocks(uid))
            rows.append({"user_id": uid, "state": "blocked" if uid in self.blocked_users else "normal",
                         "history_days": 14, "networks": len(p.networks), "devices": len(p.devices),
                         "last_seen": last, "open_blocks": opened})
        rows.sort(key=lambda r: (r["open_blocks"] == 0, -(parse_iso(r["last_seen"]).timestamp() if r["last_seen"] else 0), r["user_id"]))
        return rows

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
        if decision_id in self.ip_blocked and event.ip in self.ips:
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


@dataclass
class Person:
    id: str
    google_sub: str
    email: str
    name: str
    created_at: str
    last_login_at: str


@dataclass
class Project:
    """One isolated ClientState (events, decisions, blocklist). It has no members of its own: who may
    see it, and with which role, is decided by its organization."""

    id: str
    org_id: str
    name: str
    state: ClientState
    created_at: str
    retention_days: int = 90
    enforcement: str = "observe"
    ai_enabled: bool = True
    deleted_at: str | None = None

    def public(self) -> dict:
        return {"id": self.id, "org_id": self.org_id, "name": self.name, "retention_days": self.retention_days,
                "enforcement": self.enforcement, "ai_enabled": self.ai_enabled, "created_at": self.created_at}


@dataclass
class ApiKey:
    """An SDK key of one project. Only the SHA-256 of the token is kept (design, gap 1); `prefix` lets a
    person tell keys apart. The 256-bit secret is why an unsalted fast hash is enough."""

    id: str
    project_id: str
    name: str
    prefix: str
    key_hash: str
    created_by: str
    created_at: str
    last_used_at: str | None = None
    revoked_at: str | None = None
    revoked_by: str | None = None


KEY_PREFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
DECOY_HASH = hashlib.sha256(b"veridian-decoy").hexdigest()  # compared when the key is unknown: same work as a hit


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Organization:
    """Groups projects and holds the only member list: one role per person, valid for every project."""

    id: str
    name: str
    created_at: str
    members: dict[str, str] = field(default_factory=dict)  # person_id -> role
    joined: dict[str, str] = field(default_factory=dict)  # person_id -> when they became a member
    invitations: list[dict] = field(default_factory=list)  # see `invite`
    audit: list[dict] = field(default_factory=list)  # append-only; nothing edits or deletes it
    deleted_at: str | None = None  # logical delete: the org is gone for every endpoint

    def has_owner(self) -> bool:
        return "owner" in self.members.values()

    def owners(self) -> int:
        return sum(1 for r in self.members.values() if r == "owner")

    def add_member(self, person_id: str, role: str, at: str) -> None:
        self.members[person_id] = role
        self.joined.setdefault(person_id, at)

    def invite(self, email: str, role: Role, expires_at: str, invited_by: str | None = None) -> dict:
        inv = {"id": "inv_" + secrets.token_hex(6), "email": email.strip().lower(), "role": role,
               "invited_by": invited_by, "created_at": now_iso(), "expires_at": expires_at,
               "accepted_at": None, "revoked_at": None}
        self.invitations.append(inv)
        return inv

    def pending(self) -> list[dict]:
        return [i for i in self.invitations if i["accepted_at"] is None and i["revoked_at"] is None]


def _tokens_from_env() -> set[str]:
    tokens = {t.strip() for t in os.environ.get("VERIDIAN_TOKENS", "").split(",") if t.strip()}
    if os.environ.get("VERIDIAN_ALLOW_DEV_TOKEN") == "1":
        tokens.add(DEV_TOKEN)
    return tokens


def _emails_from_env(name: str) -> set[str]:
    return {e.strip().lower() for e in os.environ.get(name, "").split(",") if e.strip()}


def _panel_tokens_from_env() -> set[str]:
    """VERIDIAN_PANEL_TOKEN, plus VERIDIAN_PANEL_TOKENS (current and previous, to rotate without a cut)."""
    raw = [os.environ.get("VERIDIAN_PANEL_TOKEN", "")] + os.environ.get("VERIDIAN_PANEL_TOKENS", "").split(",")
    return {t.strip() for t in raw if t.strip()}


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
    panel_tokens: set[str] | None = None,
    bootstrap_owners: set[str] | None = None,
    org_creators: set[str] | None = None,
    invitation_days: int | None = None,
) -> FastAPI:
    """Build the fake engine. With no arguments it reads the environment:

    VERIDIAN_TOKENS         comma-separated SDK keys; each one is its own seeded organization with one project
    VERIDIAN_ALLOW_DEV_TOKEN=1   additionally accept the local "dev-token"
    VERIDIAN_LEARN_DELAY    seconds before an unflagged event enters the profile (default 300)
    VERIDIAN_SEED           "demo" or a JSON list of {user_id, ips, routes, devices}
    VERIDIAN_PANEL_TOKEN    service credential of the panel (VERIDIAN_PANEL_TOKENS: comma list, to rotate)
    VERIDIAN_BOOTSTRAP_OWNERS    emails that become owner of the organizations that have no owner, on login
    VERIDIAN_ORG_CREATORS   emails allowed to create organizations (empty: nobody). Provisional:
                            open decision 2 of docs/DISENO-PRODUCTO.md. Owners and admins of an
                            organization create its projects without being on this list.
    VERIDIAN_INVITATION_DAYS     days an invitation stays valid (default 14; a setting, not a measured value)

    Two callers, as in the design: `sdk` (a project's key: events, check, blocklist, routes) and
    `panel` (service credential + X-Veridian-Actor + X-Veridian-Project; the actor's role in the
    organization of that project is checked on every request).
    """
    accepted = set(tokens) if tokens is not None else _tokens_from_env()
    seed_data = seed if seed is not None else _seed_from_env()
    route_map = dict(routes) if routes is not None else dict(DEFAULT_ROUTES)
    delay = learn_delay if learn_delay is not None else float(os.environ.get("VERIDIAN_LEARN_DELAY", "300"))
    panel_accepted = set(panel_tokens) if panel_tokens is not None else _panel_tokens_from_env()
    owners = {e.lower() for e in bootstrap_owners} if bootstrap_owners is not None else _emails_from_env("VERIDIAN_BOOTSTRAP_OWNERS")
    creators = {e.lower() for e in org_creators} if org_creators is not None else _emails_from_env("VERIDIAN_ORG_CREATORS")
    invite_days = invitation_days if invitation_days is not None else int(os.environ.get("VERIDIAN_INVITATION_DAYS", "14"))

    orgs: dict[str, Organization] = {}
    projects: dict[str, Project] = {}
    by_token: dict[str, str] = {}  # seeded SDK key (VERIDIAN_TOKENS) -> project id; created keys are in `api_keys`
    people: dict[str, Person] = {}  # person id -> person
    by_sub: dict[str, str] = {}  # google_sub -> person id
    audit_seq = itertools.count(1)
    api_keys: dict[str, ApiKey] = {}  # id -> key
    key_by_hash: dict[str, ApiKey] = {}

    def new_state() -> ClientState:
        return ClientState(routes=dict(route_map), learn_delay=delay, clock=clock)

    # Each SDK key is an organization of its own with one seeded project, and no members until a
    # bootstrap owner logs in.
    ordered = sorted(accepted)
    for i, token in enumerate(ordered, start=1):
        st = new_state()
        for item in seed_data:
            st.seed(item["user_id"], item.get("ips", ()), item.get("routes", ()), item.get("devices", ()))
        digest = hashlib.sha256(token.encode()).hexdigest()[:8]
        pid, oid = "p_" + digest, "org_" + digest
        name = "Demo" if len(ordered) == 1 else f"Demo {i}"
        orgs[oid] = Organization(id=oid, name=name, created_at=now_iso())
        projects[pid] = Project(id=pid, org_id=oid, name=name, state=st, created_at=now_iso(), enforcement="enforce")
        by_token[token] = pid

    app = FastAPI(title="Veridian fake engine")
    app.state.projects = projects
    app.state.orgs = orgs
    app.state.people = people
    app.state.api_keys = api_keys
    app.state.project_id_for = by_token.__getitem__
    app.state.org_id_for = lambda token: projects[by_token[token]].org_id

    @app.exception_handler(Unauthorized)
    async def _unauthorized(_: Request, __: Unauthorized):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    @app.exception_handler(Forbidden)
    async def _forbidden(_: Request, __: Forbidden):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, __: NotFound):
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict):
        return JSONResponse({"error": exc.code}, status_code=409)

    @app.exception_handler(ProjectRequired)
    async def _project_required(_: Request, __: ProjectRequired):
        return JSONResponse({"error": "project_required"}, status_code=400)

    def bearer_of(authorization: str | None) -> str:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme != "Bearer" or not token:
            raise Unauthorized()
        return token

    def is_panel(token: str) -> bool:
        return any(hmac.compare_digest(token.encode(), p.encode()) for p in panel_accepted)

    def sdk_project(token: str, touch: bool = False) -> Project | None:
        """The project of an SDK key: a seeded token, or a created key that is not revoked and whose project
        and organization are alive. A revoked key is rejected exactly like an unknown one. `touch` records the use."""
        if token in accepted:
            return projects[by_token[token]]
        digest = hash_key(token)
        key = key_by_hash.get(digest)
        same = hmac.compare_digest(digest.encode(), (key.key_hash if key else DECOY_HASH).encode())
        if key is None or not same or key.revoked_at is not None:
            return None
        project = projects[key.project_id]
        if project.deleted_at is not None or orgs[project.org_id].deleted_at is not None:
            return None
        if touch:
            key.last_used_at = now_iso()
        return project

    def kind_of(authorization: str | None) -> tuple[str, str]:
        """("panel" | "sdk", token); anything else is 401. The panel credential is checked first."""
        token = bearer_of(authorization)
        if is_panel(token):
            return "panel", token
        if sdk_project(token) is not None:
            return "sdk", token
        raise Unauthorized()

    def actor_of(authorization: str | None, actor_id: str | None) -> Person:
        kind, _ = kind_of(authorization)
        person = people.get(actor_id or "")
        if kind != "panel" or person is None:
            raise Forbidden()
        return person

    def authorize_org(person: Person, org_id: str | None, minimum: Role) -> Organization:
        """The organization, if the person is a member with at least `minimum`. Unknown org = foreign = 403."""
        org = orgs.get(org_id or "")
        if org is not None and org.deleted_at is not None:
            org = None
        role = org.members.get(person.id) if org else None
        if org is None or role is None or ROLE_RANK[role] < ROLE_RANK[minimum]:
            raise Forbidden()
        return org

    def authorize(person: Person, project_id: str | None, minimum: Role) -> Project:
        """The project, if the person's role in its organization is at least `minimum`. There are no
        project roles. Unknown project = foreign = 403."""
        project = projects.get(project_id or "")
        if project is None:
            raise Forbidden()
        authorize_org(person, project.org_id, minimum)
        return project

    def sdk_state(authorization: Annotated[str | None, Header()] = None) -> ClientState:
        kind, token = kind_of(authorization)
        if kind != "sdk":
            raise Forbidden()
        return sdk_project(token, touch=True).state

    def panel_state(minimum: Role, allow_sdk: bool = False):
        def dep(authorization: Annotated[str | None, Header()] = None,
                x_veridian_actor: Annotated[str | None, Header()] = None,
                x_veridian_project: Annotated[str | None, Header()] = None) -> ClientState:
            kind, token = kind_of(authorization)
            if kind == "sdk" and allow_sdk:
                return sdk_project(token, touch=True).state
            person = actor_of(authorization, x_veridian_actor)
            if not x_veridian_project:
                raise ProjectRequired()
            return authorize(person, x_veridian_project, minimum).state
        return dep

    State = Annotated[ClientState, Depends(sdk_state)]  # SDK key only
    ReadState = Annotated[ClientState, Depends(panel_state("viewer"))]  # panel, any member
    SharedState = Annotated[ClientState, Depends(panel_state("viewer", allow_sdk=True))]  # SDK key or panel
    AdminState = Annotated[ClientState, Depends(panel_state("admin"))]  # panel, admin or owner

    def person_of(authorization: Annotated[str | None, Header()] = None,
                  x_veridian_actor: Annotated[str | None, Header()] = None) -> Person:
        return actor_of(authorization, x_veridian_actor)

    Actor = Annotated[Person, Depends(person_of)]

    def panel_only(authorization: Annotated[str | None, Header()] = None) -> None:
        if kind_of(authorization)[0] != "panel":
            raise Forbidden()

    def live_projects(org: Organization) -> list[Project]:
        return [p for p in projects.values() if p.org_id == org.id and p.deleted_at is None]

    def project_refs(org: Organization) -> list[dict]:
        return [{"id": p.id, "name": p.name} for p in live_projects(org)]

    def org_view(org: Organization, person: Person) -> dict:
        return {"id": org.id, "name": org.name, "role": org.members[person.id], "projects": project_refs(org)}

    def listing(person: Person) -> list[dict]:
        return [org_view(o, person) for o in orgs.values() if person.id in o.members and o.deleted_at is None]

    def project_view(project: Project, person: Person) -> dict:
        return {**project.public(), "role": orgs[project.org_id].members[person.id]}

    def person_public(person: Person) -> dict:
        return {"id": person.id, "email": person.email, "name": person.name}

    def record(org: Organization, actor: Person, action: str, target_type: str, target_id: str,
               project_id: str | None = None, meta: dict | None = None) -> None:
        """Append to the org's audit log. Never put a secret or a customer's user id in `meta`."""
        org.audit.append({"seq": next(audit_seq), "ts": now_iso(), "org_id": org.id, "project_id": project_id,
                          "actor_person_id": actor.id, "actor_kind": "person", "action": action,
                          "target_type": target_type, "target_id": target_id, "meta": meta or {}})

    def audit_view(entry: dict) -> dict:
        actor, project = people.get(entry["actor_person_id"]), projects.get(entry["project_id"] or "")
        return {**entry, "actor_name": actor.name if actor else None, "project_name": project.name if project else None}

    def member_view(org: Organization, person_id: str) -> dict:
        person = people[person_id]
        return {"person_id": person.id, "email": person.email, "name": person.name, "role": org.members[person_id],
                "joined_at": org.joined.get(person_id)}

    def invitation_view(inv: dict) -> dict:
        return {"id": inv["id"], "email": inv["email"], "role": inv["role"], "invited_by": inv["invited_by"],
                "created_at": inv["created_at"], "expires_at": inv["expires_at"],
                "expired": parse_iso(inv["expires_at"]) <= datetime.now(timezone.utc)}

    def may_touch(actor_role: str, *roles: str) -> None:
        """Rule 3: an admin does not manage owners (invite, promote, demote, remove); only an owner does."""
        if actor_role != "owner" and "owner" in roles:
            raise Forbidden()

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

    @app.post("/v1/check")
    def post_check(body: CheckIn, st: State):
        return st.check(body.event)

    @app.get("/v1/blocklist")
    def get_blocklist(st: SharedState, since: int | None = None):
        if since is not None and since == st.version:
            return Response(status_code=304)
        return {"version": st.version, "users": [], "sessions": list(st.sessions), "ips": list(st.ips)}

    @app.get("/v1/decisions")
    def get_decisions(st: ReadState, limit: Annotated[int, Query(ge=1, le=500)] = 50):
        return {"decisions": list(reversed(st.decisions))[:limit]}

    @app.get("/v1/activity")
    def get_activity(st: ReadState, limit: Annotated[int, Query(ge=1, le=200)] = 50):
        return {"activity": st.activity(limit)}

    @app.get("/v1/summary")
    def get_summary(st: ReadState, since: str | None = None, until: str | None = None):
        try:
            end = parse_iso(until) if until else datetime.now(timezone.utc)
            start = parse_iso(since) if since else end - timedelta(hours=24)
        except ValueError:
            return JSONResponse({"error": "invalid_range"}, status_code=422)
        if start >= end:
            return JSONResponse({"error": "invalid_range"}, status_code=422)
        return st.summary(start, end)

    @app.get("/v1/users")
    def get_users(st: ReadState):
        return {"users": st.users()}

    @app.get("/v1/routes")
    def get_routes(st: SharedState):
        """Contract 8: classified routes only, critical first, then by route and method."""
        rank = {"critical": 0, "sensitive": 1}
        rows = []
        for key, route_class in st.routes.items():
            if route_class in rank:
                method, route = key.split(" ", 1)
                rows.append({"method": method, "route": route, "class": route_class})
        rows.sort(key=lambda r: (rank[r["class"]], r["route"], r["method"]))
        return {"routes": rows, "default": "normal"}

    @app.post("/v1/decisions/{decision_id}/normal")
    def post_normal(decision_id: str, st: AdminState, person: Actor,
                    x_veridian_project: Annotated[str | None, Header()] = None):
        first_time = decision_id not in st.normal
        decision = st.mark_normal(decision_id)
        if decision is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if first_time:
            project = projects[x_veridian_project]  # AdminState already authorized this project
            record(orgs[project.org_id], person, "decision.marked_normal", "decision", decision_id, project.id)
        return decision

    @app.get("/v1/users/{user_id}/profile")
    def get_profile(user_id: str, st: ReadState):
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

    # --- contract 10: people, organizations and projects (panel only) ------------------------

    @app.post("/v1/people/login", dependencies=[Depends(panel_only)])
    def post_login(body: LoginIn):
        """Upsert by google_sub, take the bootstrap and the pending invitations, answer with the orgs."""
        email = body.email.strip().lower()
        stamp = now_iso()
        pid = by_sub.get(body.google_sub)
        if pid is None:
            pid = "per_" + secrets.token_hex(8)
            by_sub[body.google_sub] = pid
            people[pid] = Person(pid, body.google_sub, email, body.name or email, stamp, stamp)
        person = people[pid]
        person.email, person.last_login_at = email, stamp
        if body.name:
            person.name = body.name
        for org in orgs.values():
            if org.deleted_at is not None:
                continue
            if email in owners and not org.has_owner():
                org.add_member(person.id, "owner", stamp)
            for inv in org.pending():
                if inv["email"] == email and parse_iso(inv["expires_at"]) > datetime.now(timezone.utc):
                    inv["accepted_at"] = stamp
                    current = org.members.get(person.id)
                    if current is None or ROLE_RANK[inv["role"]] > ROLE_RANK[current]:
                        org.add_member(person.id, inv["role"], stamp)
        return {"person": person_public(person), "orgs": listing(person)}

    @app.get("/v1/me/orgs")
    def get_my_orgs(person: Actor):
        return {"orgs": listing(person)}

    @app.post("/v1/orgs", status_code=201)
    def post_org(body: OrgIn, person: Actor):
        if person.email not in creators:
            raise Forbidden()
        oid = "org_" + secrets.token_hex(4)
        org = Organization(id=oid, name=body.name.strip(), created_at=now_iso())
        org.add_member(person.id, "owner", org.created_at)
        orgs[oid] = org
        return org_view(org, person)

    @app.get("/v1/orgs/{org_id}")
    def get_org(org_id: Annotated[str, Path()], person: Actor):
        org = authorize_org(person, org_id, "viewer")
        return {**org_view(org, person), "created_at": org.created_at}

    @app.patch("/v1/orgs/{org_id}")
    def patch_org(org_id: Annotated[str, Path()], body: OrgPatch, person: Actor):
        org = authorize_org(person, org_id, "admin")
        if body.name is not None:
            org.name = body.name.strip()
        return {**org_view(org, person), "created_at": org.created_at}

    @app.delete("/v1/orgs/{org_id}", status_code=204)
    def delete_org(org_id: Annotated[str, Path()], person: Actor):
        org = authorize_org(person, org_id, "owner")
        if live_projects(org):
            return JSONResponse({"error": "org_not_empty"}, status_code=409)
        org.deleted_at = now_iso()
        return Response(status_code=204)

    # --- contract 11: members and invitations of an organization -------------------------------------

    def target_member(org: Organization, actor: Person, member_id: str) -> str:
        """The target's current role, after rule 2 (nobody acts on themselves) and 404 for a stranger."""
        if member_id not in org.members:
            raise NotFound()
        if member_id == actor.id:
            raise Conflict("cannot_change_self")
        return org.members[member_id]

    @app.get("/v1/orgs/{org_id}/members")
    def get_members(org_id: Annotated[str, Path()], person: Actor):
        org = authorize_org(person, org_id, "viewer")
        rows = sorted(org.members, key=lambda m: (-ROLE_RANK[org.members[m]], people[m].name.lower()))
        return {"members": [member_view(org, m) for m in rows]}

    @app.patch("/v1/orgs/{org_id}/members/{member_id}")
    def patch_member(org_id: Annotated[str, Path()], member_id: Annotated[str, Path()], body: MemberPatch, person: Actor):
        org = authorize_org(person, org_id, "admin")
        current = target_member(org, person, member_id)
        may_touch(org.members[person.id], current, body.role)
        if current == "owner" and body.role != "owner" and org.owners() == 1:
            raise Conflict("last_owner")  # rule 1; unreachable through the API, kept as the invariant
        if current != body.role:
            org.members[member_id] = body.role
            target = people[member_id]
            record(org, person, "member.role_changed", "person", member_id,
                   meta={"email": target.email, "name": target.name, "from": current, "to": body.role})
        return member_view(org, member_id)

    @app.delete("/v1/orgs/{org_id}/members/{member_id}", status_code=204)
    def delete_member(org_id: Annotated[str, Path()], member_id: Annotated[str, Path()], person: Actor):
        org = authorize_org(person, org_id, "admin")
        current = target_member(org, person, member_id)
        may_touch(org.members[person.id], current)
        if current == "owner" and org.owners() == 1:
            raise Conflict("last_owner")
        target = people[member_id]
        del org.members[member_id]
        record(org, person, "member.removed", "person", member_id,
               meta={"email": target.email, "name": target.name, "role": current})
        return Response(status_code=204)

    @app.get("/v1/orgs/{org_id}/invitations")
    def get_invitations(org_id: Annotated[str, Path()], person: Actor):
        org = authorize_org(person, org_id, "viewer")
        return {"invitations": [invitation_view(i) for i in org.pending()]}

    @app.post("/v1/orgs/{org_id}/invitations", status_code=201)
    def post_invitation(org_id: Annotated[str, Path()], body: InvitationIn, person: Actor):
        org = authorize_org(person, org_id, "admin")
        actor_role = org.members[person.id]
        email = body.email.strip().lower()
        replaced = next((i for i in org.pending() if i["email"] == email), None)
        may_touch(actor_role, body.role, *([replaced["role"]] if replaced else []))
        if any(people[m].email == email for m in org.members):
            raise Conflict("already_member")
        stamp = now_iso()
        if replaced:
            replaced["revoked_at"] = stamp  # inviting again renews: one pending invitation per email
        expires = iso_of(datetime.now(timezone.utc) + timedelta(days=invite_days))
        inv = org.invite(email, body.role, expires, invited_by=person.id)
        record(org, person, "invitation.resent" if replaced else "invitation.created", "invitation", inv["id"],
               meta={"email": email, "role": body.role})
        return invitation_view(inv)

    @app.delete("/v1/orgs/{org_id}/invitations/{inv_id}", status_code=204)
    def delete_invitation(org_id: Annotated[str, Path()], inv_id: Annotated[str, Path()], person: Actor):
        org = authorize_org(person, org_id, "admin")
        inv = next((i for i in org.pending() if i["id"] == inv_id), None)
        if inv is None:
            raise NotFound()
        may_touch(org.members[person.id], inv["role"])
        inv["revoked_at"] = now_iso()
        record(org, person, "invitation.revoked", "invitation", inv["id"], meta={"email": inv["email"], "role": inv["role"]})
        return Response(status_code=204)

    # --- contract 12: API keys of a project --------------------------------------------------------------

    def key_view(key: ApiKey) -> dict:
        creator, revoker = people.get(key.created_by), people.get(key.revoked_by or "")
        return {"id": key.id, "name": key.name, "prefix": key.prefix,
                "status": "revoked" if key.revoked_at else "active", "created_by": key.created_by,
                "created_by_name": creator.name if creator else None, "created_at": key.created_at,
                "last_used_at": key.last_used_at, "revoked_at": key.revoked_at,
                "revoked_by_name": revoker.name if revoker else None}

    def project_key(project: Project, key_id: str) -> ApiKey:
        key = api_keys.get(key_id)
        if key is None or key.project_id != project.id:
            raise NotFound()
        return key

    @app.get("/v1/projects/{project_id}/api-keys")
    def get_api_keys(project_id: Annotated[str, Path()], person: Actor):
        project = authorize(person, project_id, "admin")
        rows = [k for k in api_keys.values() if k.project_id == project.id]
        return {"api_keys": [key_view(k) for k in reversed(rows)]}

    @app.post("/v1/projects/{project_id}/api-keys", status_code=201)
    def post_api_key(project_id: Annotated[str, Path()], body: ApiKeyIn, person: Actor):
        project = authorize(person, project_id, "admin")
        prefix = "vrd_" + "".join(secrets.choice(KEY_PREFIX_ALPHABET) for _ in range(8))
        token = f"{prefix}_{secrets.token_urlsafe(32)}"  # 32 random bytes, base64url
        key = ApiKey(id="key_" + secrets.token_hex(6), project_id=project.id, name=body.name, prefix=prefix,
                     key_hash=hash_key(token), created_by=person.id, created_at=now_iso())
        api_keys[key.id] = key
        key_by_hash[key.key_hash] = key
        record(orgs[project.org_id], person, "api_key.created", "api_key", key.id, project.id,
               {"name": key.name, "prefix": key.prefix})
        # The plaintext leaves the engine here and never again: not in the list, not in the audit.
        return JSONResponse({"id": key.id, "name": key.name, "prefix": key.prefix, "key": token}, status_code=201,
                            headers={"Cache-Control": "no-store"})

    @app.post("/v1/projects/{project_id}/api-keys/{key_id}/revoke")
    def revoke_api_key(project_id: Annotated[str, Path()], key_id: Annotated[str, Path()], person: Actor):
        project = authorize(person, project_id, "admin")
        key = project_key(project, key_id)
        if key.revoked_at is None:
            key.revoked_at, key.revoked_by = now_iso(), person.id  # immediate: nothing caches the lookup
            record(orgs[project.org_id], person, "api_key.revoked", "api_key", key.id, project.id,
                   {"name": key.name, "prefix": key.prefix})
        return key_view(key)

    # --- contract 15: audit log (read only) ------------------------------------------------------------

    @app.get("/v1/orgs/{org_id}/audit")
    def get_audit(org_id: Annotated[str, Path()], person: Actor, project: str | None = None,
                  limit: Annotated[int, Query(ge=1, le=200)] = 50, before: Annotated[int | None, Query(ge=1)] = None):
        org = authorize_org(person, org_id, "admin")
        rows = [e for e in reversed(org.audit)
                if (project is None or e["project_id"] == project) and (before is None or e["seq"] < before)]
        page = rows[:limit]
        return {"entries": [audit_view(e) for e in page], "next_before": page[-1]["seq"] if len(rows) > limit else None}

    @app.get("/v1/orgs/{org_id}/projects")
    def get_org_projects(org_id: Annotated[str, Path()], person: Actor):
        org = authorize_org(person, org_id, "viewer")
        return {"projects": [project_view(p, person) for p in live_projects(org)]}

    @app.post("/v1/orgs/{org_id}/projects", status_code=201)
    def post_org_project(org_id: Annotated[str, Path()], body: ProjectIn, person: Actor):
        org = authorize_org(person, org_id, "admin")
        pid = "p_" + secrets.token_hex(4)
        project = Project(id=pid, org_id=org.id, name=body.name.strip(), state=new_state(), created_at=now_iso())
        projects[pid] = project
        return project_view(project, person)

    @app.get("/v1/projects/{project_id}")
    def get_project(project_id: Annotated[str, Path()], person: Actor):
        return project_view(authorize(person, project_id, "viewer"), person)

    @app.patch("/v1/projects/{project_id}")
    def patch_project(project_id: Annotated[str, Path()], body: ProjectPatch, person: Actor):
        project = authorize(person, project_id, "admin")
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(project, key, value.strip() if key == "name" else value)
        return project_view(project, person)

    return app


app = create_app()
