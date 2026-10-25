# Veridian engine

Central server: ingests access events, learns a per-user profile and publishes blocks.
API contracts: `docs/REPARTO.md` ("Contratos").

## Setup

```bash
cd engine
py -3.12 -m venv .venv            # python3.12 -m venv .venv on macOS/Linux
.venv/Scripts/python -m pip install -r requirements.txt   # .venv/bin/python on macOS/Linux
```

## Run

```bash
cd engine
VERIDIAN_TOKEN=dev .venv/Scripts/python -m uvicorn veridian_engine.api:create_app --factory --reload
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `VERIDIAN_TOKENS` | | `client_a=tok1,client_b=tok2`: one token per client. At startup each entry becomes a project (if missing) and a hashed API key (prefix `legacy`), once; requests are then authenticated against the `api_key` table, not this variable. Removing a token from here does **not** revoke its imported key |
| `VERIDIAN_TOKEN` | | Single token, mapped to client `default` (used when `VERIDIAN_TOKENS` is empty) |
| `VERIDIAN_AUTH_MAX_FAILURES_PER_IP` | `30` | Failed authentications per source IP within the window before `429` with `Retry-After`. Unmeasured starting point |
| `VERIDIAN_AUTH_MAX_FAILURES_PER_PREFIX` | `10` | Same, per visible prefix of a `vrd_<prefix>_...` key. Legacy tokens have no prefix and are limited by IP only |
| `VERIDIAN_AUTH_FAILURE_WINDOW_S` | `60` | Sliding window of the failure limits, in seconds |
| `VERIDIAN_DB` | `veridian.db` | SQLite file. At startup the engine applies pending migrations from `veridian_engine/migrations/` (recorded in `schema_migrations`), after copying the file to `<file>.pre-NNNN`; there are no down migrations, that copy is the way back. The client name in `VERIDIAN_TOKENS` is stored as `project_id` |
| `VERIDIAN_ROUTES_FILE` | `engine/routes.yaml` | Demo route classification (`"METHOD /route": class`), seeded only into projects that existed without rules at startup; new projects start empty and are edited from the panel |
| `VERIDIAN_LEARN_DELAY_MIN` | `10` | Events younger than this (by `ts`) are not learned yet |
| `VERIDIAN_RATE_FACTOR` | `3` | A session spikes above this many times the user's usual max events per minute. Unmeasured starting point |
| `VERIDIAN_RATE_FLOOR` | `30` | Minimum events per minute for a spike, whatever the history. Unmeasured starting point |
| `ANTHROPIC_API_KEY` | | Enables the AI judge. Without it the engine is rule-only: the critical block, no AI decisions |
| `VERIDIAN_MODEL` | `claude-opus-5-5` | Model used by the AI judge |
| `VERIDIAN_TRUSTED_PROXIES` | `0` | Proxies that append to `X-Forwarded-For`; the auth failure limit counts per real client IP (the entry that many from the right). `0` uses the connection address |
| `VERIDIAN_SYNC_MODEL` | `claude-haiku-5-5` | Model for the judge inside `POST /v1/check`, where the request waits |
| `VERIDIAN_SYNC_JUDGE_TIMEOUT_S` | `10` | Seconds `POST /v1/check` waits for the judge on a gray deviation before its fallback decides |
| `VERIDIAN_SECRET_KEY` | empty | Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) that encrypts webhook signing secrets at rest. Empty: webhooks cannot be configured (clear error); the engine still runs. Losing or changing it makes stored secrets unreadable: their deliveries fail until the endpoint is set again. Never log it |
| `VERIDIAN_WEBHOOKS` | `on` | `off` stops the background webhook sender (block notifications stay queued in `notifications`) |
| `VERIDIAN_PANEL_URL` | empty | Panel base URL, used for `panel_url` in the webhook payload and for the self-unlock link (`<url>/desbloquear/<token>`; empty: `https://veridian-panel.onrender.com`) |
| `RESEND_API_KEY` | empty | API key of [Resend](https://resend.com), the email provider. With `VERIDIAN_MAIL_FROM` it turns on real email (admin block alerts and the self-unlock email). Never log it or commit it; set it only in the host's secrets |
| `VERIDIAN_MAIL_FROM` | empty | Sender address (for example `seguridad@mail.example.com`), on a domain verified in Resend. The visible name is the project's `brand_name` (the self-unlock email) or `Veridian` (admin alerts). Without both variables the engine keeps the `NullMailer` and emails are marked `failed` ("no email provider configured") |
| `VERIDIAN_PANEL_TOKEN` | empty | Service credential of the panel (Bearer, compared in constant time). Empty: no panel can call the engine. The most sensitive secret: whoever holds it can act as any member whose `person_id` they know. Must differ from every project key |
| `VERIDIAN_PANEL_TOKENS` | empty | Extra panel credentials, comma-separated, accepted together with `VERIDIAN_PANEL_TOKEN` (current and previous, to rotate without a cut) |
| `VERIDIAN_BOOTSTRAP_OWNERS` | empty | Comma-separated emails. On login, such a person becomes owner of every organization that has no owner (for example org `Demo`, created for the legacy projects). An organization that already has an owner is never touched |
| `VERIDIAN_MAX_ORGS_PER_PERSON` | `3` | Open signup: any signed-in person creates organizations (`POST /v1/orgs`), up to this many live ones they created; past it, `409 org_limit_reached`. A design default against abuse, not measured |
| `VERIDIAN_ORG_CREATORS` | empty | Comma-separated emails with no organization limit (the team). Projects are created by the owners and admins of an organization |
| `VERIDIAN_INVITATION_DAYS` | `14` | Days an invitation stays valid (design default, not measured) |
| `VERIDIAN_RETENTION` | `on` | `off` stops the retention worker (purge by each project's `retention_days`, wipe of deleted projects) |
| `VERIDIAN_RETENTION_INTERVAL_S` | `3600` | Seconds between retention passes; the first pass runs one interval after start |

## Callers, organizations and roles

Two kinds of caller (docs/DISENO-PRODUCTO.md, "Revisión 2026-10-25"):

- `sdk`: a project's API key. Only `POST /v1/events`, `POST /v1/check`, `GET /v1/blocklist` and `GET /v1/routes`, of the key's project. Any other endpoint answers `403 {"error": "forbidden"}`.
- `panel`: `Authorization: Bearer <VERIDIAN_PANEL_TOKEN>` plus `X-Veridian-Actor: <person_id>` and, on data reads (decisions, activity, summary, users, profile, and also blocklist and routes), `X-Veridian-Project: <project_id>`. Missing project header: `400 {"error": "project_required"}`. Unknown actor, non-member, unknown or deleted project: `403`. The panel credential on `/v1/events` or `/v1/check`: `403`.

Roles (`owner`, `admin`, `viewer`) live only in the organization: one role per person and organization, valid for every project of it; a person may belong to several organizations. The matrix is `control_plane/roles.py`; whether a viewer may mark a decision normal (open decision 3) is the single switch `VIEWER_CAN_MARK_NORMAL` there (default: no). Migration `0004` adds `organization` and `project.org_id`, and moves memberships (keeping the strongest role), invitations and audit to the organization; existing projects, and the projects seeded at startup from `VERIDIAN_TOKENS` or from existing data, belong to org `Demo` (`org_demo`). `POST /v1/decisions/{id}/normal` needs `admin` or `owner` and writes `decision.mark_normal` to `audit_log` in the same transaction. Endpoints: contracts 10 and 12 in `docs/REPARTO.md`.

They can also live in a `.env` file (git-ignored), found by searching upward from the working directory. Real environment variables take precedence.

## AI judge

A deviation is an event with a new network, a new device (non-empty `device_id` not in the profile), a never-used `critical`/`sensitive` route, or a rate spike: more events of the session in the last 60 s than `max(VERIDIAN_RATE_FACTOR × usual max per minute, VERIDIAN_RATE_FLOOR)`, even on the owner's network and device. `new_transition` (route pair never seen in the history) and `unusual_hour` travel in `signals` as context for the judge but never make a deviation on their own. Two rules block synchronously: a never-used critical route from a new network (session and IP), and a rate spike (the session; also the IP only when the network is new, since on the owner's network the IP is the owner's). Then, after the `202`, the judge runs in the background and either replaces the block's rationale or records an `allow`/`alert`/`step_up`/`block` decision. Each session is judged once; it is judged again only on a rule block, a never-used `critical`/`sensitive` route, or its first rate spike, and a blocked session gets no more decisions. An AI block on a known network blocks only the session, never the owner's IP. A judge failure never breaks ingestion. Without a judge, both rules still block.

## Synchronous check

`POST /v1/check` with `{"event": <contract-1 event>}` decides a `critical` request before the app executes it (contract 9) and answers `{"verdict": "allow"|"block", "decision_id": ...}`. The event is stored as if it came through `/v1/events`, so the middleware's later batch resend is ignored by id and never judged twice; re-checking a known id answers from the current blocklist. No deviation → `allow` without AI. A rule block → `block` at once, and the judge rewrites the rationale afterwards. A gray deviation → the judge decides in a worker thread, waiting at most `VERIDIAN_SYNC_JUDGE_TIMEOUT_S`; past it, or on error, the judge's deterministic fallback (`alert`) applies. `block`/`step_up` → `block` (stored as a block: session, plus IP only on a new network); `allow`/`alert` → `allow`. Without a judge, only the rules block. Any internal error answers `allow` (fail-open).

## Observe mode and per-project AI

Each `project` row carries `enforcement` (`observe` | `enforce`) and `ai_enabled` (decisions 6 and 10 of `docs/DISENO-PRODUCTO.md`); a project without a row behaves as `enforce` with AI. They are read on every request and changed with `PATCH /v1/projects/{id}` (admin), which goes through `project_settings.set_project_flags`.

- `observe`: detection, decisions, activity and the panel stay exactly as in `enforce`; only enforcement is suppressed. `GET /v1/blocklist` returns empty `users`/`sessions`/`ips` with the current version, and `POST /v1/check` answers `allow` with the `decision_id` of the decision it would have enforced (the stored status is not rewritten to 403). Activity still shows accesses after a recorded block as `block`: it reflects decisions, not enforcement. Changing `enforcement` bumps the blocklist version, so middlewares polling with `since` refresh.
- `ai_enabled = 0`: rule-only for that project, as without `ANTHROPIC_API_KEY`: neither the background nor the synchronous judge is called (no `ai_judgments` rows); rule blocks still apply, subject to enforcement.

## Webhooks

When a `block` decision is created (rule or AI, `/v1/events` or `/v1/check`), `create_decision` inserts a row in the `notifications` outbox in the same transaction, if the project has an enabled endpoint in `webhook_endpoint`. Only `block` notifies (decision 8), and `observe` projects are notified too. A background thread (`notifications/sender.py`, started and stopped with the app, polling every 5 s) sends due rows as a signed `POST` (`X-Veridian-Signature: t=<unix>,v1=HMAC-SHA256(secret, "<t>.<raw body>")`, `X-Veridian-Event`, `X-Veridian-Delivery`); the payload is the one in `docs/DISENO-PRODUCTO.md` and never carries the IP. Only a `2xx` counts as delivered; anything else retries after 1 min, 5 min, 30 min, 2 h and 6 h (unmeasured starting point), then `failed`. The URL must be `https` to a public address, checked again on every attempt, and redirects are not followed. A send failure never affects ingestion or `/v1/check`; delivery is at least once (deduplicate by `X-Veridian-Delivery`).

The endpoint is set with `webhook_store.set_webhook_endpoint` (validates the URL, returns the signing secret once, stores it encrypted with `VERIDIAN_SECRET_KEY`) and removed with `clear_webhook_endpoint`. No HTTP endpoint yet (contract 13 is pending).

## Self-unlock by email

Spec: `docs/DESBLOQUEO-POR-CORREO.md`; API shapes: `docs/REPARTO.md` (T3 and T4 sections). When a project turns on `self_unlock_enabled` (`PATCH /v1/projects/{id}`, with `brand_name`, `brand_logo_url`, `brand_color`, `brand_support_url`, `brand_support_email`) and the middleware sends `user_email` with its events or checks, each `block` of an enforcing project queues a branded email to that user (`notifications.purpose = 'self_unlock'`, sent by the same worker as the admin alerts, so `VERIDIAN_WEBHOOKS=off` also stops it). At most one email per user every 15 minutes. The email links to the panel's public page; the page calls `GET|POST /v1/self-unlock` with the panel credential and the token in `X-Veridian-Unlock-Token`. The token is random, single use, bound to its decision, expires after 24 h and only its SHA-256 is stored. `POST` marks the decision normal (same logic as `POST /v1/decisions/{id}/normal`) and audits `decision.self_unlocked`. `GET /v1/blocklist` (`self_unlock_sessions`) and `POST /v1/check` (`self_unlock: true`) tell the middleware when to show the "we sent you an email" message. `user_email` is personal data: only the latest per user is kept, it never reaches the AI judge, and per-user erasure and retention delete it (`docs/privacidad/DATOS.md`).

Real email needs `RESEND_API_KEY` and `VERIDIAN_MAIL_FROM` (see the table above); `ResendMailer` is `veridian_engine/resend_mailer.py`.

## Activity feed

`GET /v1/activity?limit=50` (1–200) lists the client's latest accesses, newest first, each with a panel verdict (`normal`/`review`/`block`) and its `decision_id`; requests of a blocked session after the block show as `block` (contract 5 in `docs/REPARTO.md`).

## Summary and users

`GET /v1/summary?since=<ISO>&until=<ISO>` gives the panel's headline numbers for `[since, until)` (default: the last 24 h; `since >= until` is a `422`): accesses, users, `reviewed_by_ai` (real judge calls, recorded in `ai_judgments`; `0` without a judge), decisions by type, the current active blocks and a per-hour breakdown (contract 6).

`GET /v1/users` lists every user with events, open blocks first, then by `last_seen`, with state and profile counts (contract 7).

## Tests

```bash
cd engine
.venv/Scripts/python -m pytest -q
```

## Sample events

With the engine running, send a 14-day owner history and an attacker session (stdlib only, no venv needed):

```bash
VERIDIAN_TOKEN=dev py -3.12 engine/scripts/send_sample_events.py --base-url http://localhost:8000
```

It prints the blocklist (attacker session and IP); its read of the decisions now answers `403`, since decisions are panel-only. With `--scenario bot`, the second step is a burst of 60 `GET /invoices/{id}` in 60 s from the owner's network and device: with the AI judge it can block that session only; rule-only, it records nothing.

## Demo rehearsal

Each rehearsal starts from a clean project with `py -3.12 engine/scripts/prepare_demo.py` (see its docstring): `--calibrar` once on the event wifi, then the normal mode before each run. It erases the project's data through `DELETE /v1/projects/{id}/data` with the panel credential (the rehearsal reset `POST /v1/admin/reset` was removed with U13), seeds the history and checks the profile and the empty blocklist. The scripts below are the manual steps it replaces.

1. Erase: `DELETE /v1/projects/{id}/data` (owner, panel credential), or the panel's data page.
2. Seed the presenter's history with the event wifi IP measured that morning (see `docs/DEMO.md`), every day including weekends, at the demo hours in UTC:

   ```bash
   py -3.12 engine/scripts/seed_demo_history.py --user-id u_presenter --ip <wifi IP> --hours 13,14,15,16 --device-id <cookie, if known>
   ```

   It prints the accepted count and then tries to read the profile with the SDK key, which now answers `403` (data reads are panel-only); check the profile in the panel. Without `--device-id`, the presenter's real device is new to the profile and counts as a deviation.
3. Run the demo.

## Deploy (Render)

`render.yaml` at the repo root declares the `veridian-engine` web service. In Render: **New → Blueprint**, pick this repo, and fill the secrets it asks for:

- `VERIDIAN_TOKENS`: `client=token` for each customer app; the demo app's middleware uses that token as `VERIDIAN_TOKEN`.
- `ANTHROPIC_API_KEY`: enables the AI judge.
- `VERIDIAN_PANEL_TOKEN`: the panel's service credential (the same value in the panel service).
- `VERIDIAN_BOOTSTRAP_OWNERS`: the email that should own org `Demo` on first login.
- `VERIDIAN_ORG_CREATORS`: the team's emails, with no organization limit (optional).
- `VERIDIAN_ORG_CREATORS`: emails allowed to create organizations (optional).

The service runs on the `starter` plan with a 1 GB disk at `/var/data`, where the SQLite file lives. Free instances cannot attach a disk, so every deploy would wipe the profiles, and they spin down after 15 minutes idle ([Render docs](https://render.com/docs/disks), [free tier](https://render.com/docs/free)). With a disk the service runs as one instance, and each deploy has a few seconds of downtime.

Seed the demo history against the deployed engine:

```bash
py -3.12 engine/scripts/send_sample_events.py --base-url https://<service>.onrender.com --token <token>
```
