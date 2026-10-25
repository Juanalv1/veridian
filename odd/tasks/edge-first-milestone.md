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
- [x] T5 Run instructions (`edge/README.md`, Spanish) and a local end-to-end check of the demo path. Route: delegated. Evidence: 1f56bf4; `uv run pytest -q` 65 passed; local smoke (3 uvicorn processes): owner login 303, invoices 200; attacker (XFF 9.9.9.9, 203.0.113.9, trusted_proxies=1) bank POST 200, ~5 s later 403 blocked; owner 200; panel shows block + rationale.
- [x] T6 Panel: port approved design (users, decision status, mark normal, profile). Route: delegated (writer trigger: panel code, template, tests, fake engine). Evidence: 1b021ea; `uv run pytest -q` 77 passed; smoke (3 uvicorn processes): attacker blocked 403, panel showed BLOQUEADO + "Esto era normal"; POST mark-normal via panel 303; panel then showed BLOQUEO LEVANTADO. Also fixed fake engine profile `routes` to include the method (contract section 4).
- [x] T7 Panel v2: verdict + profile vs access comparison. Route: delegated (writer trigger: panel code, template, tests, fake engine). Evidence: e7ac2cd; `uv run pytest -q` 91 passed; smoke (3 uvicorn processes, VERIDIAN_SEED=demo): attacker blocked 403, panel showed "Bloqueado" with 127.0.0.0/24 vs 203.0.113.9 "nueva"; POST mark-normal via panel 303, panel then showed "Bloqueo levantado" and no button. Fake engine decisions now carry `access`.
- [x] T8 Split CSS/JS and templates (no visual change). Route: delegated (writer trigger: 10+ files across panel, demo, landing, middleware). Evidence: b724876 (middleware `exclude_paths`, default `/static`, TDD: 3 RED then GREEN), 376bb12 (panel), 88b2023 (demo), 910c733 (landing); `uv run pytest -q` 97 passed; parity before/after with 3 local servers and the README attacker flow (attacker bank POST 200, then 403 blocked): visible text of panel `/`, demo `/login`, `/invoices`, `/payouts/bank-account` identical except per-run timestamps and the random device id; extracted CSS identical to the new files (`diff` empty). Decision: static requests skip the blocklist and are not reported. Screenshots not compared.
- [x] T9 Panel interactivity with htmx (vendored 2.0.4). Route: delegated (writer trigger: panel template/partials, JS, main, tests, vendored file). Evidence: 3e59206; `uv run pytest -q` 103 passed; browser smoke (chrome-devtools, 3 local servers): `window.__marker` survived >4 s of polling, "Esto era normal" swapped to "Bloqueo levantado" without reload, boosted nav updates URL and back works, no console errors, times in local zone (America/Caracas). Unverified: ran tests together with code (no separate RED run).
- [x] T10 Design system + interaction UX (panel, landing, demo focus/loading/forms) and detection time in the panel. Route: delegated (writer trigger: 15+ files across panel, landing, demo, tests). Evidence: 6339920 (panel), 6b34c2c (landing), 4068c4f (demo), 6989cd5 (detection time); TDD on, RED observed for each group (8 failing before panel/landing/demo changes, 6 failing before detection time); `uv run pytest -q` 122 passed. Browser check (chrome-devtools, isolated page, 3 local servers, README attacker flow: bank POST 200 then 403): panel button bg rgb(76,194,154), `.btn:active` rule is scale(var(--press-scale)=0.97), 'Esto era normal' showed 'Marcando…' + aria-busy + disabled in flight (request delayed 800 ms) then swapped to 'Bloqueo levantado'; demo login: Tab focus outline 2px solid, hover bg rgb(36,87,214) -> rgb(29,71,179), submit shows 'Entrando…' disabled; no horizontal scroll at 390px (landing, panel); engine killed: '#conn' read 'Motor sin conexión · último dato hace 10 s' with role=status; no console errors; panel showed 'Detectado y bloqueado en 2 s'. Unverified: hover on panel nav and landing links not measured (CSS only); Tab focus on panel/landing not exercised in browser.
- [x] T11 Live activity feed (contract 5) and reporting blocked requests. Route: delegated (writer trigger: middleware, fake engine, panel code/templates/CSS, tests). Evidence: 5ab3301 (middleware reports 403 events with the route matched against the app's router without calling it; raw-path fallback commented), af65f92 + 46c4bf8 (fake engine: 403 events never learned or judged, `GET /v1/activity` with the post-block rule and the marked-normal rule; 46c4bf8 fixes a test fixture), 70c86d1 (panel feed fed by `/v1/activity`, empty state with real counts, graceful 404 fallback, "Esto era normal" hidden for `allow`), plus the CSS width tweak commit. TDD on, RED observed per group (4 failing middleware, 14 failing engine, 12 failing panel before code); `uv run pytest -q` 157 passed. Browser check (chrome-devtools, isolated page, 3 local servers, VERIDIAN_SEED=demo): owner login/invoices/invoice -> 3 Normal rows, no h1, "Todo normal. Veridian está vigilando 3 accesos de 1 usuario."; attacker login + bank POST (200) -> Bloqueado decision; two attacker GET /invoices -> 403 x2 -> two "Bloqueado ... rechazado" rows linking to /users/u_42/decisions/d_1 (red word rgb(255,107,87), normal words rgb(154,168,162)); "Esto era normal" swapped in place (marker kept) -> all 7 rows Normal, "Bloqueo levantado"; scrollWidth == clientWidth at 1440 and at 390 (emulated); no console errors. Layout: left "Actividad" feed replaces "Decisiones recientes" when the engine has `/v1/activity`; right the selected decision as hero; below 960 px the feed stacks under the detail. Unverified: hover/focus on feed rows not exercised in the browser; real engine's `/v1/activity` not tested.

## Acceptance

- `uv run pytest` green in `edge/`.
- Local run: owner browses normally (no block); a login from another IP that posts to `/payouts/bank-account` gets `403` on the next request; the panel lists the `block` decision.

## Progress

- Created 2026-10-25. T1-T5 done 2026-10-25. Next: swap fake engine for Dev 1's engine; open questions in the hand-off report.
