# engine-sync-check

## Objective

Implement contract 9 (`POST /v1/check`): decide `critical` requests before the app executes them, with the AI deciding gray cases in that moment.

## Problem

In a live rehearsal the attacker's `POST /payouts/bank-account` was served (200) and only the next request was blocked: the damage was already done.

## Design

- New module `engine/veridian_engine/check.py` (`check_event`); `api.py` only adds the route and schedules the background rationale.
- Ingest with `insert_events` (idempotent). A known id is not re-evaluated: the answer comes from the current blocklist (open block on the session, or on the IP when it has one).
- `evaluate_event` under the write lock: no deviation → `allow`; rule block → `block` at once, judge rewrites the rationale in the background (`judge_deviations`, unchanged).
- Gray deviation with a judge and `needs_judgment` → `reserve_judgment` (shared with the background path, extracted from `judge_deviations`), then the judge runs outside the lock in a daemon worker thread joined with `VERIDIAN_SYNC_JUDGE_TIMEOUT_S` (default 10). Timeout or error → the judge module's fallback (`alert` unless the critical rule applies). `step_up` is stored as `block`; recorded through `apply_judgment`.
- Any internal error → `allow`, `decision_id: null` (fail-open), logged.

## Checks

- [x] T1 Tests first (`engine/tests/test_check.py`, 18 tests). RED: collection failed without the module; with an empty stub, 18 failed.
- [x] T2 Implement `check.py`, settings field, `reserve_judgment`, route. GREEN: 18 passed.
- [x] T3 Full suite: 165 passed (147 before).
- [x] T4 README section and env var.

## Limitations

- A judge that exceeds the budget keeps running in its thread until the SDK's own timeout; its result is discarded, but the call still counts in `ai_judgments`.
- The fallback rationale says the AI was not available, also when it only was slow.

## Progress

Done. Route: delegated direct (single writer, isolated worktree).
- 2026-10-25: the sync check uses its own judge on `VERIDIAN_SYNC_MODEL` (default `claude-haiku-5-5`): 90 live runs showed the same decisions as Opus, ~2 s instead of up to 11.7 s (Opus crossed the 10 s budget). 2 tests RED, then 167 passed.
