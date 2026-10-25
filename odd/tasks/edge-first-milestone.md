# Edge first milestone (Dev 2)

## Objective

End-to-end path without AI on the edge side: the attacker logs in with the owner's password, changes the payout bank account, and the next request is blocked with `403`; the panel shows the decision.

## Scope (authorized)

Only `edge/`: `middleware/`, `demo_app/`, `panel/`, plus `fake_engine/` that honors the contracts in `docs/REPARTO.md` until Dev 1's engine exists. No contract changes.

## Constraints

- Python 3.12, FastAPI, Jinja server-rendered templates, pytest, managed with `uv` from `edge/`.
- Contracts in `docs/REPARTO.md` are the boundary with Dev 1.
- Middleware never slows or breaks the app: background send/poll, fail open with last known blocklist.
- Never send request bodies, passwords or customer data.
- Client IP: `trusted_proxies` rule (rightmost N of `X-Forwarded-For`), default 0 = socket IP.
- No secrets in the repo: `VERIDIAN_TOKEN`, `VERIDIAN_URL` from env.

## TDD

Mode: on. Source: `INIT.md` ("test first when there is clear behavior"). Runner: `uv run pytest` in `edge/`.

## Tasks

- [x] T1 Scaffold `edge/` (pyproject, uv, Python 3.12) and `fake_engine/` implementing the 4 contracts in memory, with the Dev 1 first-milestone rule (critical route from a new /24 → block session + IP). Route: delegated (writer trigger: 2+ non-trivial files). Evidence: a062f66 (code landed in the hook sync commit 08d3c16); `uv run pytest -q` 26 passed.
- [x] T2 Middleware: event capture (route template, IP rule, `device_id` cookie, user/session callables), batched background sender (~2 s, bounded buffer, retry), blocklist poller (`since`, `304`, keep last list on failure), `403 {"error": "blocked"}`. Route: delegated. Evidence: b9b4abd; `uv run pytest -q` 50 passed.
- [x] T3 Demo app: login, invoices (list, detail, export), payout bank account (view, change), using the middleware. Route: delegated. Evidence: 779c08b; `uv run pytest -q` 60 passed. Note: fake engine learns unflagged events after VERIDIAN_LEARN_DELAY (default 300 s) so the attacker's own login does not whitelist their network.
- [x] T4 Panel: lists `GET /v1/decisions` with signals and rationale, auto-refresh. Route: delegated. Evidence: 34bd444; `uv run pytest -q` 65 passed.
- [x] T5 Run instructions (`edge/README.md`, Spanish) and a local end-to-end check of the demo path. Route: delegated. Evidence: COMMIT_T5; `uv run pytest -q` 65 passed; local smoke (3 uvicorn processes): owner login 303, invoices 200; attacker (XFF 9.9.9.9, 203.0.113.9, trusted_proxies=1) bank POST 200, ~5 s later 403 blocked; owner 200; panel shows block + rationale.

## Acceptance

- `uv run pytest` green in `edge/`.
- Local run: owner browses normally (no block); a login from another IP that posts to `/payouts/bank-account` gets `403` on the next request; the panel lists the `block` decision.

## Progress

- Created 2026-10-25. T1-T5 done 2026-10-25. Next: swap fake engine for Dev 1's engine; open questions in the hand-off report.
