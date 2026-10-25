# Engine first milestone

## Objective

Minimal end-to-end engine without AI: ingest events, learn a per-user profile, block an attacker who hits a never-used `critical` route from a new network, and publish the result through `/v1/blocklist` and `/v1/decisions`.

## Why

This is the Dev 1 milestone in `docs/REPARTO.md`. It makes the demo work before any AI is involved.

## Scope

- `engine/` only, plus root `.gitignore` entries.
- Contracts: `docs/REPARTO.md`, closed on 2026-10-25. Do not change them.

## Design

- **Stack:** Python 3.12, FastAPI, stdlib `sqlite3`, PyYAML, pytest and httpx. Venv at `engine/.venv`.
- **App factory:** `create_app(settings)`. Tests inject the DB path, tokens, the routes file, the learn delay and a clock.
- **Tokens:** `VERIDIAN_TOKENS="client_a=tok1,client_b=tok2"`. Fallback: `VERIDIAN_TOKEN`, mapped to client `default`. Every table carries `client_id`.
- **Learning:** the profile is built only from events older than `VERIDIAN_LEARN_DELAY_MIN` (default 10, measured from `ts`) that are not part of a `block`/`alert` decision. The exception is a decision marked normal: its events are learned immediately.
- **Networks:** `/24` for IPv4, `/64` for IPv6.
- **Rule (on ingest, per new event):**
  - when to block: the route class is `critical`, the route is not in the profile and the network is not in the profile;
  - what to block: the event's `session_id` and `ip`, and the blocklist `version` goes up;
  - only one block decision per session.
- **Route classes:** from `engine/routes.yaml`. Anything not listed is `normal`.
- **`path` signal:** ordered routes of the session, up to and including the event.
- **Known limitation:** a user with no history who hits a critical route gets blocked. The demo seeds history.

## Tasks

| ID | Task | Route | Status |
|---|---|---|---|
| T1 | Scaffold `engine/`; `POST /v1/events` (auth 401, validation 422, idempotent by `id`, `202 {"accepted": n}` counting new events only) | delegated (writer trigger: 2+ non-trivial files) | [x] |
| T2 | `routes.yaml` classification; profile learning; `GET /v1/users/{user_id}/profile` | delegated | [x] |
| T3 | Block rule; `GET /v1/decisions`; `GET /v1/blocklist` (304, full list); `POST /v1/decisions/{id}/normal` | delegated | [x] |
| T4 | `engine/scripts/send_sample_events.py` (seeded history + attacker scenario); `engine/README.md` with run instructions | delegated | [x] |

## Acceptance criteria

- Each contract endpoint behaves as `docs/REPARTO.md` says.
- Scenario test: seeded owner history → owner uses invoices (no decision) → attacker from a new network does `POST /payouts/bank-account` → one `block` decision, and the blocklist contains the attacker's session and IP.
- Marking that decision normal removes the block and bumps `version`.

## Checks

- `cd engine && .venv/Scripts/python -m pytest -q` is green after each task.
- Test-first per task (RED observed before GREEN).

## Delivery

- One Conventional Commit per task, directly on `main` (project convention, no PRs). No AI attribution.
- Native review is off globally, so the RDD review lifecycle does not apply.
- Forecast: about 700 authored lines, tests included.

## Progress

- 2026-10-25: document created; T1-T4 delegated to one writer.
- 2026-10-25: T1 `6bd22af`, T2 `06d2dcb` (most T2 files landed earlier in auto-sync commit `8cc9986`), T3 `9692138`, T4 in the commit that checks this line. Suite: 31 passed. Smoke: live uvicorn + `send_sample_events.py` produced one `block` (attacker session + IP), `since=1` -> 304.
- Requirement change during T2: `routes.yaml` keys are `"METHOD /route"`; profile `routes` entries carry the method (`"GET /invoices"`), refining contract 4's example; REPARTO.md update pending (parent).
