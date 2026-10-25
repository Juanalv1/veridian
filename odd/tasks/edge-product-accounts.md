# Edge: product accounts (login, projects, members, keys)

## Objective

The panel stops being a single-tenant demo: people sign in with Google, belong to one or more projects with a role, and manage members, invitations and API keys. The SDK and docs follow the per-project API keys.

## Problem

The panel has no login and reads the engine with one env token (`VERIDIAN_TOKEN`): anyone with the URL sees every decision and can lift blocks. Tokens are a static env var with no rotation. See the audit gaps 1–8 and the design in `docs/DISENO-PRODUCTO.md`.

## Why

The team chose to be product-oriented, not feature-oriented: one person may own several projects, and the panel must resolve the project from the session.

## Scope (authorized)

Dev 2 units from `docs/DISENO-PRODUCTO.md` (plan de trabajo): U1, U9, U10, U11, panel side of U12/U13, U16, U18. `fake_engine/` honors contracts 10–15 so the panel can be built before the real engine (Dev 1: U2–U8, U12–U14, U17).

Out of scope: engine changes (Dev 1), organization layer above projects (open decision 1), billing.

## Constraints

- Code, comments and tests in English; team docs (`docs/`) in neutral Spanish. Conventional Commits, no AI attribution, work on `main`.
- Login copied from Centinela (`centinela_svc/web/auth.py`): hand-rolled OAuth with httpx, `state` in the session, `email_verified` required, injectable Google transport for tests.
- Panel → engine: service credential + `X-Veridian-Actor` + `X-Veridian-Project` (design, "Cómo se autentica el panel contra el motor").
- No unsourced numbers. The repo auto-sync may commit uncommitted work: commit each task promptly.

## Open decisions affecting this work

From `docs/DISENO-PRODUCTO.md` "Decisiones abiertas": 1 (org layer), 2 (who creates projects), 3 (viewer marks normal), 4 (email). Pending user answers; tasks that depend on them wait.

## TDD

Mode: on. Source: `INIT.md` ("test first when there is clear behavior"). Runner: `uv run pytest -q` in `edge/`.

## Tasks

- [ ] T1 (U1) Remove `VERIDIAN_ALLOW_RESET` from `render.yaml`. Blocked: rehearsals use `engine/scripts/reset_client.py` until U13 (Dev 1) ships `DELETE /v1/projects/{id}/data`; waiting for user decision.
- [x] T2 Fake engine: panel principal (service credential + actor + project headers, role guard) and contract 10 (`POST /v1/people/login`, `GET /v1/me/projects`, `POST /v1/projects`, `GET`/`PATCH /v1/projects/{id}`). Route: delegated (writer trigger: fake engine + tests). Done: commits 9f5a9d4, 8f1c424 (tests, auto-committed during the work), 1cce043 (implementation). RED: 3 failed, 110 errors (fixtures) before the code; GREEN: 209 passed (fake engine, middleware, demo). Provisional / not covered: project creation behind `VERIDIAN_PROJECT_CREATORS` (empty = nobody) pending decision 2; new projects start in `observe` and seeded ones in `enforce` (decision 6 pending), but `enforcement`, `ai_enabled` and `retention_days` are only stored, not applied; `mark normal` needs admin (decision 3 pending); no AuditLog, no `DELETE /v1/projects/{id}`, invitations have no endpoints yet (T5; the test helper uses `Project.invite`). Gap: the design table lists `GET /v1/blocklist` and `/v1/routes` as sdk-only, but the panel reads both (block effect, Rutas page), so the fake engine lets the panel read them as viewer; Dev 1 to confirm.
- [x] T3 (U9) Panel: Google login, signed session, CSRF (Origin + custom header, SameSite=lax), logout, no open mode. Route: delegated (same writer as T2). Done: commit 12c0e3f. RED: collection error (`panel.config` missing) with the new tests in place; GREEN: `uv run pytest -q` in edge/: 402 passed. CSRF = same-site Origin (PUBLIC_URL when set) plus the session token in `X-CSRF-Token` (htmx, via `htmx:configRequest`) or a hidden `csrf` field (no-JS form), as in the design. No open mode = the panel answers 503 on every page when Google, session or panel credential settings are missing. Persons without projects are redirected to `/sin-proyectos`; data reads use the first project of the login (T4 adds the selector). `ASSET_VERSION` is now 22. UNVERIFIED: the real Google round trip (only a fake transport was exercised), the real cookie `Secure` flag behind Render, and the panel against the real engine (Dev 1) which must implement the same headers and `POST /v1/people/login`. Panel rate limit on `/auth/*` (design, Seguridad) is not done.
- [ ] T4 (U10) Panel: project selector and creation, project from session, `TTLCache` keyed by project, retire `VERIDIAN_TOKEN`. Depends on decisions 1–2.
- [ ] T5 (U11) Fake engine contracts 11, 12, 15 + panel members, invitations, API keys (shown once), audit. Depends on decision 3.
- [ ] T6 Panel side of contracts 13 (webhook settings) and 14 (delete user / project data).
- [x] T7 (U18) SDK: tests that `401`/`429` on `/v1/check` and `/v1/blocklist` fail open; SDK README and landing say "create a key in the panel"; fix root README package name (`veridian-sdk`, `import veridian_sdk`). Route: delegated, parallel worktree. Evidence: 61d9a14 (14 cases: 401, 429 with/without `Retry-After` on check, blocklist, routes, events and the background loops), 8b1f914 (docs). No RED: already passing, middleware untouched. Behavior: `Retry-After` is ignored (no backoff), requests proceed; events stay queued until a flush succeeds; background loops stay under 40 calls in 0.2 s with 429 everywhere (mildly timing-based). `uv run --project edge pytest -q edge/tests` 308 passed in the worktree; 76 passed for `test_middleware.py` after cherry-pick to main.
- [x] T8 (U16) Privacy artifacts in `docs/`: subprocessors with the fields sent to the AI (verified against code), DPA template outline, deletion/retention procedures. Route: delegated, parallel worktree.
  - [x] Evidence: commit with `docs(privacy): add subprocessors, data inventory, DPA outline and procedures` (cherry-picked from 67d6c7f). Fields sent to the AI verified: `{"signals", "profile"}` (`ai/judge.py:123-133`), `profile.user_id` in clear, networks as /24 or /64, device cookie values; not sent: session_id, full IP, user_agent. Unverified: Anthropic location/contract, fallback model location, panel/demo hosting (not in `render.yaml`), Render disk backups. Readback: structural, citation `judge.py:123-133` checked.

## Progress

- 2026-10-25: design in `docs/DISENO-PRODUCTO.md` (commit c13f199, auto-sync). Engine units sent to Dev 1.

## Next step

T4 (project selector, creation, retire `VERIDIAN_TOKEN`); needs decisions 1-2.
