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
- [ ] T2 Fake engine: panel principal (service credential + actor + project headers, role guard) and contract 10 (`POST /v1/people/login`, `GET /v1/me/projects`, `POST /v1/projects`, `GET`/`PATCH /v1/projects/{id}`). Route: delegated (writer trigger: fake engine + tests).
- [ ] T3 (U9) Panel: Google login, signed session, CSRF (Origin + custom header, SameSite=lax), logout, no open mode. Route: delegated (same writer as T2).
- [ ] T4 (U10) Panel: project selector and creation, project from session, `TTLCache` keyed by project, retire `VERIDIAN_TOKEN`. Depends on decisions 1–2.
- [ ] T5 (U11) Fake engine contracts 11, 12, 15 + panel members, invitations, API keys (shown once), audit. Depends on decision 3.
- [ ] T6 Panel side of contracts 13 (webhook settings) and 14 (delete user / project data).
- [x] T7 (U18) SDK: tests that `401`/`429` on `/v1/check` and `/v1/blocklist` fail open; SDK README and landing say "create a key in the panel"; fix root README package name (`veridian-sdk`, `import veridian_sdk`). Route: delegated, parallel worktree. Evidence: 61d9a14 (14 cases: 401, 429 with/without `Retry-After` on check, blocklist, routes, events and the background loops), 8b1f914 (docs). No RED: already passing, middleware untouched. Behavior: `Retry-After` is ignored (no backoff), requests proceed; events stay queued until a flush succeeds; background loops stay under 40 calls in 0.2 s with 429 everywhere (mildly timing-based). `uv run --project edge pytest -q edge/tests` 308 passed in the worktree; 76 passed for `test_middleware.py` after cherry-pick to main.
- [ ] T8 (U16) Privacy artifacts in `docs/`: subprocessors with the fields sent to the AI (verified against code), DPA template outline, deletion/retention procedures. Route: delegated, parallel worktree.

## Progress

- 2026-10-25: design in `docs/DISENO-PRODUCTO.md` (commit c13f199, auto-sync). Engine units sent to Dev 1.

## Next step

Run T2+T3 (main checkout) with T7 and T8 in parallel worktrees.
