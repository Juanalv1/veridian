"""VeridianMiddleware and self-unlock by email: the optional `user_email`, and the block message that says an
email was sent, only when the engine's signal says so (`self_unlock_sessions` in the blocklist, `self_unlock` in the
check answer). Everything else keeps the generic body exactly."""

import logging

import pytest
from fastapi import Request

from tests.test_middleware import BANK, build_app, client_for, make_mw, post_bank, rec  # noqa: F401  (rec is a fixture)
from veridian_sdk import VeridianConfigError, VeridianMiddleware

MESSAGE = "Detectamos un acceso inusual. Te enviamos un correo para confirmar que fuiste tú."
GENERIC = {"error": "blocked"}
WITH_MESSAGE = {"error": "blocked", "message": MESSAGE}
EMAIL = "ana@example.com"


def email_of(request):
    return request.cookies.get("em") or getattr(request.state, "user_email", None)


# --- user_email in events and check ---------------------------------------------------------------------------------


async def test_user_email_is_sent_in_the_event_when_given(rec):
    mw = make_mw(build_app(), rec.client(), user_email=email_of)
    async with client_for(mw) as c:
        await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_1", "em": EMAIL})
    await mw.flush()
    assert rec.events[0]["user_email"] == EMAIL


async def test_without_the_option_events_carry_no_user_email(rec):
    mw = make_mw(build_app(), rec.client())
    async with client_for(mw) as c:
        await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_1", "em": EMAIL})
    await mw.flush()
    assert "user_email" not in rec.events[0]


@pytest.mark.parametrize("cookies", [{"uid": "u_42"}, {"uid": "u_42", "em": ""}])
async def test_a_missing_email_is_none_not_an_error(rec, cookies, caplog):
    mw = make_mw(build_app(), rec.client(), user_email=email_of)
    with caplog.at_level(logging.WARNING, logger="veridian"):
        async with client_for(mw) as c:
            assert (await c.get("/items/1", cookies=cookies)).status_code == 200
    await mw.flush()
    assert "user_email" not in rec.events[0] and caplog.records == []


async def test_a_lookup_that_fails_because_nobody_is_logged_in_is_quiet(rec, caplog):
    mw = make_mw(build_app(), rec.client(), user_email=lambda request: request.state.user.email)
    with caplog.at_level(logging.WARNING, logger="veridian"):
        async with client_for(mw) as c:
            assert (await c.get("/items/1", cookies={"uid": "u_42"})).status_code == 200
    await mw.flush()
    assert "user_email" not in rec.events[0] and caplog.records == []


async def test_something_that_is_not_an_address_is_not_sent(rec):
    mw = make_mw(build_app(), rec.client(), user_email=lambda request: "not-an-address")
    async with client_for(mw) as c:
        await c.get("/items/1", cookies={"uid": "u_42"})
    await mw.flush()
    assert "user_email" not in rec.events[0]


async def test_the_email_set_by_the_app_during_the_request_is_used(rec):
    app = build_app()

    @app.post("/signin")
    async def signin(request: Request):
        request.state.user_id, request.state.user_email = "u_9", EMAIL
        return {"ok": True}

    mw = make_mw(app, rec.client(), user_email=email_of)
    async with client_for(mw) as c:
        await c.post("/signin")
    await mw.flush()
    assert rec.events[0]["user_email"] == EMAIL


async def test_the_pre_check_event_carries_the_email(rec):
    mw = make_mw(build_app(), rec.client(), user_email=email_of)
    await mw.refresh_routes()
    await post_bank(mw, {**BANK, "em": EMAIL})
    assert rec.checks[0]["event"]["user_email"] == EMAIL


def test_user_email_must_be_a_function():
    with pytest.raises(VeridianConfigError, match="user_email"):
        VeridianMiddleware(build_app(), api_key="k", user=lambda r: "u", user_email="ana@example.com")


# --- the block message -----------------------------------------------------------------------------------------------


async def blocked_by_list(rec, listed, cookies, key="sessions", value="s_bad"):
    rec.version = 3
    rec.blocklist[key] = [value]
    if listed is not None:
        rec.blocklist["self_unlock_sessions"] = listed
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        return await c.get("/items/1", cookies=cookies)


async def test_a_session_in_self_unlock_sessions_gets_the_email_message(rec):
    r = await blocked_by_list(rec, ["s_bad"], {"uid": "u_42", "sid": "s_bad"})
    assert r.status_code == 403 and r.json() == WITH_MESSAGE


async def test_without_the_signal_the_body_is_the_old_generic_one_exactly(rec):
    r = await blocked_by_list(rec, None, {"uid": "u_42", "sid": "s_bad"})
    assert r.status_code == 403 and r.json() == GENERIC and r.text == '{"error":"blocked"}'


async def test_another_blocked_session_is_generic_even_if_a_different_one_is_listed(rec):
    r = await blocked_by_list(rec, ["s_other"], {"uid": "u_42", "sid": "s_bad"})
    assert r.json() == GENERIC


async def test_an_ip_block_of_a_session_that_is_not_listed_stays_generic(rec):
    r = await blocked_by_list(rec, ["s_bad"], {"uid": "u_42", "sid": "s_ok"}, key="ips", value="198.51.100.1")
    assert r.json() == GENERIC


async def test_the_signal_goes_away_when_the_engine_stops_sending_it(rec):
    rec.version = 3
    rec.blocklist.update(sessions=["s_bad"], self_unlock_sessions=["s_bad"])
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_blocklist()
    rec.version = 4
    del rec.blocklist["self_unlock_sessions"]
    await mw.refresh_blocklist()
    async with client_for(mw) as c:
        r = await c.get("/items/1", cookies={"uid": "u_42", "sid": "s_bad"})
    assert r.json() == GENERIC


async def test_a_bad_self_unlock_sessions_value_is_ignored(rec):
    r = await blocked_by_list(rec, "s_bad", {"uid": "u_42", "sid": "s_bad"})
    assert r.status_code == 403 and r.json() == GENERIC


async def test_a_check_block_with_self_unlock_gets_the_email_message(rec):
    rec.check_body = {"verdict": "block", "decision_id": "d_1", "self_unlock": True}
    calls = []
    mw = make_mw(build_app(calls), rec.client())
    await mw.refresh_routes()
    r = await post_bank(mw)
    assert r.status_code == 403 and r.json() == WITH_MESSAGE and calls == []


@pytest.mark.parametrize("body", [{"verdict": "block", "decision_id": "d_1"},
                                  {"verdict": "block", "decision_id": "d_1", "self_unlock": False},
                                  {"verdict": "block", "decision_id": "d_1", "self_unlock": "yes"}])
async def test_a_check_block_without_a_true_signal_is_generic(rec, body):
    rec.check_body = body
    mw = make_mw(build_app(), rec.client())
    await mw.refresh_routes()
    r = await post_bank(mw)
    assert r.status_code == 403 and r.json() == GENERIC and r.text == '{"error":"blocked"}'
