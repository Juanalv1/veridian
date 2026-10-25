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

## AI judge

A deviation is an event with a new network, a new device (non-empty `device_id` not in the profile), or a never-used `critical`/`sensitive` route. The critical rule still blocks synchronously; after the `202`, the judge runs in the background (one call per session per batch) and either replaces the block's rationale or records an `allow`/`alert`/`step_up`/`block` decision (at most one of each kind per session; a blocked session gets no more). A judge failure never breaks ingestion.

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
