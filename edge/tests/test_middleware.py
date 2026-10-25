"""Tests for VeridianMiddleware. No real network and no sleeps: background
tasks are disabled and flush()/refresh_blocklist() are driven by hand."""

import json

import httpx
import pytest
from fastapi import FastAPI, Request

from fake_engine import create_app as create_engine
from middleware import VeridianMiddleware

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

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("engine down")
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
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
    assert len(rec.events) == 2  # only the two non-blocked requests were reported


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
