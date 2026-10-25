# engine-ai-judge

**Objective:** module that, given deviation signals (contract 3) and the profile (contract 4), asks Claude for `decision` + `rationale` (neutral Spanish). Code computes the profile; the AI only judges.

**Interface:** `veridian_engine.ai.judge.judge(signals, profile, *, client=None, model=None) -> Judgment(decision, rationale)`. Model from `VERIDIAN_MODEL` (default `claude-opus-5-5`), key from `ANTHROPIC_API_KEY`. Never raises.

**Constraints:** critical route + new network + route never used is never weaker than `block` (code guardrail). API error, timeout, refusal or invalid output → deterministic fallback (`block` under that rule, otherwise `alert`). Untrusted signals go in a delimited data block that cannot be closed from inside.

## Tasks

- [x] T1 tests with fake client (valid parse, guardrail, API error, invalid output, refusal, data block) — RED observed (module missing)
- [x] T2 implement `judge.py` — GREEN, 16 passed
- [ ] T3 live smoke call — skipped, `ANTHROPIC_API_KEY` not set
- [ ] T4 integration in the engine API (other writer)

## Checks

`cd engine && .venv/Scripts/python -m pytest tests/ai -q`

**Route:** delegated direct (bounded writer, isolated worktree).
