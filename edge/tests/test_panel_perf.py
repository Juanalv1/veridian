import asyncio
import re
import time

import httpx
from fastapi.testclient import TestClient

from panel.cache import TTLCache
from tests.panel_support import make_panel, stub_client

DECISION = {"id": "d_1", "ts": "2026-10-26T10:14:05Z", "user_id": "u_1", "decision": "block", "status": "open",
            "signals": {}, "rationale": "r", "event_ids": []}
ACTIVITY = [{"event_id": "e_1", "ts": "2026-10-26T10:14:03Z", "user_id": "u_1", "decision_id": "d_1",
             "method": "POST", "route": "/x", "verdict": "block", "status": 200}]
BODIES = {"/v1/decisions": {"decisions": [DECISION]}, "/v1/activity": {"activity": ACTIVITY},
          "/v1/blocklist": {"version": 1, "users": [], "ips": []},
          "/v1/users/u_1/profile": {"networks": [], "devices": [], "routes": [], "hours": []}}


class Engine:
    """Canned engine that counts calls per path and can delay every answer."""

    def __init__(self, delay=0.0):
        self.delay, self.calls, self.client = delay, [], None
        self.client = stub_client(self.handle)

    async def handle(self, request):
        self.calls.append(request.url.path)
        if self.delay:
            await asyncio.sleep(self.delay)
        if request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        body = BODIES.get(request.url.path)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404, json={})

    def count(self, path):
        return self.calls.count(path)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def panel_for(engine, clock=None):
    return make_panel(engine.client, **({"clock": clock} if clock else {}))


# -- concurrency ---------------------------------------------------------------------------------

def test_engine_calls_overlap_when_the_user_is_in_the_url():
    engine = Engine(delay=0.2)
    panel = panel_for(engine)
    start = time.perf_counter()
    r = panel.get("/users/u_1/decisions/d_1")
    took = time.perf_counter() - start
    assert r.status_code == 200 and "Bloqueado" in r.text
    assert sorted(set(engine.calls)) == ["/v1/activity", "/v1/blocklist", "/v1/decisions", "/v1/users/u_1/profile"]
    assert took < 0.4, f"4 engine calls of 200 ms took {took:.2f} s: they are not concurrent"


def test_index_needs_two_round_trips_not_four():
    engine = Engine(delay=0.2)
    panel = panel_for(engine)
    start = time.perf_counter()
    assert panel.get("/actividad").status_code == 200
    took = time.perf_counter() - start
    assert took < 0.6, f"took {took:.2f} s: expected about two round trips (decisions+activity+blocklist, then profile)"


# -- TTL cache -----------------------------------------------------------------------------------

def test_decisions_activity_and_blocklist_are_cached_for_one_second():
    engine, clock = Engine(), Clock()
    panel = panel_for(engine, clock)
    panel.get("/users/u_1/decisions/d_1")
    panel.get("/users/u_1/decisions/d_1")
    assert [engine.count(p) for p in ("/v1/decisions", "/v1/activity", "/v1/blocklist")] == [1, 1, 1]
    clock.now += 0.9
    panel.get("/users/u_1/decisions/d_1")
    assert engine.count("/v1/decisions") == 1
    clock.now += 0.2
    panel.get("/users/u_1/decisions/d_1")
    assert [engine.count(p) for p in ("/v1/decisions", "/v1/activity", "/v1/blocklist")] == [2, 2, 2]


def test_profile_is_cached_for_fifteen_seconds():
    engine, clock = Engine(), Clock()
    panel = panel_for(engine, clock)
    profile = "/v1/users/u_1/profile"
    panel.get("/users/u_1/decisions/d_1")
    clock.now += 14
    panel.get("/users/u_1/decisions/d_1")
    assert engine.count(profile) == 1
    clock.now += 2
    panel.get("/users/u_1/decisions/d_1")
    assert engine.count(profile) == 2


def test_mark_normal_invalidates_everything():
    engine, clock = Engine(), Clock()
    panel = panel_for(engine, clock)
    panel.get("/users/u_1/decisions/d_1")
    r = panel.post("/decisions/d_1/normal", data={"user": "u_1"})
    assert r.status_code == 303
    before = list(engine.calls)
    panel.get("/users/u_1/decisions/d_1")
    fetched = engine.calls[len(before):]
    assert sorted(set(fetched)) == ["/v1/activity", "/v1/blocklist", "/v1/decisions", "/v1/users/u_1/profile"]


