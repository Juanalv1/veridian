# Engine AI wiring

## Objective

Connect the AI judge (`veridian_engine/ai/judge.py`) to the engine. Deviations get an AI decision with a Spanish explanation, while the deterministic critical rule keeps blocking immediately.

## Why

Demo steps 2 and 3 (`docs/DEMO.md`):
- the owner on a new network → "raro, pero permitido", with an explanation;
- the attacker → blocked, with the AI's explanation.

Without the wiring, decisions only come from the rule, and their rationale is in English.

## Design

- **Deviation:** an event whose `new_network` or `new_device` is true (`new_device` only when `device_id` is non-empty), or a `route_never_used` on a `critical`/`sensitive` route. Anything else never reaches the AI.
- **Critical rule unchanged:** it creates the `block` synchronously and updates the blocklist at once. Its deterministic rationale moves to neutral Spanish. Afterwards the judge runs in the background and replaces that rationale. The decision stays `block`: the judge's guardrail guarantees it.
- **Other deviations:** the judge runs in the background, after the `202`, and its result becomes a decision with `rationale` from the AI.
  - A `block` from the AI also adds the session and IP to the blocklist and bumps `version`.
  - `allow`, `alert` and `step_up` are recorded only.
- **Dedupe:**
  - at most one AI decision of each kind per session;
  - a blocked session gets no more decisions.
- **Judge injection:**
  - the judge is injected through settings (`judge` callable);
  - by default it is `ai.judge.judge` when `ANTHROPIC_API_KEY` is set, otherwise `None`, which means rule-only and no AI decisions;
  - tests inject a fake judge.
- **Ingestion never waits for the AI,** and a judge failure never breaks ingestion. The judge already never raises; the caller is defensive too.
- **Contract:** shapes stay the same as contract 3. `signals` already includes `new_device`.

## Tasks

| ID | Task | Route | Status |
|---|---|---|---|
| W1 | Deviation detection + background judge call + decision persistence + dedupe; Spanish deterministic rationale; settings injection; tests | delegated (worktree; writer trigger: 2+ non-trivial files; keeps main checkout clean for the sync hooks) | [x] |

## Acceptance criteria

- **Owner on a new network reading invoices:** the fake judge is called once, and one `alert`/`allow` decision appears with the judge's rationale. No block.
- **Attacker:** the `block` exists right after ingest, and the blocklist is updated before the judge returns. The rationale is replaced by the judge's.
- **No judge configured:** behavior identical to the first milestone (the critical block only).
- **Judge raising:** ingestion still returns `202`, and the block keeps its deterministic rationale.
- **No deviation:** the judge is not called.

## Checks

- `cd engine && .venv/Scripts/python -m pytest -q` green. Test-first.

## Progress

- 2026-10-25: document created.
- 2026-10-25: W1 done (commit cf64827). Test-first: 13 new tests RED (missing `judge` setting), then GREEN. Also added contract 3 `status` (`open` | `marked_normal`, from `normal`) after upstream 090b3e7: 2 tests RED, then GREEN. Suite: 79 passed. Notes: one judge call per session per batch (the latest deviation); signal `new_device` is now false for an empty `device_id` (was true in the critical rule); if the judge returns something other than `block` on a rule block, the deterministic rationale stays.
- 2026-10-25: W1 follow-up: a session that already has a decision is not judged again unless the rule blocked or a never-used critical/sensitive route appears (batches every ~2 s were re-calling the model). RED: 3 calls instead of 1; GREEN: 80 passed.
