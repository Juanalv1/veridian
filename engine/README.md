# Veridian engine

Central server: ingests access events, learns a per-user profile and publishes blocks.
API contracts: `docs/REPARTO.md` ("Contratos").

## Setup

```bash
cd engine
py -3.12 -m venv .venv            # python3.12 -m venv .venv on macOS/Linux
.venv/Scripts/python -m pip install -r requirements.txt   # .venv/bin/python on macOS/Linux
```

## Run

```bash
cd engine
VERIDIAN_TOKEN=dev .venv/Scripts/python -m uvicorn veridian_engine.api:create_app --factory --reload
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `VERIDIAN_TOKENS` | | `client_a=tok1,client_b=tok2`: one token per client |
| `VERIDIAN_TOKEN` | | Single token, mapped to client `default` (used when `VERIDIAN_TOKENS` is empty) |
| `VERIDIAN_DB` | `veridian.db` | SQLite file. At startup the engine applies pending migrations from `veridian_engine/migrations/` (recorded in `schema_migrations`), after copying the file to `<file>.pre-NNNN`; there are no down migrations, that copy is the way back. The client name in `VERIDIAN_TOKENS` is stored as `project_id` |
| `VERIDIAN_ROUTES_FILE` | `engine/routes.yaml` | Route classification (`"METHOD /route": class`) |
| `VERIDIAN_LEARN_DELAY_MIN` | `10` | Events younger than this (by `ts`) are not learned yet |
| `VERIDIAN_RATE_FACTOR` | `3` | A session spikes above this many times the user's usual max events per minute. Unmeasured starting point |
| `VERIDIAN_RATE_FLOOR` | `30` | Minimum events per minute for a spike, whatever the history. Unmeasured starting point |
| `ANTHROPIC_API_KEY` | | Enables the AI judge. Without it the engine is rule-only: the critical block, no AI decisions |
| `VERIDIAN_MODEL` | `claude-opus-5-5` | Model used by the AI judge |
| `VERIDIAN_SYNC_MODEL` | `claude-haiku-5-5` | Model for the judge inside `POST /v1/check`, where the request waits |
| `VERIDIAN_SYNC_JUDGE_TIMEOUT_S` | `10` | Seconds `POST /v1/check` waits for the judge on a gray deviation before its fallback decides |
| `VERIDIAN_ALLOW_RESET` | `false` | `true` enables `POST /v1/admin/reset` (demo rehearsals only) |

They can also live in a `.env` file (git-ignored), found by searching upward from the working directory. Real environment variables take precedence.

## AI judge

A deviation is an event with a new network, a new device (non-empty `device_id` not in the profile), a never-used `critical`/`sensitive` route, or a rate spike: more events of the session in the last 60 s than `max(VERIDIAN_RATE_FACTOR × usual max per minute, VERIDIAN_RATE_FLOOR)`, even on the owner's network and device. `new_transition` (route pair never seen in the history) and `unusual_hour` travel in `signals` as context for the judge but never make a deviation on their own. Two rules block synchronously: a never-used critical route from a new network (session and IP), and a rate spike (the session; also the IP only when the network is new, since on the owner's network the IP is the owner's). Then, after the `202`, the judge runs in the background and either replaces the block's rationale or records an `allow`/`alert`/`step_up`/`block` decision. Each session is judged once; it is judged again only on a rule block, a never-used `critical`/`sensitive` route, or its first rate spike, and a blocked session gets no more decisions. An AI block on a known network blocks only the session, never the owner's IP. A judge failure never breaks ingestion. Without a judge, both rules still block.

## Synchronous check

`POST /v1/check` with `{"event": <contract-1 event>}` decides a `critical` request before the app executes it (contract 9) and answers `{"verdict": "allow"|"block", "decision_id": ...}`. The event is stored as if it came through `/v1/events`, so the middleware's later batch resend is ignored by id and never judged twice; re-checking a known id answers from the current blocklist. No deviation → `allow` without AI. A rule block → `block` at once, and the judge rewrites the rationale afterwards. A gray deviation → the judge decides in a worker thread, waiting at most `VERIDIAN_SYNC_JUDGE_TIMEOUT_S`; past it, or on error, the judge's deterministic fallback (`alert`) applies. `block`/`step_up` → `block` (stored as a block: session, plus IP only on a new network); `allow`/`alert` → `allow`. Without a judge, only the rules block. Any internal error answers `allow` (fail-open).

## Activity feed

`GET /v1/activity?limit=50` (1–200) lists the client's latest accesses, newest first, each with a panel verdict (`normal`/`review`/`block`) and its `decision_id`; requests of a blocked session after the block show as `block` (contract 5 in `docs/REPARTO.md`).

## Summary and users

`GET /v1/summary?since=<ISO>&until=<ISO>` gives the panel's headline numbers for `[since, until)` (default: the last 24 h; `since >= until` is a `422`): accesses, users, `reviewed_by_ai` (real judge calls, recorded in `ai_judgments`; `0` without a judge), decisions by type, the current active blocks and a per-hour breakdown (contract 6).

`GET /v1/users` lists every user with events, open blocks first, then by `last_seen`, with state and profile counts (contract 7).

## Tests

```bash
cd engine
.venv/Scripts/python -m pytest -q
```

## Sample events

With the engine running, send a 14-day owner history and an attacker session (stdlib only, no venv needed):

```bash
VERIDIAN_TOKEN=dev py -3.12 engine/scripts/send_sample_events.py --base-url http://localhost:8000
```

It prints the blocklist (attacker session and IP) and the block decision. With `--scenario bot`, the second step is a burst of 60 `GET /invoices/{id}` in 60 s from the owner's network and device: with the AI judge it can block that session only; rule-only, it records nothing.

## Demo rehearsal

With `VERIDIAN_ALLOW_RESET=true` on the engine (otherwise the endpoint answers 404), each rehearsal starts from a clean client. Both scripts read `VERIDIAN_BASE_URL` and `VERIDIAN_TOKEN` from the environment or a `.env`, and never print the token.

1. Reset: `py -3.12 engine/scripts/reset_client.py` deletes this token's client events and decisions (so its profiles) and empties its blocklist, keeping the version moving forward. It asks you to type the base URL, or pass `--yes`.
2. Seed the presenter's history with the event wifi IP measured that morning (see `docs/DEMO.md`), every day including weekends, at the demo hours in UTC:

   ```bash
   py -3.12 engine/scripts/seed_demo_history.py --user-id u_presenter --ip <wifi IP> --hours 13,14,15,16 --device-id <cookie, if known>
   ```

   It prints the accepted count and the resulting profile. Without `--device-id`, the presenter's real device is new to the profile and counts as a deviation.
3. Run the demo.

## Deploy (Render)

`render.yaml` at the repo root declares the `veridian-engine` web service. In Render: **New → Blueprint**, pick this repo, and fill the secrets it asks for:

- `VERIDIAN_TOKENS`: `client=token` for each customer app; the demo app's middleware uses that token as `VERIDIAN_TOKEN`.
- `ANTHROPIC_API_KEY`: enables the AI judge.

The service runs on the `starter` plan with a 1 GB disk at `/var/data`, where the SQLite file lives. Free instances cannot attach a disk, so every deploy would wipe the profiles, and they spin down after 15 minutes idle ([Render docs](https://render.com/docs/disks), [free tier](https://render.com/docs/free)). With a disk the service runs as one instance, and each deploy has a few seconds of downtime.

Seed the demo history against the deployed engine:

```bash
py -3.12 engine/scripts/send_sample_events.py --base-url https://<service>.onrender.com --token <token>
```
