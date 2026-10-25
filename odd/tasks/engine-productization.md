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
| U6 | Panel principal (service credential + actor + project), org layer, role guard per endpoint, contracts 10/12 (11 pending) | delegated (worktree) | [x] |
| U7+U8 | Per-project routes + isolation test battery | delegated (worktree) | [ ] |
| U12 | Signed webhook (contract 13): wiring done; HTTP endpoints pending (with U6/U13) | delegated (worktree) | [ ] |
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
- 2026-10-25: U12 wiring (worktree branch, parallel to U6). `cryptography==50.0.2` added (secret round-trip test no longer skipped). Migration `0005_webhooks.sql`: `webhook_endpoint` (one per project, secret Fernet-encrypted with `VERIDIAN_SECRET_KEY`) and the `notifications` outbox. Choke point: `decisions.create_decision` enqueues for `block` only, same transaction, when the project has an enabled endpoint (covers rule and AI paths, `/v1/events` and `/v1/check`). `notifications/sender.py`: daemon thread started/stopped by the app lifespan (`VERIDIAN_WEBHOOKS=off` disables), reads due rows, sends without a DB connection open, saves outcomes; never raises. `webhook_store.py`: set (SSRF check, secret returned once) / clear / enable / get. RED: the 3 new test modules failed at collection (no `webhook_store`, `outbox`, `sender`). GREEN: 398 passed. `test_control_plane_migration` now asserts `3 in applied` instead of "last is 3". Pending for the endpoint unit: `PUT/DELETE .../webhook` over `set_webhook_endpoint`/`clear_webhook_endpoint`, `GET .../webhook/deliveries` over `outbox.list_notifications`, `POST .../webhook/test` (no queued test event yet). The 0003 `project.webhook_url`/`webhook_secret_enc` columns stay unused.
- 2026-10-25: U6 with the organization layer decided by the user (worktree branch). RED: 15 failed + 27 errors in the control-plane tests rewritten for orgs (no `organization` table, per-project signatures) and `tests/test_panel_api.py` failed to collect (no `DEMO_ORG_ID`, no panel principal). GREEN: 448 passed. Migration `0004_organizations.sql`: `organization` (soft delete), `project.org_id`; membership (strongest role kept), invitation and audit_log rebuilt per org (`audit_log.project_id` nullable for org actions); existing projects and the ones seeded from `VERIDIAN_TOKENS` go to org `Demo` (`org_demo`). Caller kinds: `sdk` (events, check, blocklist, routes) and `panel` (`VERIDIAN_PANEL_TOKEN[S]`, constant time, + `X-Veridian-Actor`, + `X-Veridian-Project` on data reads); data reads and mark-normal are panel-only, as in the fake engine. Endpoints: contract 10 (login with bootstrap owners and pending invitations, `me/orgs`, orgs with `VERIDIAN_ORG_CREATORS`, org projects, project GET/PATCH through `set_project_flags`) and contract 12 (keys in the `keys.generate_key` format, shown once; list; revoke). `mark_normal` needs `admin` (decision 3 is one switch, `roles.VIEWER_CAN_MARK_NORMAL`) and is audited. Not done: contract 11 endpoints (the fake engine has none), `DELETE /v1/projects/{id}`. Engine tests read data through `conftest.as_panel` (fixture change, assertions unchanged). The rehearsal scripts' SDK reads of profile/decisions now print a 403 (scripts outside this unit's surface).
- Next order: W-A wires U4+U5 (migration 0003, projects seeded from existing ids, keys from `VERIDIAN_TOKENS`, auth through `api_key`). Then U6 and U14 in parallel. Then U7+U8, U13, and the wiring of U12 and U17.
