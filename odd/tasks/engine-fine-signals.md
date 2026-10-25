# Engine fine signals: rhythm and path

## Objective

Detect someone using the owner's credential from the owner's own network and device: a script, malware or a stolen session cookie. Today the engine only sees "new network / new device / never-used route", so that case is invisible. `docs/DECISIONES.md` promises it: "El dispositivo y el ritmo delatan scripts y malware aunque usen la red del dueño."

## Design

All signals are deterministic code. The AI only judges them.

- **Rhythm (deviation trigger):**
  - `rate_per_min`: events of the same session in the 60 s up to and including the event.
  - `usual_max_rate_per_min`: the highest per-minute count of any session in the user's learned history (the same learning rules as the profile: older than the learn delay, never from a block/alert decision unless marked normal).
  - `rate_spike`: `rate_per_min > max(VERIDIAN_RATE_FACTOR × usual_max_rate_per_min, VERIDIAN_RATE_FLOOR)`, with defaults factor 3 and floor 30. These defaults are a starting point, not measured; tune them in rehearsals.
  - A `rate_spike` is a deviation even on a known network and device.
- **Path and hour (context only, never a trigger by themselves):**
  - `new_transition`: the pair (previous route in the session → this route), both as `"METHOD /route"`, never seen in the learned history.
  - `unusual_hour`: the event's UTC hour is not in the profile's `hours`. False when the profile has no hours.

  They go into `signals` so the judge can weigh them. Triggering on them alone would call the AI on every exploration by the owner.
- **Re-judging:** a session already judged is judged again on a rate spike, but only once: the first time its `rate_spike` signal appears in that session. Existing rules stay: always on a rule block, and on a never-used critical/sensitive route.
- **Block scope:** when the AI blocks a deviation whose network is known (`new_network` false), block only the `session_id`, never the `ip`. The owner shares that IP; blocking it would lock the victim out. When the network is new, keep blocking the session and the IP.
- **Profile (contract 4, additive):** add `usual_max_rate_per_min` (int, 0 when there is no history).
- **Contract 3, additive:** the new keys in `signals` (`rate_per_min`, `usual_max_rate_per_min`, `rate_spike`, `new_transition`, `unusual_hour`). Document both in `docs/REPARTO.md` in the same commit.
- **Demo scenario:** `send_sample_events.py --scenario bot` sends owner history, then a burst of `GET /invoices/{id}` from the owner's network and device (well over the floor within 60 s). Default scenario unchanged.

## Tasks

| ID | Task | Route | Status |
|---|---|---|---|
| F1 | Rhythm + path/hour signals, profile field, rate-spike deviation, re-judging, block scope, settings, tests, contract notes, bot scenario, README | delegated (worktree; writer trigger: 2+ non-trivial files) | [x] |

## Acceptance criteria

- **Burst from the owner's network and device** (above the floor and above 3× the usual): one deviation with `rate_spike` true, and the judge is called. An AI `block` blocks the session only, so `ips` stays empty.
- **Owner at a normal pace:** no `rate_spike`, and the judge is not called.
- **High usual rate:** a user whose history already shows 40/min does not spike at 50/min.
- **A spiking session judged earlier** (e.g. `alert` for a new network) is judged again once on its first spike, and not on later batches.
- `new_transition` and `unusual_hour` appear in `signals` but never trigger the judge alone.
- **New network:** an AI `block` still blocks the session and the IP.
- **Profile endpoint:** includes `usual_max_rate_per_min`.
- All existing tests stay green.

## Checks

- `cd engine && .venv/Scripts/python -m pytest -q` green, test-first.

## Progress

- 2026-10-25: document created.
- 2026-10-25: F1 done. RED: 9 of 13 new tests in `engine/tests/test_fine_signals.py` failed before the change (the 4 that passed are negative cases). GREEN: full suite 107 passed (93 before). The "first spike once" marker lives in a new table `rate_spike_judgments`; a session-only block stores an empty `ip` in its decision and the blocklist skips it. Live `send_sample_events.py --scenario bot` without `ANTHROPIC_API_KEY`: 84 + 60 accepted, no decision and empty blocklist, as expected rule-only.
