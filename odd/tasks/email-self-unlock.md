# Self-unlock by email with the customer's brand

## Objective

When the engine blocks a user, that end user gets an email in the customer's brand ("Detectamos un acceso sospechoso", button "Fui yo, desbloquear") and can unlock themselves in one click, without an admin.

## Source

`docs/DESBLOQUEO-POR-CORREO.md` (decided by Juan, 2026-10-25): pieces, link security and minimum tests. Assigned to José on 2026-10-25. Provider: the user (José) chose Resend and supplied an API key (kept out of the repo; configured on Render only).

## Scope (authorized)

Everything in the spec's "Piezas a construir", plus a Resend `Mailer` implementation behind configuration (`NullMailer` stays the default when unconfigured). Out of scope: the spec's "Fuera de alcance" (no "No fui yo", no customer domain, no SMS).

## Constraints

- `GET` of the link never unlocks; only the page's `POST` does (mail scanners open links).
- Token: signed, single use, bound to `decision_id` + `project_id` + `user_id`, only its hash stored, expires at 24 h or when the decision is no longer `open`.
- At most one email per user every 15 min. No full IP in the email or the page. `user_email` is personal data: privacy docs, erasure, retention; never sent to the AI.
- Unlock reuses the `POST /v1/decisions/{id}/normal` logic and is audited as `decision.self_unlocked`.
- Fake engine (`edge/fake_engine`) keeps parity for the parts the panel and SDK use.
- Code, comments, tests in English; team docs and UI copy in neutral Spanish. Conventional Commits, no AI attribution, on `main`.

## TDD

Mode: on. Runners: `uv run pytest -q` in `edge/`; engine suite in `engine/` (its `.venv` lacks pytest: a scratch venv with `requirements.txt` was used before); `node --test` in `sdk/node`.

## Tasks

- [x] T1 Engine: optional `user_email` in events and check (contracts 1 and 9), last one stored per user and project; erasure and retention; `docs/privacidad/DATOS.md`. Route: delegated (writer A, engine).
- [x] T2 Engine: project brand fields + `self_unlock_enabled` with `PATCH`, logo URL validated like the webhook URL. Route: delegated (writer A).
- [x] T3 Engine: `Mailer.send` with `html`; branded template; email queued on each `block` (same outbox as webhook/U17), 15 min limit; token issue/hash/expiry. Route: delegated (writer A).
- [x] T4 Engine: token endpoints (read for the page, unlock `POST`), audit `decision.self_unlocked`. Route: delegated (writer A).
- [x] T5 Engine: `ResendMailer` behind env config; Render configured. Route: delegated (writer A) + inline Render config.
- [x] T6 Panel + fake engine: brand settings screen, public confirmation page (`GET` shows, `POST` unlocks), audit label "Desbloqueado por el usuario". Route: delegated (writer B).
- [x] T7 SDKs (Python and Node): `user_email` option; block message when self-unlock applies. Route: delegated (writer B).

## Acceptance

The spec's "Pruebas mínimas", all green, plus the existing suites.

## Progress

Created 2026-10-25.

- T1 done (commit 7dc68a9, migration 0012 `user_contact`): engine suite 709 baseline -> 720 passed. Invalid `user_email` is dropped, not a 422 (it must not cost the customer the batch). Route: delegated (writer A).
- T2 done (commit a9d221f, migration 0013): suite 720 -> 739 passed. Project JSON gains the 5 brand fields and self_unlock_enabled; PATCH clears a brand field with null or blank. Route: delegated (writer A).
- T3 done (commit 6406f40, migration 0014): suite 739 -> 764 passed. Queued in create_decision (single place for rule and AI blocks, events and check paths); skipped in observe projects; address read from user_contact at send time (notifications.recipient stays NULL); token issued at send time. Blocklist gains optional `self_unlock_sessions`, check gains optional `self_unlock: true`. Route: delegated (writer A).
- T4 done (commit 4e37bf0, no migration): suite 764 -> 781 passed. `GET|POST /v1/self-unlock` (panel credential, token in header X-Veridian-Unlock-Token); GET read-only; POST reuses mark_normal and audits decision.self_unlocked (actor_kind system). Route: delegated (writer A).
- T5 engine side done (commit 0f29850, no migration): suite 781 -> 795 passed. ResendMailer + mailer_from_env, env vars documented in engine/README.md; HTTP mocked. Render env vars (RESEND_API_KEY, VERIDIAN_MAIL_FROM, optionally VERIDIAN_PANEL_URL) remain inline for the lead. Route: delegated (writer A).
- T6 done (commits cc65860, 1c9ec19, 5efdad3): `edge` suite 824 baseline -> 909 passed (with T7). Fake engine: brand fields, `PATCH` with the engine's validation (https, `#RRGGBB`, 80 chars; unsafe logo = `422 unsafe_url`, simplified without DNS: reuses the webhook check), `user_email` per user, `self_unlock_sessions` and `self_unlock` signals, in-memory outbox (`app.state.outbox`, one mail per user per 15 min) and `GET|POST /v1/self-unlock` with the engine's states and errors. The fake audits `project.update` only for brand and self-unlock changes (older changes keep a quiet audit log). Panel: `GET|POST /marca` (viewer reads, admin+ edits; nav item for every known role), public `GET|POST /desbloquear/{token}` (no session; POST guarded by a per-token form nonce plus Origin check, `Origin: null` accepted because `no-referrer` makes browsers send it; noindex, no-store, no-referrer; access log filter hides the token), audit label "Desbloqueado por el usuario". Existing nav and public-route tests were updated for the new items. Route: delegated (writer B).
- T7 done (commits 236a8d1, 78f0d26): `node --test` 134 baseline -> 148 passed; Python SDK tests in `edge/tests/test_middleware_self_unlock.py`. `user_email`/`userEmail` optional callable (None/null, TypeError-style lookups and non-addresses mean no email, key absent); 403 body adds `message` only with the engine signal, otherwise exactly `{"error":"blocked"}`. Versions: npm 0.1.0 -> 0.2.0; Python was 0.3.0 so it went to 0.4.0 (0.2.0 would be a downgrade). Not published. Route: delegated (writer B).
