"""Veridian ASGI middleware.

Reports one event per request to the engine (in the background, batched) and
refuses requests whose session, IP or user is on the engine's blocklist.
It is fail-open: the engine being slow or down never delays or breaks the app.
Request and response bodies are never read into events.
"""

import asyncio
import logging
import uuid
from collections import deque
from datetime import datetime, timezone
from http.cookies import SimpleCookie

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("veridian")

DEVICE_COOKIE = "vrd_did"
DEVICE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
MAX_BATCH = 200


def resolve_client_ip(scope: dict, trusted_proxies: int = 0) -> str:
    """Client IP that the client cannot choose.

    With 0 trusted proxies the socket peer is used and X-Forwarded-For is
    ignored (anyone can write it). With N, the Nth value from the right is
    used: the one appended by our outermost proxy. Fewer values than N falls
    back to the first value.
    """
    client = scope.get("client")
    peer = client[0] if client else ""
    if trusted_proxies <= 0:
        return peer
    raw = [v for k, v in scope.get("headers", []) if k == b"x-forwarded-for"]
    values = [p.strip() for h in raw for p in h.decode("latin-1").split(",") if p.strip()]
    if not values:
        return peer
    if len(values) < trusted_proxies:
        return values[0]
    return values[-trusted_proxies]


def _cookie(scope: dict, name: str) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            jar = SimpleCookie()
            try:
                jar.load(v.decode("latin-1"))
            except Exception:
                return None
            if name in jar:
                return jar[name].value
    return None


def _header(scope: dict, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return None


def _route_template(scope: dict) -> str:
    route = scope.get("route")
    if route is not None:
        template = getattr(route, "path_format", None) or getattr(route, "path", None)
        if template:
            return template
    return scope.get("path", "")


class VeridianMiddleware:
    def __init__(
        self,
        app,
        token: str,
        base_url: str,
        user_id=lambda request: None,
        session_id=lambda request: None,
        trusted_proxies: int = 0,
        http_client: httpx.AsyncClient | None = None,
        flush_interval: float = 2.0,
        poll_interval: float = 2.0,
        max_queue: int = 1000,
        background: bool = True,
    ):
        """`user_id` and `session_id` are callables taking a Starlette Request
        and returning a string or None. They run before the app (to enforce the
        blocklist) and again after it (the app may have just logged the user in,
        e.g. by setting request.state). `background=False` disables the
        flush/poll tasks so tests can drive flush() and refresh_blocklist()."""
        self.app = app
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.session_id = session_id
        self.trusted_proxies = trusted_proxies
        self.client = http_client or httpx.AsyncClient(timeout=2.0)
        self.flush_interval = flush_interval
        self.poll_interval = poll_interval
        self.background = background
        self.queue: deque[dict] = deque(maxlen=max_queue)  # drops oldest when full
        self.blocklist_version: int | None = None
        self.blocked = {"users": set(), "sessions": set(), "ips": set()}
        self._tasks: list[asyncio.Task] = []

    # --- ASGI --------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, self._watch_shutdown(receive), send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        self._ensure_background()
        request = Request(scope)
        ip = resolve_client_ip(scope, self.trusted_proxies)

        if self._is_blocked(request, ip):
            await JSONResponse({"error": "blocked"}, status_code=403)(scope, receive, send)
            return

        device_id = _cookie(scope, DEVICE_COOKIE)
        new_device = device_id is None
        if new_device:
            device_id = "d_" + uuid.uuid4().hex[:12]
        status = 0
        size = 0

        async def send_wrapper(message):
            nonlocal status, size
            if message["type"] == "http.response.start":
                status = message["status"]
                if new_device:
                    cookie = (
                        f"{DEVICE_COOKIE}={device_id}; Path=/; HttpOnly; SameSite=Lax; "
                        f"Max-Age={DEVICE_COOKIE_MAX_AGE}"
                    )
                    message = {
                        **message,
                        "headers": [*message.get("headers", []), (b"set-cookie", cookie.encode())],
                    }
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            self._record(scope, request, ip, device_id, status, size)

    def _watch_shutdown(self, receive):
        async def wrapped():
            message = await receive()
            if message["type"] == "lifespan.shutdown":
                await self.aclose()
            return message

        return wrapped

    # --- blocklist ---------------------------------------------------------

    def _safe(self, fn, request):
        try:
            value = fn(request)
            return value if value else None
        except Exception:
            log.exception("veridian: identity callable failed")
            return None

    def _is_blocked(self, request: Request, ip: str) -> bool:
        b = self.blocked
        if ip and ip in b["ips"]:
            return True
        session = self._safe(self.session_id, request)
        if session and session in b["sessions"]:
            return True
        user = self._safe(self.user_id, request)
        return bool(user and user in b["users"])

    async def refresh_blocklist(self) -> None:
        """Poll the engine once. Any failure keeps the last known list."""
        try:
            params = {"since": self.blocklist_version} if self.blocklist_version is not None else None
            r = await self.client.get(
                f"{self.base_url}/v1/blocklist", params=params, headers=self._auth()
            )
            if r.status_code == 304:
                return
            if r.status_code != 200:
                return
            data = r.json()
            self.blocked = {
                "users": set(map(str, data.get("users", []))),
                "sessions": set(map(str, data.get("sessions", []))),
                "ips": set(map(str, data.get("ips", []))),
            }
            self.blocklist_version = int(data["version"])
        except Exception as exc:
            log.warning("veridian: blocklist refresh failed: %s", exc)

    # --- events ------------------------------------------------------------

    def _record(self, scope, request, ip, device_id, status, size) -> None:
        try:
            user = self._safe(self.user_id, request)
            if not user:
                return
            self.queue.append(
                {
                    "id": "e_" + uuid.uuid4().hex,
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "user_id": user,
                    "session_id": self._safe(self.session_id, request),
                    "ip": ip,
                    "user_agent": (_header(scope, b"user-agent") or "")[:200],
                    "device_id": device_id,
                    "method": scope["method"],
                    "route": _route_template(scope),
                    "status": status,
                    "bytes": size,
                }
            )
        except Exception:
            log.exception("veridian: could not record event")

    async def flush(self) -> None:
        """Send queued events once. On failure they stay queued (ids make the
        retry idempotent)."""
        batch = list(self.queue)[:MAX_BATCH]
        if not batch:
            return
        try:
            r = await self.client.post(
                f"{self.base_url}/v1/events", json={"events": batch}, headers=self._auth()
            )
            if r.status_code != 202:
                return
        except Exception as exc:
            log.warning("veridian: event flush failed: %s", exc)
            return
        sent = {e["id"] for e in batch}
        remaining = [e for e in self.queue if e["id"] not in sent]
        self.queue.clear()
        self.queue.extend(remaining)

    # --- background --------------------------------------------------------

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _ensure_background(self) -> None:
        if not self.background or self._tasks:
            return
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._loop(self.flush, self.flush_interval)),
            loop.create_task(self._loop(self.refresh_blocklist, self.poll_interval)),
        ]

    @staticmethod
    async def _loop(fn, interval: float) -> None:
        while True:
            try:
                await fn()
            except Exception:
                log.exception("veridian: background task error")
            await asyncio.sleep(interval)

    async def aclose(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []
