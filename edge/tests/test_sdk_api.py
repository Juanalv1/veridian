"""The simple SDK API: defaults from the environment, `user`/`session`,
session detection, clear startup errors, `add_middleware` as the one way to
install it, and the published quickstart snippet run as is."""

import hashlib
import json
import html
import logging
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request

import veridian_sdk.middleware as sdk_middleware
from fake_engine import create_app as create_engine
from veridian_sdk import DEFAULT_BASE_URL, VeridianConfigError, VeridianMiddleware

KEY = "tok-sdk"
ENGINE_URL = "http://engine"
REPO = Path(__file__).resolve().parents[2]
QUICKSTART = REPO / "sdk" / "python" / "examples" / "quickstart.py"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("VERIDIAN_API_KEY", raising=False)
    monkeypatch.delenv("VERIDIAN_URL", raising=False)


def hashed(value: str) -> str:
    return "s_" + hashlib.sha256(value.encode()).hexdigest()[:16]


class Engine:
    """Engine stand-in that records what the SDK sends."""

    def __init__(self, routes=()):
        self.auth = []
        self.events = []
        self.checks = []
        self.routes = list(routes)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("authorization"))
        if request.url.path == "/v1/routes":
            return httpx.Response(200, json={"routes": self.routes, "default": "normal"})
        if request.url.path == "/v1/check":
            self.checks.append(json.loads(request.content)["event"])
            return httpx.Response(200, json={"verdict": "allow", "decision_id": None})
        if request.url.path == "/v1/events":
            self.events.extend(json.loads(request.content)["events"])
            return httpx.Response(202, json={"accepted": 1})
        return httpx.Response(200, json={"version": 1, "users": [], "sessions": [], "ips": []})

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def an_app():
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def item(item_id: str, request: Request):
        return {"id": item_id}

    @app.post("/payouts/bank-account")
    async def bank():
        return {"ok": True}

    return app


def instance(asgi_app) -> VeridianMiddleware:
    """The VeridianMiddleware inside a Starlette app's built middleware stack."""
    node = asgi_app.middleware_stack
    for _ in range(16):
        if isinstance(node, VeridianMiddleware):
            return node
        node = getattr(node, "app", None)
    raise AssertionError("VeridianMiddleware not in the stack")


def client_for(asgi_app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app, client=("198.51.100.1", 5555)),
                             base_url="http://app")


# --- configuration -------------------------------------------------------------------------------


def test_the_key_and_url_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("VERIDIAN_API_KEY", "from-env")
    monkeypatch.setenv("VERIDIAN_URL", "https://engine.example/")
    mw = VeridianMiddleware(an_app(), user=lambda r: None, background=False)
    assert mw.token == "from-env"
    assert mw.base_url == "https://engine.example"


def test_without_a_url_the_production_engine_is_used(monkeypatch):
    monkeypatch.setenv("VERIDIAN_API_KEY", "k")
    mw = VeridianMiddleware(an_app(), user=lambda r: None, background=False)
    assert mw.base_url == DEFAULT_BASE_URL == "https://veridian-engine.onrender.com"


def test_arguments_win_over_the_environment(monkeypatch):
    monkeypatch.setenv("VERIDIAN_API_KEY", "from-env")
    monkeypatch.setenv("VERIDIAN_URL", "https://env.example")
    mw = VeridianMiddleware(an_app(), api_key="arg", base_url="http://arg.example", user=lambda r: None,
                            background=False)
    assert (mw.token, mw.base_url) == ("arg", "http://arg.example")


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_missing_key_is_a_clear_error_naming_the_variable(key):
    with pytest.raises(VeridianConfigError) as err:
        VeridianMiddleware(an_app(), api_key=key, user=lambda r: None)
    assert "VERIDIAN_API_KEY" in str(err.value) and "panel" in str(err.value)
    assert isinstance(err.value, ValueError)


@pytest.mark.parametrize("url", ["veridian-engine.onrender.com", "ftp://x", "http://"])
def test_a_bad_url_is_a_clear_error_naming_the_variable(url):
    with pytest.raises(VeridianConfigError) as err:
        VeridianMiddleware(an_app(), api_key="k", base_url=url, user=lambda r: None)
    assert "VERIDIAN_URL" in str(err.value)


def test_add_middleware_raises_the_error_when_the_app_starts():
    app = an_app()
    app.add_middleware(VeridianMiddleware, user=lambda r: None)
    with pytest.raises(VeridianConfigError):
        app.build_middleware_stack()


@pytest.mark.parametrize("kw", [
    {"api_key": "a", "token": "b"},
    {"user": lambda r: None, "user_id": lambda r: None},
    {"session": lambda r: None, "session_id": lambda r: None},
])
def test_old_and_new_names_together_are_refused(kw):
    kw.setdefault("api_key", "k")
    if "user" not in kw and "user_id" not in kw:
        kw["user"] = lambda r: None
    with pytest.raises(VeridianConfigError):
        VeridianMiddleware(an_app(), **kw)


@pytest.mark.parametrize("name", ["user", "session"])
def test_identity_must_be_callable(name):
    kw = {"api_key": "k", "user": lambda r: None, name: "u_1"}
    with pytest.raises(VeridianConfigError) as err:
        VeridianMiddleware(an_app(), **kw)
    assert name in str(err.value)


def test_without_user_it_warns_that_nothing_will_be_reported(caplog):
    with caplog.at_level(logging.WARNING, logger="veridian"):
        VeridianMiddleware(an_app(), api_key="k", background=False)
    assert any("user" in r.getMessage() for r in caplog.records)


# --- identity ------------------------------------------------------------------------------------


async def test_add_middleware_with_user_sends_events_with_the_env_key(monkeypatch):
    monkeypatch.setenv("VERIDIAN_API_KEY", KEY)
    monkeypatch.setenv("VERIDIAN_URL", ENGINE_URL)
    engine = Engine()
    app = an_app()
    app.add_middleware(VeridianMiddleware, user=lambda request: request.state.user.id,
                       http_client=engine.client(), background=False)

    @app.middleware("http")  # the app's own auth: added after Veridian, so it runs first
    async def login(request: Request, call_next):
        if request.cookies.get("uid"):
            request.state.user = type("User", (), {"id": int(request.cookies["uid"])})()
        return await call_next(request)

    async with client_for(app) as c:
        r = await c.get("/items/7", cookies={"uid": "42"})
        assert r.status_code == 200
        await instance(app).flush()
    (e,) = engine.events
    assert e["user_id"] == "42"  # ints become strings
    assert engine.auth[-1] == f"Bearer {KEY}"


async def test_a_user_lookup_that_fails_because_nobody_logged_in_is_quiet(caplog):
    engine = Engine()
    mw = VeridianMiddleware(an_app(), api_key=KEY, base_url=ENGINE_URL, user=lambda request: request.state.user.id,
                            http_client=engine.client(), background=False)
    with caplog.at_level(logging.WARNING, logger="veridian"):
        async with client_for(mw) as c:
            r = await c.get("/items/1")
    assert r.status_code == 200
    await mw.flush()
    assert engine.events == []
    assert not [rec for rec in caplog.records if rec.levelno >= logging.ERROR]


async def test_an_unexpected_error_in_user_is_logged_and_never_breaks_the_request(caplog):
    def boom(request):
        raise RuntimeError("nope")

    mw = VeridianMiddleware(an_app(), api_key=KEY, base_url=ENGINE_URL, user=boom, http_client=Engine().client(),
                            background=False)
    with caplog.at_level(logging.WARNING, logger="veridian"):
        async with client_for(mw) as c:
            r = await c.get("/items/1")
    assert r.status_code == 200
    assert any(rec.levelno >= logging.ERROR for rec in caplog.records)


# --- session detection ---------------------------------------------------------------------------


async def event_with(cookies=None, headers=None, **kw):
    engine = Engine()
    mw = VeridianMiddleware(an_app(), api_key=KEY, base_url=ENGINE_URL, user=lambda r: "u_1",
                            http_client=engine.client(), background=False, **kw)
    async with client_for(mw) as c:
        await c.get("/items/1", cookies=cookies or {}, headers=headers or {})
    await mw.flush()
    (e,) = engine.events
    return e


@pytest.mark.parametrize("cookie", ["sessionid", "connect.sid", "sid", "session_id", "PHPSESSID", "JSESSIONID"])
async def test_a_known_session_cookie_is_detected_and_only_its_hash_is_sent(cookie):
    e = await event_with(cookies={cookie: "secret-value-123"})
    assert e["session_id"] == hashed("secret-value-123")
    assert "secret-value-123" not in str(e)


async def test_starlettes_session_cookie_is_ignored_because_it_changes_on_every_response():
    e = await event_with(cookies={"session": "eyJ1c2VyIjogMX0=.abc.def"})
    assert e["session_id"] is None


async def test_a_bearer_token_is_the_session_when_there_is_no_cookie():
    e = await event_with(headers={"Authorization": "Bearer jwt.payload.sig"})
    assert e["session_id"] == hashed("jwt.payload.sig")
    assert "jwt.payload.sig" not in str(e)


