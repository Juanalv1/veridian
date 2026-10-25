"""Tests for VeridianMiddleware. No real network and no sleeps: background
tasks are disabled and flush()/refresh_blocklist() are driven by hand."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI, Request

from fake_engine import create_app as create_engine
from veridian_sdk import VeridianMiddleware

TOKEN = "tok-test"
ENGINE_URL = "http://engine"


def build_app(calls=None):
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def item(item_id: str, request: Request):
        if calls is not None:
            calls.append(item_id)
        return {"id": item_id}

    @app.post("/payouts/bank-account")
    async def bank(request: Request):
        body = await request.body()
        if calls is not None:
            calls.append("bank")
        return {"len": len(body)}

    @app.post("/login")
    async def login(request: Request):
        request.state.user_id = "u_1"
        request.state.session_id = "s_new"
        return {"ok": True}

    return app


def cookie_user(request: Request):
    return request.cookies.get("uid") or getattr(request.state, "user_id", None)


def cookie_session(request: Request):
    return request.cookies.get("sid") or getattr(request.state, "session_id", None)


def make_mw(app, engine_client, **kw):
    kw.setdefault("background", False)
    return VeridianMiddleware(
        app,
        token=TOKEN,
        base_url=ENGINE_URL,
        user_id=cookie_user,
        session_id=cookie_session,
        http_client=engine_client,
        **kw,
    )


def client_for(asgi_app, peer="198.51.100.1"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi_app, client=(peer, 5555)),
        base_url="http://app",
    )


class Recorder:
    """Engine stand-in that records events and serves a configurable blocklist."""

    def __init__(self):
        self.batches = []
        self.version = 0
        self.blocklist = {"users": [], "sessions": [], "ips": []}
        self.fail = False
        self.routes = [
            {"method": "POST", "route": "/payouts/bank-account", "class": "critical"},
            {"method": "GET", "route": "/invoices/export", "class": "sensitive"},
        ]
        self.routes_status = 200
        self.checks = []  # bodies received on POST /v1/check
        self.check_status = 200
        self.check_body = {"verdict": "allow", "decision_id": None}
        self.check_hang = None  # an asyncio.Event that is never set makes /v1/check hang
        self.catalogs = []  # bodies received on PUT /v1/routes/catalog
        self.catalog_status = 200

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("engine down")
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.url.path == "/v1/routes/catalog":
            assert request.method == "PUT"
            self.catalogs.append(json.loads(request.content))
            return httpx.Response(self.catalog_status, json={"received": 0, "to_classify": 0})
        if request.url.path == "/v1/routes":
            return httpx.Response(self.routes_status, json={"routes": self.routes, "default": "normal"})
        if request.url.path == "/v1/check":
            self.checks.append(json.loads(request.content))
            if self.check_hang is not None:
                await self.check_hang.wait()
            if isinstance(self.check_body, bytes):
                return httpx.Response(self.check_status, content=self.check_body)
            return httpx.Response(self.check_status, json=self.check_body)
        if request.url.path == "/v1/events":
            self.batches.append(json.loads(request.content)["events"])
            return httpx.Response(202, json={"accepted": len(self.batches[-1])})
        since = request.url.params.get("since")
        if since is not None and int(since) == self.version:
            return httpx.Response(304)
        return httpx.Response(200, json={"version": self.version, **self.blocklist})

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    @property
    def events(self):
        return [e for b in self.batches for e in b]


@pytest.fixture
def rec():
    return Recorder()


# --- event capture ---------------------------------------------------------


async def test_event_uses_route_template_not_raw_url(rec):
    mw = make_mw(build_app(), rec.client())
    async with client_for(mw) as c:
        r = await c.get("/items/12345", cookies={"uid": "u_42", "sid": "s_1"})
    assert r.status_code == 200
    await mw.flush()
    (e,) = rec.events
    assert e["route"] == "/items/{item_id}"
    assert e["method"] == "GET" and e["status"] == 200
    assert e["user_id"] == "u_42" and e["session_id"] == "s_1"
    assert e["ts"].endswith("Z") and e["id"]
    assert e["bytes"] == len(r.content)


async def test_unmatched_route_falls_back_to_raw_path(rec):
    mw = make_mw(build_app(), rec.client())
    async with client_for(mw) as c:
        r = await c.get("/nope", cookies={"uid": "u_42"})
    assert r.status_code == 404
    await mw.flush()
    assert rec.events[0]["route"] == "/nope"


async def test_events_never_contain_bodies_or_secrets(rec):
    mw = make_mw(build_app(), rec.client())
    async with client_for(mw) as c:
        await c.post(
            "/payouts/bank-account",
            content=b"iban=VE123456&password=hunter2",
            cookies={"uid": "u_42"},
        )
    await mw.flush()
    (e,) = rec.events
    assert set(e) == {
        "id", "ts", "user_id", "session_id", "ip", "user_agent",
        "device_id", "method", "route", "status", "bytes",
    }
    dumped = json.dumps(rec.batches)
    assert "hunter2" not in dumped and "VE123456" not in dumped


async def test_events_without_user_are_skipped(rec):
    mw = make_mw(build_app(), rec.client())
    async with client_for(mw) as c:
        await c.get("/items/1")
    await mw.flush()
    assert rec.events == []


async def test_user_set_by_app_during_request_is_used(rec):
    mw = make_mw(build_app(), rec.client())
    async with client_for(mw) as c:
        await c.post("/login")
    await mw.flush()
    (e,) = rec.events
    assert e["user_id"] == "u_1" and e["session_id"] == "s_new"


async def test_device_cookie_is_set_once_and_reused(rec):
    mw = make_mw(build_app(), rec.client())
    async with client_for(mw) as c:
        r1 = await c.get("/items/1", cookies={"uid": "u_42"})
        did = r1.cookies.get("vrd_did")
        assert did
        r2 = await c.get("/items/2", cookies={"uid": "u_42", "vrd_did": did})
        assert "vrd_did" not in r2.headers.get("set-cookie", "")
    await mw.flush()
    assert [e["device_id"] for e in rec.events] == [did, did]


async def test_event_ids_are_unique(rec):
    mw = make_mw(build_app(), rec.client())
    async with client_for(mw) as c:
        for _ in range(3):
            await c.get("/items/1", cookies={"uid": "u_42"})
    await mw.flush()
    assert len({e["id"] for e in rec.events}) == 3


# --- client IP rule --------------------------------------------------------


async def ip_seen(rec, trusted_proxies, xff, peer="198.51.100.1"):
    mw = make_mw(build_app(), rec.client(), trusted_proxies=trusted_proxies)
    headers = {"X-Forwarded-For": xff} if xff else {}
    async with client_for(mw, peer) as c:
        await c.get("/items/1", cookies={"uid": "u_42"}, headers=headers)
    await mw.flush()
    return rec.events[-1]["ip"]


async def test_zero_trusted_proxies_ignores_spoofed_header(rec):
    assert await ip_seen(rec, 0, "190.202.14.7") == "198.51.100.1"


async def test_one_trusted_proxy_takes_rightmost(rec):
    # client forged the first value; our proxy appended the real one
    assert await ip_seen(rec, 1, "190.202.14.7, 203.0.113.9") == "203.0.113.9"


async def test_two_trusted_proxies_takes_second_from_right(rec):
    assert await ip_seen(rec, 2, "1.1.1.1, 203.0.113.9, 10.0.0.2") == "203.0.113.9"


async def test_fewer_values_than_proxies_falls_back_to_first(rec):
    assert await ip_seen(rec, 3, "203.0.113.9, 10.0.0.2") == "203.0.113.9"


async def test_trusted_proxies_without_header_uses_peer(rec):
    assert await ip_seen(rec, 1, None) == "198.51.100.1"


# --- blocklist enforcement -------------------------------------------------


@pytest.mark.parametrize(
    "key,value,cookies",
    [
        ("sessions", "s_bad", {"uid": "u_42", "sid": "s_bad"}),
        ("ips", "198.51.100.1", {"uid": "u_42", "sid": "s_ok"}),
        ("users", "u_42", {"uid": "u_42"}),
    ],
)
async def test_blocked_request_gets_403_and_app_not_called(rec, key, value, cookies):
    rec.version = 3
    rec.blocklist[key] = [value]
    calls = []
    mw = make_mw(build_app(calls), rec.client())
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        r = await c.get("/items/1", cookies=cookies)
    assert r.status_code == 403
    assert r.json() == {"error": "blocked"}
    assert calls == []


async def test_unlisted_request_passes(rec):
    rec.version = 3
    rec.blocklist["sessions"] = ["s_bad"]
    calls = []
    mw = make_mw(build_app(calls), rec.client())
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        r = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_good"})
    assert r.status_code == 200 and calls == ["1"]


async def test_blocklist_304_keeps_list_and_sends_since(rec):
    rec.version = 3
    rec.blocklist["sessions"] = ["s_bad"]
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    rec.blocklist["sessions"] = []  # would change, but version is the same -> 304
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        r = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_bad"})
    assert r.status_code == 403


async def test_blocklist_update_replaces_list(rec):
    rec.version = 1
    rec.blocklist["sessions"] = ["s_bad"]
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    rec.version, rec.blocklist["sessions"] = 2, []
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        r = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_bad"})
    assert r.status_code == 200


# --- fail open -------------------------------------------------------------


async def test_engine_down_requests_still_work_and_last_list_kept(rec):
    rec.version = 1
    rec.blocklist["sessions"] = ["s_bad"]
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    rec.fail = True
    await mw.refresh_blocklist()  # must not raise
    async with client_for(mw) as c:
        ok = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_ok"})
        bad = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_bad"})
        await c.get("/items/2", cookies={"uid": "u_42", "sid": "s_ok"})
    assert ok.status_code == 200
    assert bad.status_code == 403
    await mw.flush()  # must not raise; batch kept
    rec.fail = False
    await mw.flush()
    # two passed requests plus the one it rejected (reported as 403)
    assert [e["status"] for e in rec.events] == [200, 403, 200]


async def test_failed_flush_is_retried_with_same_ids(rec):
    mw = make_mw(build_app(), rec.client())
    async with client_for(mw) as c:
        await c.get("/items/1", cookies={"uid": "u_42"})
    rec.fail = True
    await mw.flush()
    rec.fail = False
    await mw.flush()
    await mw.flush()  # nothing left to send
    assert len(rec.batches) == 1 and len(rec.events) == 1


async def test_queue_is_bounded_dropping_oldest(rec):
    mw = make_mw(build_app(), rec.client(), max_queue=3)
    async with client_for(mw) as c:
        for i in range(5):
            await c.get(f"/items/{i}", cookies={"uid": "u_42"})
    await mw.flush()
    assert len(rec.events) == 3
    assert [e["route"] for e in rec.events] == ["/items/{item_id}"] * 3


async def test_bad_blocklist_payload_is_ignored(rec):
    rec.version = 1
    rec.blocklist["sessions"] = ["s_bad"]
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    rec.blocklist = None  # handler will blow up building the payload -> 500
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        r = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_bad"})
    assert r.status_code == 403


async def test_callable_errors_never_break_the_request(rec):
    def boom(request):
        raise RuntimeError("nope")

    mw = VeridianMiddleware(
        build_app(), token=TOKEN, base_url=ENGINE_URL,
        user_id=boom, session_id=boom, http_client=rec.client(), background=False,
    )
    async with client_for(mw) as c:
        r = await c.get("/items/1")
    assert r.status_code == 200


async def test_an_app_that_raises_is_reported_as_a_500(rec):
    async def app(scope, receive, send):
        # A bare ASGI app: FastAPI would send its own 500 first, this one starts no response.
        raise RuntimeError("app bug")

    mw = make_mw(app, rec.client())
    transport = httpx.ASGITransport(app=mw, client=("198.51.100.1", 5555), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as c:
        await c.get("/boom", cookies={"uid": "u_42", "sid": "s_1"})
    await mw.flush()
    (e,) = rec.events
    assert e["status"] == 500  # the engine accepts 100-599 only


async def test_a_user_without_a_session_is_not_reported(rec):
    """The engine requires a session, so the event would only be rejected."""
    mw = make_mw(build_app(), rec.client())
    async with client_for(mw) as c:
        r = await c.get("/items/1", cookies={"uid": "u_42"})
    assert r.status_code == 200
    assert len(mw.queue) == 0
    await mw.flush()
    assert rec.events == []


# --- end to end against the fake engine ------------------------------------

SEED = [{"user_id": "u_42", "ips": ["190.202.14.7"],
         "routes": ["GET /items/{item_id}", "GET /payouts/bank-account"]}]
ROUTES = {"POST /payouts/bank-account": "critical"}


async def test_end_to_end_attacker_is_blocked_owner_is_not():
    engine = create_engine(tokens={TOKEN}, seed=SEED, routes=ROUTES)
    engine_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=engine), base_url=ENGINE_URL)
    # trusted_proxies=1: a proxy of ours puts the real client IP last in X-Forwarded-For
    mw = make_mw(build_app(), engine_client, trusted_proxies=1)

    def xff(ip):
        return {"X-Forwarded-For": f"9.9.9.9, {ip}"}

    async with client_for(mw) as c:
        attacker = {"uid": "u_42", "sid": "s_atk"}
        owner = {"uid": "u_42", "sid": "s_own"}
        r = await c.post("/payouts/bank-account", content=b"x", cookies=attacker, headers=xff("203.0.113.9"))
        assert r.status_code == 200  # the triggering request already passed
        await mw.flush()
        await mw.refresh_blocklist()
        r = await c.get("/items/1", cookies=attacker, headers=xff("203.0.113.9"))
        assert r.status_code == 403
        # same session from another IP is still blocked (session is listed)
        r = await c.get("/items/1", cookies=attacker, headers=xff("190.202.14.7"))
        assert r.status_code == 403
        # the owner, own session and IP, keeps working
        r = await c.get("/items/1", cookies=owner, headers=xff("190.202.14.7"))
        assert r.status_code == 200


# --- excluded paths ----------------------------------------------------------


def app_with_static():
    app = build_app()

    @app.get("/static/app.css")
    async def css():
        return {"css": True}

    @app.get("/staticky")
    async def staticky():
        return {"ok": True}

    return app


async def test_static_requests_are_not_reported_by_default(rec):
    mw = make_mw(app_with_static(), rec.client())
    async with client_for(mw) as c:
        r = await c.get("/static/app.css", cookies={"uid": "u_42", "sid": "s_1"})
    assert r.status_code == 200
    assert "set-cookie" not in r.headers
    await mw.flush()
    assert rec.events == []


async def test_exclusion_matches_whole_path_segments_only(rec):
    mw = make_mw(app_with_static(), rec.client())
    async with client_for(mw) as c:
        await c.get("/staticky", cookies={"uid": "u_42"})
    await mw.flush()
    assert [e["route"] for e in rec.events] == ["/staticky"]


async def test_excluded_paths_skip_the_blocklist(rec):
    # Deliberate: a blocked attacker only has to be stopped on app routes.
    rec.blocklist = {"users": ["u_42"], "sessions": [], "ips": []}
    rec.version = 1
    mw = make_mw(app_with_static(), rec.client())
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        assert (await c.get("/static/app.css", cookies={"uid": "u_42"})).status_code == 200
        assert (await c.get("/items/1", cookies={"uid": "u_42"})).status_code == 403


async def test_exclude_paths_is_configurable(rec):
    mw = make_mw(app_with_static(), rec.client(), exclude_paths=())
    async with client_for(mw) as c:
        await c.get("/static/app.css", cookies={"uid": "u_42"})
    await mw.flush()
    assert [e["route"] for e in rec.events] == ["/static/app.css"]


# --- blocked requests are reported -----------------------------------------


async def test_blocked_request_is_reported_as_403_event(rec):
    rec.version = 3
    rec.blocklist["sessions"] = ["s_bad"]
    calls = []
    mw = make_mw(build_app(calls), rec.client(), trusted_proxies=1)
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        r = await c.get(
            "/items/99",
            cookies={"uid": "u_42", "sid": "s_bad", "vrd_did": "d_77"},
            headers={"X-Forwarded-For": "203.0.113.9", "User-Agent": "UA-test"},
        )
    assert r.status_code == 403 and calls == []
    await mw.flush()
    (e,) = rec.events
    assert e["status"] == 403
    assert e["bytes"] == len(r.content)
    assert e["user_id"] == "u_42" and e["session_id"] == "s_bad"
    assert e["ip"] == "203.0.113.9" and e["device_id"] == "d_77"
    assert e["method"] == "GET"
    assert e["ts"].endswith("Z") and e["id"]
    assert "body" not in e


async def test_blocked_request_reports_route_template_without_calling_app(rec):
    rec.version = 3
    rec.blocklist["sessions"] = ["s_bad"]
    calls = []
    mw = make_mw(build_app(calls), rec.client())
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        await c.get("/items/12345", cookies={"uid": "u_42", "sid": "s_bad"})
        await c.post("/payouts/bank-account", cookies={"uid": "u_42", "sid": "s_bad"})
    assert calls == []
    await mw.flush()
    assert [(e["method"], e["route"]) for e in rec.events] == [
        ("GET", "/items/{item_id}"),
        ("POST", "/payouts/bank-account"),
    ]


async def test_blocked_request_to_unknown_path_reports_raw_path(rec):
    rec.version = 3
    rec.blocklist["sessions"] = ["s_bad"]
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        await c.get("/nope/1", cookies={"uid": "u_42", "sid": "s_bad"})
    await mw.flush()
    assert rec.events[0]["route"] == "/nope/1" and rec.events[0]["status"] == 403


async def test_blocked_request_without_identity_is_not_reported(rec):
    rec.version = 3
    rec.blocklist["ips"] = ["198.51.100.1"]
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        r = await c.get("/items/1")
    assert r.status_code == 403
    await mw.flush()
    assert rec.events == []


async def test_excluded_paths_blocked_or_not_are_never_reported(rec):
    rec.version = 3
    rec.blocklist["sessions"] = ["s_bad"]
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        await c.get("/static/app.css", cookies={"uid": "u_42", "sid": "s_bad"})
    await mw.flush()
    assert rec.events == []


# --- pre-check of critical routes (contract 9) --------------------------------

BANK = {"uid": "u_42", "sid": "s_atk"}


async def post_bank(mw, cookies=BANK):
    async with client_for(mw) as c:
        return await c.post("/payouts/bank-account", content=b"x", cookies=cookies)


async def test_check_block_verdict_answers_403_and_app_not_called(rec):
    rec.check_body = {"verdict": "block", "decision_id": "d_1"}
    calls = []
    mw = make_mw(build_app(calls), rec.client())
    await mw.refresh_routes()
    r = await post_bank(mw)
    assert r.status_code == 403 and r.json() == {"error": "blocked"}
    assert calls == []
    assert len(rec.checks) == 1


async def test_check_allow_verdict_calls_app(rec):
    calls = []
    mw = make_mw(build_app(calls), rec.client())
    await mw.refresh_routes()
    r = await post_bank(mw)
    assert r.status_code == 200 and calls == ["bank"]
    assert len(rec.checks) == 1


@pytest.mark.parametrize(
    "status, body",
    [
        (500, {"error": "boom"}),
        (404, {"error": "not_found"}),
        (401, {"error": "unauthorized"}),
        (200, {"verdict": "step_up"}),
        (200, {"verdict": None}),
        (200, {"nope": 1}),
        (200, ["block"]),
        (200, b"<html>not json</html>"),
    ],
)
async def test_check_failure_or_garbage_lets_the_request_through(rec, status, body):
    rec.check_status, rec.check_body = status, body
    calls = []
    mw = make_mw(build_app(calls), rec.client())
    await mw.refresh_routes()
    r = await post_bank(mw)
    assert r.status_code == 200 and calls == ["bank"]


async def test_check_engine_down_lets_the_request_through(rec):
    calls = []
    mw = make_mw(build_app(calls), rec.client())
    await mw.refresh_routes()
    rec.fail = True
    r = await post_bank(mw)
    assert r.status_code == 200 and calls == ["bank"]


async def test_check_slower_than_the_timeout_lets_the_request_through(rec):
    rec.check_hang = asyncio.Event()  # never set: the engine "hangs"
    calls = []
    mw = make_mw(build_app(calls), rec.client(), check_timeout=0.05)
    await mw.refresh_routes()
    r = await post_bank(mw)
    assert r.status_code == 200 and calls == ["bank"]


async def test_check_timeout_defaults_to_12_seconds(rec):
    mw = make_mw(build_app(), rec.client())
    assert mw.check_timeout == 12.0 and mw.routes_poll_interval > 0


async def test_non_critical_routes_are_not_checked(rec):
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_routes()
    async with client_for(mw) as c:
        await c.get("/items/1", cookies=BANK)  # normal
    assert rec.checks == []


async def test_same_method_and_template_only_gets_checked(rec):
    # GET on the bank route is not the critical POST (the class is per method + route)
    app = build_app()

    @app.get("/payouts/bank-account")
    async def view():
        return {"ok": True}

    mw = make_mw(app, rec.client())
    await mw.refresh_routes()
    async with client_for(mw) as c:
        r = await c.get("/payouts/bank-account", cookies=BANK)
    assert r.status_code == 200 and rec.checks == []


async def test_check_event_matches_the_batched_event_id(rec):
    mw = make_mw(build_app(), rec.client(), trusted_proxies=1)
    await mw.refresh_routes()
    async with client_for(mw) as c:
        await c.post(
            "/payouts/bank-account", content=b"x", cookies={**BANK, "vrd_did": "d_7"},
            headers={"X-Forwarded-For": "203.0.113.9", "User-Agent": "UA-test"},
        )
    await mw.flush()
    (check,) = rec.checks
    (sent,) = rec.events
    ev = check["event"]
    assert ev["id"] == sent["id"]
    assert ev["user_id"] == "u_42" and ev["session_id"] == "s_atk"
    assert ev["ip"] == "203.0.113.9" and ev["device_id"] == "d_7"
    assert ev["method"] == "POST" and ev["route"] == "/payouts/bank-account"
    assert ev["ts"].endswith("Z") and ev["user_agent"] == "UA-test"
    # The engine keeps the first write for an id, so the pre-check carries a
    # placeholder status and the real one only travels in the batch.
    assert ev["status"] == 200 and ev["bytes"] == 0
    assert sent["status"] == 200


async def test_blocked_by_check_is_batched_as_403_with_the_same_id(rec):
    rec.check_body = {"verdict": "block", "decision_id": "d_1"}
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_routes()
    r = await post_bank(mw)
    await mw.flush()
    (sent,) = rec.events
    assert sent["id"] == rec.checks[0]["event"]["id"]
    assert sent["status"] == 403 and sent["bytes"] == len(r.content)
    assert sent["route"] == "/payouts/bank-account"
    assert "set-cookie" not in r.headers  # no device cookie for a refused client


async def test_new_device_cookie_is_issued_on_allow_and_matches_the_check(rec):
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_routes()
    r = await post_bank(mw)
    did = rec.checks[0]["event"]["device_id"]
    assert did.startswith("d_") and f"vrd_did={did}" in r.headers["set-cookie"]


async def test_critical_request_without_user_or_session_is_not_checked(rec):
    calls = []
    mw = make_mw(build_app(calls), rec.client())
    await mw.refresh_routes()
    async with client_for(mw) as c:
        await c.post("/payouts/bank-account", content=b"x")
        await c.post("/payouts/bank-account", content=b"x", cookies={"uid": "u_42"})
    assert rec.checks == [] and calls == ["bank", "bank"]


async def test_routes_never_loaded_means_no_check(rec):
    calls = []
    mw = make_mw(build_app(calls), rec.client())
    r = await post_bank(mw)
    assert r.status_code == 200 and rec.checks == [] and calls == ["bank"]


async def test_routes_load_failure_means_no_check(rec):
    rec.routes_status = 500
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_routes()
    await post_bank(mw)
    assert rec.checks == []


async def test_routes_refresh_picks_up_a_newly_critical_route(rec):
    rec.routes = []
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_routes()
    await post_bank(mw)
    assert rec.checks == []
    rec.routes = [{"method": "POST", "route": "/payouts/bank-account", "class": "critical"}]
    await mw.refresh_routes()
    await post_bank(mw)
    assert len(rec.checks) == 1


async def test_routes_refresh_keeps_last_critical_set_on_errors(rec):
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_routes()
    rec.routes_status = 503
    await mw.refresh_routes()
    rec.routes_status = 200
    rec.fail = True
    await mw.refresh_routes()
    rec.fail = False
    await post_bank(mw)
    assert len(rec.checks) == 1


async def test_routes_garbage_payload_keeps_last_critical_set(rec):
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_routes()
    rec.routes = "nonsense"
    await mw.refresh_routes()
    await post_bank(mw)
    assert len(rec.checks) == 1


async def test_blocklisted_request_is_refused_without_a_check(rec):
    rec.version = 1
    rec.blocklist["sessions"] = ["s_atk"]
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    await mw.refresh_routes()
    r = await post_bank(mw)
    assert r.status_code == 403 and rec.checks == []


async def test_end_to_end_critical_request_is_stopped_before_it_runs():
    engine = create_engine(tokens={TOKEN}, seed=SEED, routes=ROUTES)
    engine_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=engine), base_url=ENGINE_URL)
    calls = []
    mw = make_mw(build_app(calls), engine_client, trusted_proxies=1)
    await mw.refresh_routes()

    def xff(ip):
        return {"X-Forwarded-For": f"9.9.9.9, {ip}"}

    async with client_for(mw) as c:
        r = await c.post("/payouts/bank-account", content=b"x", cookies={"uid": "u_42", "sid": "s_atk"},
                         headers=xff("203.0.113.9"))
        assert r.status_code == 403 and r.json() == {"error": "blocked"} and calls == []
        # the owner's own use of the critical route is allowed
        r = await c.post("/payouts/bank-account", content=b"x", cookies={"uid": "u_42", "sid": "s_own"},
                         headers=xff("190.202.14.7"))
        assert r.status_code == 200 and calls == ["bank"]
        await mw.flush()
        # the engine already blocked the attacker's session: even a normal route is refused
        await mw.refresh_blocklist()
        r = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_atk"}, headers=xff("203.0.113.9"))
        assert r.status_code == 403


async def test_background_loads_routes_on_startup(rec):
    mw = make_mw(build_app(), rec.client(), background=True, routes_poll_interval=3600.0,
                 flush_interval=3600.0, poll_interval=3600.0)
    async with client_for(mw) as c:
        await c.get("/items/1")  # the first request starts the background tasks
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert ("POST", "/payouts/bank-account") in mw.critical
    await mw.aclose()


# --- engine auth and rate-limit answers (401, 429) fail open ------------------


class Rejecting:
    """Engine stand-in that answers every request with one status and counts them."""

    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}
        self.paths = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        error = "unauthorized" if self.status == 401 else "rate_limited"
        return httpx.Response(self.status, json={"error": error}, headers=self.headers)

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


REJECTIONS = [
    pytest.param(401, {}, id="401"),
    pytest.param(429, {"Retry-After": "30"}, id="429-retry-after"),
    pytest.param(429, {}, id="429-no-header"),
]


@pytest.mark.parametrize("status, headers", REJECTIONS)
async def test_check_rejected_by_the_engine_lets_the_request_through_without_retry(status, headers):
    calls = []
    engine = Rejecting(status, headers)
    mw = make_mw(build_app(calls), engine.client())
    mw.critical = {("POST", "/payouts/bank-account")}
    r = await post_bank(mw)
    assert r.status_code == 200 and calls == ["bank"]
    assert engine.paths == ["/v1/check"]  # asked once, never retried


@pytest.mark.parametrize("status, headers", REJECTIONS)
async def test_blocklist_rejected_by_the_engine_keeps_the_last_list(status, headers, rec):
    rec.version = 1
    rec.blocklist["sessions"] = ["s_bad"]
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    engine = Rejecting(status, headers)
    mw.client = engine.client()
    await mw.refresh_blocklist()  # must not raise
    assert engine.paths == ["/v1/blocklist"]
    assert mw.blocklist_version == 1
    async with client_for(mw) as c:
        ok = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_ok"})
        bad = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_bad"})
    assert ok.status_code == 200 and bad.status_code == 403


@pytest.mark.parametrize("status, headers", REJECTIONS)
async def test_blocklist_rejected_before_any_load_lets_everything_through(status, headers):
    engine = Rejecting(status, headers)
    mw = make_mw(build_app(), engine.client())
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        r = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_any"})
    assert r.status_code == 200


@pytest.mark.parametrize("status, headers", REJECTIONS)
async def test_routes_rejected_by_the_engine_means_no_check(status, headers):
    calls = []
    engine = Rejecting(status, headers)
    mw = make_mw(build_app(calls), engine.client())
    await mw.refresh_routes()  # must not raise
    r = await post_bank(mw)
    assert r.status_code == 200 and calls == ["bank"]
    assert engine.paths == ["/v1/routes"]


@pytest.mark.parametrize("status, headers", REJECTIONS)
async def test_events_rejected_by_the_engine_stay_queued_and_are_sent_later(status, headers, rec):
    engine = Rejecting(status, headers)
    mw = make_mw(build_app(), engine.client())
    async with client_for(mw) as c:
        r = await c.get("/items/1", cookies={"uid": "u_42"})
    assert r.status_code == 200
    await mw.flush()  # must not raise
    assert engine.paths == ["/v1/events"] and len(mw.queue) == 1
    mw.client = rec.client()
    await mw.flush()
    assert len(rec.events) == 1 and len(mw.queue) == 0


async def test_background_tasks_do_not_hot_loop_on_429():
    engine = Rejecting(429, {"Retry-After": "30"})
    mw = make_mw(build_app(), engine.client(), background=True, flush_interval=0.05,
                 poll_interval=0.05, routes_poll_interval=0.05)
    async with client_for(mw) as c:
        r = await c.get("/items/1", cookies={"uid": "u_42"})  # starts the background tasks
        await asyncio.sleep(0.2)
    await mw.aclose()
    assert r.status_code == 200
    # Three loops at 0.05 s over 0.2 s: about 12 calls; a hot loop would make thousands.
    assert len(engine.paths) < 40


# --- route report (contract 16) ---------------------------------------------


def build_documented_app():
    app = FastAPI()

    @app.post("/transfers", summary="Send money")
    async def transfers():
        """Moves funds to another account."""
        return {}

    @app.get("/static/logo")
    async def logo():
        return {}

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        return {}

    return app


async def test_report_sends_method_route_and_openapi_text_only(rec):
    mw = make_mw(build_documented_app(), rec.client())
    assert await mw.report_routes() is True
    [body] = rec.catalogs
    routes = {(r["method"], r["route"]): r for r in body["routes"]}
    assert routes[("POST", "/transfers")] == {
        "method": "POST", "route": "/transfers", "summary": "Send money",
        "description": "Moves funds to another account.",
    }
    assert ("GET", "/items/{item_id}") in routes
    assert all(set(r) == {"method", "route", "summary", "description"} for r in body["routes"])


async def test_report_skips_excluded_paths(rec):
    mw = make_mw(build_documented_app(), rec.client())
    await mw.report_routes()
    assert all(not r["route"].startswith("/static") for r in rec.catalogs[0]["routes"])


async def test_report_finds_the_openapi_through_wrapped_apps(rec):
    inner = build_documented_app()

    class Wrapper:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            await self.app(scope, receive, send)

    mw = make_mw(Wrapper(inner), rec.client())
    assert await mw.report_routes() is True
    assert rec.catalogs


async def test_an_app_without_openapi_reports_nothing(rec):
    async def bare(scope, receive, send):
        return None

    mw = make_mw(bare, rec.client())
    assert await mw.report_routes() is True  # nothing to report counts as done
    assert rec.catalogs == []


async def test_a_failed_report_returns_false_and_never_raises(rec):
    mw = make_mw(build_documented_app(), rec.client())
    rec.fail = True
    assert await mw.report_routes() is False
    rec.fail = False
    rec.catalog_status = 500
    assert await mw.report_routes() is False


async def test_the_report_runs_once_in_the_background_at_startup(rec):
    mw = make_mw(build_documented_app(), rec.client(), background=True, flush_interval=3600, poll_interval=3600,
                 routes_poll_interval=3600)
    mw._ensure_background()
    for _ in range(50):
        if rec.catalogs:
            break
        await asyncio.sleep(0)
    await mw.aclose()
    assert len(rec.catalogs) == 1


async def test_the_report_can_be_turned_off(rec):
    mw = make_mw(build_documented_app(), rec.client(), background=True, flush_interval=3600, poll_interval=3600,
                 routes_poll_interval=3600, report_routes=False)
    mw._ensure_background()
    for _ in range(50):
        await asyncio.sleep(0)
    await mw.aclose()
    assert rec.catalogs == []