def test_failed_engine_answers_are_not_cached():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(500, json={})

    client = stub_client(handler)
    panel = make_panel(client, clock=Clock())
    panel.get("/actividad")
    panel.get("/actividad")
    assert calls.count("/v1/decisions") == 2


def test_cache_single_flight_shares_one_call():
    cache, calls = TTLCache(), []

    async def fetch():
        calls.append(1)
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"n": len(calls)})

    async def run():
        return await asyncio.gather(*(cache.get("k", 1.0, fetch) for _ in range(5)))

    results = asyncio.run(run())
    assert len(calls) == 1 and all(r.json() == {"n": 1} for r in results)


def test_cache_single_flight_shares_errors_and_recovers():
    cache, calls = TTLCache(), []

    async def boom():
        calls.append(1)
        await asyncio.sleep(0.02)
        raise httpx.ConnectError("down")

    async def ok():
        return httpx.Response(200, json={})

    async def run():
        results = await asyncio.gather(*(cache.get("k", 1.0, boom) for _ in range(3)), return_exceptions=True)
        return results, await cache.get("k", 1.0, ok)

    results, later = asyncio.run(run())
    assert len(calls) == 1 and all(isinstance(r, httpx.ConnectError) for r in results)
    assert later.status_code == 200


def test_cache_invalidate_drops_entries_and_late_results():
    clock, cache = Clock(), None
    cache = TTLCache(clock)

    async def run():
        slow_started = asyncio.Event()
        release = asyncio.Event()

        async def slow():
            slow_started.set()
            await release.wait()
            return httpx.Response(200, json={"v": "old"})

        async def fresh():
            return httpx.Response(200, json={"v": "new"})

        pending = asyncio.ensure_future(cache.get("k", 5.0, slow))
        await slow_started.wait()
        cache.invalidate()
        release.set()
        assert (await pending).json() == {"v": "old"}
        return await cache.get("k", 5.0, fresh)  # the late result was not stored

    assert asyncio.run(run()).json() == {"v": "new"}


# -- Server-Timing -------------------------------------------------------------------------------

def test_server_timing_reports_engine_and_render_time():
    panel = panel_for(Engine(delay=0.05))
    h = panel.get("/actividad").headers["server-timing"]
    m = re.fullmatch(r"engine;dur=(\d+(?:\.\d+)?), render;dur=(\d+(?:\.\d+)?)", h)
    assert m, h
    assert float(m.group(1)) >= 50  # at least one engine round trip
    assert panel.post("/decisions/d_1/normal", data={"user": "u_1"}, headers={"HX-Request": "true"}).headers["server-timing"]


# -- skeleton and prefetch markup ----------------------------------------------------------------

def test_skeleton_template_is_inert_and_matches_the_detail_layout():
    html = panel_for(Engine()).get("/users/u_1/decisions/d_1").text
    tpl = re.search(r'<template id="detalle-skeleton">(.*?)</template>', html, re.S)
    assert tpl, "skeleton template missing"
    body = tpl.group(1)
    assert 'aria-hidden="true"' in body
    assert body.count('class="sk-row') >= 6  # comparison table rows
    for part in ("sk-meta", "sk-verdict", "sk-line"):
        assert part in body
    # Hidden by default: the detail region is not busy and the skeleton lives only in the template.
    detail = re.search(r'<section id="detalle"[^>]*>', html).group(0)
    assert "aria-busy" not in detail
    assert html.count("sk-verdict") == 1


def test_preload_extension_is_wired_and_served_locally():
    panel = panel_for(Engine())
    html = panel.get("/users/u_1/decisions/d_1").text
    assert 'hx-ext="preload"' in html and "/static/preload.js" in html
    assert re.search(r"<nav[^>]*preload=\"mouseenter\"", html)
    assert "unpkg" not in html and "cdn.jsdelivr" not in html
    js = panel.get("/static/preload.js")
    assert js.status_code == 200 and "defineExtension('preload'" in js.text


def test_nav_prefetch_does_not_abort_polling():
    html = panel_for(Engine()).get("/users/u_1/decisions/d_1").text
    nav = re.search(r"<nav[^>]*>", html).group(0)
    assert 'hx-sync="closest a:replace"' in nav  # per-link sync: a hover never aborts the poll
    assert re.search(r'<main id="live"[^>]*hx-sync="this:replace"', html)


def test_skeleton_css_respects_reduced_motion():
    css = TestClient(panel_for(Engine()).app).get("/static/panel.css").text
    assert ".sk-" in css and "@keyframes sk-shimmer" in css
    reduced = css[css.index("prefers-reduced-motion"):]
    assert "sk-" in reduced and "animation:none" in reduced.replace(" ", "")
