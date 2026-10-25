# Middleware: check critical routes before they run

## Objective

A request on a `critical` route is judged by the engine before the customer's app executes it, so the attacker can no longer change the bank account and only then get blocked.

## Problem

In the rehearsal the middleware only applied the blocklist, so the triggering request on a critical route always passed (blocklist cycle of ~2 s). The engine already implements contract 9 (`POST /v1/check`, deployed).

## Scope (authorized)

- `sdk/python/src/veridian_sdk/middleware.py`: read `GET /v1/routes` (contract 8) at startup and periodically, keep `class: critical`; pre-check with `POST /v1/check` (contract 9); `403 {"error": "blocked"}` on `block`; fail-open on anything else; still batch the event (same id).
- Options `routes_poll_interval` and `check_timeout` (default 12.0 s).
- `edge/fake_engine/app.py`: honor contract 9 (contract 8 already present).
- Version `veridian-sdk` 0.2.0, SDK README (English), `edge/README.md` (Spanish, behavior only).
- Out of scope: pushing, tagging, publishing to PyPI; engine changes.

## Constraints

- Real engine facts (`engine/veridian_engine/check.py`, `events.py`): `Event` requires non-empty `user_id` and `session_id`, a valid IP, `status` 100-599, `bytes` >= 0 (422 otherwise). `insert_events` is `INSERT OR IGNORE`: first write of an id wins, a later event with the same id never updates status. `_check` returns `current_verdict` for an id already seen.
- Code, comments, tests in English; docs in neutral Spanish. Conventional Commits, no AI attribution, work on `main`.

## TDD

Mode: on. Source: `INIT.md`. Runner: `uv run pytest` in `edge/`. Baseline 258 passed. RED observed: 32 failed, 260 passed (292 total) before implementing.

## Decisions

- Pre-check event: `status` 200 and `bytes` 0 (placeholders; the app has not run and the engine rejects status below 100). Same `id` as the batched event.
- Batched event carries the real status (403 when blocked, with the same id). Because the engine keeps the first write, a blocked critical request is stored with status 200 engine-side and the batched 403 is ignored. Gap for Dev 1: the engine could store 403 itself on a `block` verdict so the panel does not show a refused request as served.
- No user or no session (or no IP): check skipped, request passes. The engine would answer 422, which is fail-open anyway.
- Timeout enforced with `asyncio.wait_for` plus httpx `timeout`, so it is a total cap and not per phase.
- Routes are also loaded on lifespan startup; before the first load nothing is checked.

## Tasks

- [x] T1 Fake engine `POST /v1/check` + tests. Route: inline (small, one source file + tests, understood). Evidence: commit `dade68d`; `tests/test_fake_engine.py` 76 passed (6 new failed before).
- [x] T2 Middleware pre-check, options, routes poll, tests, docs, version 0.2.0. Route: inline by the single writer. Evidence: commit `0b5cce5`; `edge` `uv run pytest -q` 292 passed (258 + 34 new); `sdk/python` `uv build` produced sdist and wheel 0.2.0, `dist/` removed.

## Acceptance

- `cd edge && uv run pytest -q` passes.
- Block verdict: 403 and app not called; allow/failure/timeout/garbage: app called; non-critical: no check; same event id in check and batch; routes refresh picks up new critical routes; routes never loaded: no check.
- Not pushed, not tagged, not published.
