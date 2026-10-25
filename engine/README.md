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
| `VERIDIAN_DB` | `veridian.db` | SQLite file |
| `VERIDIAN_ROUTES_FILE` | `engine/routes.yaml` | Route classification (`"METHOD /route": class`) |
| `VERIDIAN_LEARN_DELAY_MIN` | `10` | Events younger than this (by `ts`) are not learned yet |
| `ANTHROPIC_API_KEY` | | Enables the AI judge. Without it the engine is rule-only: the critical block, no AI decisions |
| `VERIDIAN_MODEL` | `claude-opus-5-5` | Model used by the AI judge |

They can also live in a `.env` file (git-ignored), found by searching upward from the working directory. Real environment variables take precedence.

## AI judge

A deviation is an event with a new network, a new device (non-empty `device_id` not in the profile), or a never-used `critical`/`sensitive` route. The critical rule still blocks synchronously; after the `202`, the judge runs in the background and either replaces the block's rationale or records an `allow`/`alert`/`step_up`/`block` decision. Each session is judged once; it is judged again only on a rule block or a never-used `critical`/`sensitive` route, and a blocked session gets no more decisions. A judge failure never breaks ingestion.

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

It prints the blocklist (attacker session and IP) and the block decision.

## Deploy (Render)

`render.yaml` at the repo root declares the `veridian-engine` web service. In Render: **New → Blueprint**, pick this repo, and fill the secrets it asks for:

- `VERIDIAN_TOKENS`: `client=token` for each customer app; the demo app's middleware uses that token as `VERIDIAN_TOKEN`.
- `ANTHROPIC_API_KEY`: enables the AI judge.

The service runs on the `starter` plan with a 1 GB disk at `/var/data`, where the SQLite file lives. Free instances cannot attach a disk, so every deploy would wipe the profiles, and they spin down after 15 minutes idle ([Render docs](https://render.com/docs/disks), [free tier](https://render.com/docs/free)). With a disk the service runs as one instance, and each deploy has a few seconds of downtime.

Seed the demo history against the deployed engine:

```bash
py -3.12 engine/scripts/send_sample_events.py --base-url https://<service>.onrender.com --token <token>
```
