# Engine: demo rehearsal reset and history seeding

## Objective
Let the team wipe a client's data between rehearsals and preload the presenter's real history (event wifi IP, weekends included) against the deployed engine, as `docs/DEMO.md` asks.

## Scope
- `POST /v1/admin/reset`: off unless `VERIDIAN_ALLOW_RESET=true` (404 otherwise, before auth). Deletes only the caller's events and decisions (the profile is learned from them), empties its blocklist and bumps `version` so `since=<old>` gets 200, never a stale 304.
- `engine/scripts/reset_client.py` (confirmation required) and `engine/scripts/seed_demo_history.py`, stdlib only, sharing `engine/scripts/engine_client.py` with `send_sample_events.py`.
- Docs: `render.yaml`, `engine/README.md`, `docs/REPARTO.md`.

## Tasks
- [x] T1 Tests first (`tests/test_reset.py`, `tests/test_rehearsal_scripts.py`); RED observed: 6 failed, 3 errors, 83 passed.
- [x] T2 Reset endpoint + `Settings.allow_reset`.
- [x] T3 Shared script helpers, reset and seed scripts.
- [x] T4 Docs.

Route: delegated direct (single bounded writer).

## Checks
- `cd engine && .venv/Scripts/python -m pytest -q`: 92 passed (GREEN).

## Next step
Merge into `main`; on Render set `VERIDIAN_ALLOW_RESET=true` only for the demo engine.
