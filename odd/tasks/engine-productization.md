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
| U4+U5 | Control-plane tables and repositories + hashed API keys, constant-time compare, 429 on failures, import of `VERIDIAN_TOKENS` | delegated (worktree) | [ ] |
| U6 | Panel principal (service credential + actor + project), role guard per endpoint, contracts 10/11 | delegated (worktree) | [ ] |
| U7+U8 | Per-project routes + isolation test battery | delegated (worktree) | [ ] |
| U12 | Signed webhook (contract 13) | delegated (worktree) | [ ] |
| U13 | Retention and deletion (contract 14); removes reset | delegated (worktree) | [ ] |
| U14 | `observe` mode and `ai_enabled` per project | delegated (worktree) | [ ] |
| U17 | Admin email (`Mailer`, `notify_email`) | delegated (worktree) | [ ] |

## Checks

- Each unit: test-first, full engine suite green, and the acceptance tests listed for it in `DISENO-PRODUCTO.md`.
- At each merge: a live check on Render:
  - `/health`;
  - the `demo` token still authenticates (`GET /v1/blocklist` returns 200);
  - an attacker `/v1/check` on the `smoke` client returns `block`.

## Progress

- 2026-10-25: document created. Suite at the start: 169 passed.
- 2026-10-25: U2+U3 done in a worktree (not yet on `main`). U2 RED: 7 of 9 new tests failed (no runner); GREEN 178 passed, commit `0cadc10`. U3 RED: 5 of 8 new tests failed (no `project_id`); GREEN 186 passed, commit `2cd532d`. A legacy-schema database with demo rows migrates in place (backup `<db>.pre-0002`) and serves `/v1/blocklist`, `/v1/decisions`, `/v1/activity`, `/v1/summary`, `/v1/users` and `/v1/profile` identically to a fresh database with the same rows. Live Render check pending until merge.