async def test_basic_auth_is_never_a_session():
    e = await event_with(headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert e["session_id"] is None


async def test_a_cookie_wins_over_the_bearer_token():
    e = await event_with(cookies={"sessionid": "c"}, headers={"Authorization": "Bearer t"})
    assert e["session_id"] == hashed("c")


async def test_an_explicit_session_wins_and_is_not_second_guessed():
    e = await event_with(cookies={"sessionid": "c"}, session=lambda r: None)
    assert e["session_id"] is None
    e = await event_with(cookies={"sessionid": "c"}, session=lambda r: "s_mine")
    assert e["session_id"] == "s_mine"


async def test_the_detected_session_lets_critical_routes_be_checked():
    engine = Engine(routes=[{"method": "POST", "route": "/payouts/bank-account", "class": "critical"}])
    mw = VeridianMiddleware(an_app(), api_key=KEY, base_url=ENGINE_URL, user=lambda r: "u_1",
                            http_client=engine.client(), background=False)
    await mw.refresh_routes()
    async with client_for(mw) as c:
        r = await c.post("/payouts/bank-account", cookies={"sessionid": "abc"})
    assert r.status_code == 200
    (check,) = engine.checks
    assert check["session_id"] == hashed("abc")


# --- the old API keeps working -------------------------------------------------------------------


async def test_the_old_api_still_works():
    engine = Engine()
    mw = VeridianMiddleware(an_app(), token=KEY, base_url=ENGINE_URL, user_id=lambda r: "u_9",
                            session_id=lambda r: "s_9", http_client=engine.client(), background=False)
    async with client_for(mw) as c:
        await c.get("/items/1")
    await mw.flush()
    (e,) = engine.events
    assert (e["user_id"], e["session_id"]) == ("u_9", "s_9")
    assert engine.auth[-1] == f"Bearer {KEY}"


# --- the published snippet -----------------------------------------------------------------------


def snippet_code() -> str:
    return QUICKSTART.read_text(encoding="utf-8").strip()


def code_lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.strip().splitlines()]


async def test_the_quickstart_runs_as_is_against_the_fake_engine(monkeypatch):
    engine = create_engine(tokens={KEY}, seed=[], routes={})
    engine_transport = httpx.ASGITransport(app=engine)

    real_client = httpx.AsyncClient

    def routed_client(*args, **kwargs):  # the SDK's own client reaches the fake engine; others keep theirs
        kwargs.setdefault("transport", engine_transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(sdk_middleware.httpx, "AsyncClient", routed_client)
    monkeypatch.setenv("VERIDIAN_API_KEY", KEY)
    monkeypatch.setenv("VERIDIAN_URL", ENGINE_URL)

    namespace: dict = {}
    exec(compile(snippet_code(), str(QUICKSTART), "exec"), namespace)  # exactly what the docs show
    app = namespace["app"]

    @app.get("/facturas")
    async def invoices():
        return {"ok": True}

    @app.middleware("http")  # the app's own login, added after Veridian as the snippet says
    async def login(request: Request, call_next):
        request.state.user = type("User", (), {"id": "u_42"})()
        return await call_next(request)

    async with client_for(app) as c:
        r = await c.get("/facturas", cookies={"sessionid": "abc"})
        assert r.status_code == 200
        mw = instance(app)
        await mw.flush()
        await mw.aclose()
    (project,) = engine.state.projects.values()
    (event,) = project.state.log
    assert (event.user_id, event.route, event.session_id) == ("u_42", "/facturas", hashed("abc"))


def visible_text(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


@pytest.mark.parametrize("doc", ["README.md", "sdk/python/README.md"])
def test_the_readmes_show_the_quickstart_exactly(doc):
    text = (REPO / doc).read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", text, flags=re.S)
    assert code_lines(snippet_code()) in [code_lines(b) for b in blocks]


def test_the_landing_shows_the_quickstart_exactly():
    page = (REPO / "landing" / "index.html").read_text(encoding="utf-8")
    block = re.search(r'<code id="snippet-python">(.*?)</code>', page, flags=re.S)
    assert block and code_lines(visible_text(block.group(1))) == code_lines(snippet_code())


def test_the_panel_template_shows_the_quickstart_exactly():
    page = (REPO / "edge" / "panel" / "templates" / "partials" / "snippet.html").read_text(encoding="utf-8")
    block = re.search(r'<code id="snippet-python"[^>]*>(.*?)</code>', page, flags=re.S)
    assert block and code_lines(visible_text(block.group(1))) == code_lines(snippet_code())
