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
