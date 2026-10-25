# Engine productization (Dev 1 units of DISENO-PRODUCTO.md)

## Objective

Implement the engine units of `docs/DISENO-PRODUCTO.md` ("Plan de trabajo"): U2–U8, U12, U13, U14, U17. They turn the single-client demo engine into a multi-project product: migrations, projects, people, hashed API keys, panel principal with roles, per-project routes, isolation tests, signed webhook, retention, observe mode, admin email.

## Authorization and constraints

- Requested by the user on 2026-10-25, about 6 h before the demo. The parent recommended freezing the engine, and the user chose to proceed.
- **The demo must keep working at every merge.** Render auto-deploys `main`. Each unit is built in an isolated worktree and goes to `main` only when the full suite is green and a live check confirms that the demo SDK (client `demo`) still authenticates and `/v1/check` still blocks.
- `POST /v1/admin/reset` stays until U13, which replaces it by design.
- **Open decisions in DISENO-PRODUCTO.md:** the document's recommendations are taken as the defaults:
  - 3: `viewer` cannot mark normal.
  - 4: the `Mailer` interface uses a fake provider; no real provider is chosen.
  - 5: retention defaults to 90 days, provisionally.
  - 6: new projects start in `observe`; the migrated demo project stays in `enforce`.
  - 8: only `block` notifies.
  - 10: `ai_enabled` per project.
  - 11: keys never expire; `last_used_at` is recorded.
  - 12: audit stores the hash of `user_id`.

## Tasks

| ID | Unit | Route | Status |
|---|---|---|---|
| U2+U3 | Numbered migrations (`schema_migrations`, `0001` = current schema) + `0002` `client_id` → `project_id` | delegated (worktree; mapping + 2+ non-trivial files) | [x] |
| U4+U5 | Control-plane tables and repositories + hashed API keys, constant-time compare, 429 on failures, import of `VERIDIAN_TOKENS` | delegated (worktree) | [x] |
| U6 | Panel principal (service credential + actor + project), role guard per endpoint, contracts 10/11 | delegated (worktree) | [ ] |
| U7+U8 | Per-project routes + isolation test battery | delegated (worktree) | [ ] |
| U12 | Signed webhook (contract 13) | delegated (worktree) | [ ] |
| U13 | Retention and deletion (contract 14); removes reset | delegated (worktree) | [ ] |
| U14 | `observe` mode and `ai_enabled` per project | delegated (worktree) | [x] |
| U17 | Admin email (`Mailer`, `notify_email`) | delegated (worktree) | [ ] |

## Checks

- Each unit: test-first, full engine suite green, and the acceptance tests listed for it in `DISENO-PRODUCTO.md`.
- At each merge: a live check on Render:
  - `/health`;
  - the `demo` token still authenticates (`GET /v1/blocklist` returns 200);
  - an attacker `/v1/check` on the `smoke` client returns `block`.

## Progress

- 2026-10-25: document created. Suite at the start: 169 passed.
- 2026-10-25: U2+U3 merged to `main`. U2 RED: 7 of 9 new tests failed (no runner); GREEN 178 passed, commit `0cadc10`. U3 RED: 5 of 8 new tests failed (no `project_id`); GREEN 186 passed, commit `2cd532d`. A legacy-schema database with demo rows migrates in place (backup `<db>.pre-0002`) and serves `/v1/blocklist`, `/v1/decisions`, `/v1/activity`, `/v1/summary`, `/v1/users` and `/v1/profile` identically to a fresh database with the same rows. The user waived the live demo checks: tests gate merges.
- 2026-10-25: standalone modules merged before wiring, built in parallel worktrees: U4 `control_plane/` (domain, roles, repositories, schema), U5 `keys.py` (hash, constant time, failure limiter, legacy import), U12 `notifications/` (HMAC signing, SSRF guard, outbox, retries; needs the `cryptography` dependency), U17 `mailer.py`. None is wired to the API yet.
- 2026-10-25: U4+U5 wired (W-A, worktree branch). RED: 15 of 21 new tests failed (no `api_key` table, no seeding, no limiter). GREEN: 328 passed, 1 skipped. Commit `521243a`: control-plane schema moves to `migrations/0003_control_plane.sql` (single source, `apply_schema` removed), startup seeds a `project` (enforce) per existing id and per `VERIDIAN_TOKENS` id and imports each token once as a hashed legacy key; `db.connect` enables foreign keys (no FKs on data tables). Next commit (`feat(engine): authenticate with hashed API keys and rate-limit failures`): Bearer resolved through `api_key` with `keys.resolve_key`; identical `401` for unknown/revoked/deleted-project keys; `429 {"error":"rate_limited"}` + `Retry-After` per IP and per key prefix; `last_used_at` at most once a minute per key. Deferred to U6: contract 12 endpoints (need the `panel` principal and `admin` role) and the `sdk`-only guard on panel endpoints (the panel still reads with the same token until U6). `repository.create_api_key` still uses its own key format, not `keys.generate_key`; align it in U6 with contract 12.
- 2026-10-25: U14 (worktree branch). RED: 7 of 12 new tests in `tests/test_project_modes.py` failed (blocklist published and check blocked under `observe`; judge called with `ai_enabled = 0`); the 5 passing ones covered the new `project_settings` helper. GREEN: 358 passed, 1 skipped. Commit `feat(engine): add observe mode and per-project AI switch` (commit `3e0a4e8`). Flags read per request from `project`; no row = `enforce` + AI. `observe` only suppresses enforcement: decisions and activity are unchanged, `check` answers `allow` with the would-have `decision_id`. U6 must expose `enforcement`/`ai_enabled` in `PATCH /v1/projects/{id}` through `project_settings.set_project_flags` (it bumps the version on enforcement changes).
- Next order: W-A wires U4+U5 (migration 0003, projects seeded from existing ids, keys from `VERIDIAN_TOKENS`, auth through `api_key`). Then U6 and U14 in parallel. Then U7+U8, U13, and the wiring of U12 and U17.
