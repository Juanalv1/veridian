# Install Veridian (guide for coding agents)

Veridian detects when someone other than the owner uses a credential, and blocks it. A middleware in the customer's app reports each access to the Veridian engine and answers `403 {"error": "blocked"}` to blocked sessions, IPs or users. This guide adds that middleware to the project you are working in and verifies it.

Follow the steps in order. Every step has a check. If a check fails, stop and tell the human what failed; do not improvise around it.

## Goal

When you finish: the app runs with the Veridian middleware, `user` is wired to the app's real authentication, and the Veridian panel shows the project as "Conectado" after one authenticated request.

## Prerequisites (the human provides them)

- A Veridian API key, created by the human in the panel (https://veridian-panel.onrender.com, "Claves de API", one key per project). It looks like `vrd_...`.
- The key must live in the environment variable `VERIDIAN_API_KEY`, in the app's gitignored env file (for example `.env`) or the host's secret store.

Rules about the key:

- Do NOT ask the human to paste the key into this chat. Anything pasted here goes to a third-party model.
- Never write the key in source code, a committed file, a log, a test or a commit message.
- If `VERIDIAN_API_KEY` is not set, do not stop the integration: write the code, then tell the human which env file to put it in (check it is listed in `.gitignore`; if it is not, tell the human instead of editing the key into anything).

## Step 1. Detect the stack

Look at the project's manifest (`pyproject.toml`, `requirements*.txt`, `package.json`) and entry points.

| You find | Use |
|---|---|
| Python with FastAPI or Starlette (any ASGI app) | Python SDK |
| Node with Express 4 or 5 | Node SDK, Express |
| Next.js App Router route handlers, on the Node.js runtime, on a server that stays running (`next start`, Docker, a VM) | Node SDK, Next.js |
| Anything else (Django/Flask WSGI, Rails, Go, Next.js Edge runtime, `middleware.ts`, serverless that freezes after each response) | **Stop.** Tell the human the stack is not supported yet. Do not try to adapt the SDK. |

If the project has several services, ask the human which one to protect (one question, then wait).

Check: you can name the framework and the file where the app object is created.

## Step 2. Install

Python (3.12 or newer):

```bash
pip install veridian-sdk
```

Node (18 or newer):

```bash
npm install veridian-sdk
```

Use the project's own package manager and record the dependency the way the project does (`requirements.txt`, `pyproject.toml`, `package.json` + lockfile; `uv add`, `poetry add`, `pnpm add`, `yarn add` are fine).

Check: `python -c "import veridian_sdk"` or `node -e "import('veridian-sdk')"` succeeds.

## Step 3. Add the middleware

Add it where the app is created. One style per SDK.

### Python (FastAPI, Starlette, any ASGI app)

```python
from veridian_sdk import VeridianMiddleware

app.add_middleware(
    VeridianMiddleware,
    user=lambda request: request.state.user.id,  # replace in step 4
    trusted_proxies=0,                           # replace in step 5
)
```

The API key is read from `VERIDIAN_API_KEY`; do not pass `api_key`. In Starlette the middleware added last runs first. If the app's login puts the user in `request.state` from another middleware, add that middleware with `add_middleware` after Veridian's call, so it runs first and the user is already in `request.state` when Veridian reads it (the `user` function also runs again after the routes).

### Node: Express (4 or 5)

```js
import { veridian } from 'veridian-sdk';

app.use(
  veridian({
    user: (req) => req.user.id, // replace in step 4
    trustedProxies: 0,          // replace in step 5
  }),
);
```

Register it after the middleware that identifies the user (Passport, `express-session`, your own) and before the routes. The API key is read from `VERIDIAN_API_KEY`; do not pass `apiKey`. The package is an ES module; a CommonJS app can `require('veridian-sdk')` on Node 20.19+ / 22.12+, otherwise `const { veridian } = await import('veridian-sdk')`.

### Node: Next.js route handlers

Create one instance for the whole process:

```js
// lib/veridian.js
import { createVeridian } from 'veridian-sdk/next';

export const protect = createVeridian({
  user: async (request) => (await getSession(request))?.userId, // replace in step 4
  trustedProxies: 1,                                            // replace in step 5
});
```

Wrap each route handler you want reported, with its route template:

```js
// app/api/payouts/bank-account/route.js
import { protect } from '@/lib/veridian';

export const POST = protect(
  async (request) => Response.json({ ok: true }),
  { route: '/api/payouts/bank-account' }, // for [id] folders: '/api/invoices/[id]'
);
```

Wrap the handlers that matter (anything that moves money, changes credentials or reads personal data) and the authenticated ones; ask the human if unsure which. Without `route`, the raw pathname is reported and may carry IDs.

Check: the app still starts and serves a request.

## Step 4. Wire `user` to the app's existing authentication

`user` tells Veridian who makes the request: the id of the authenticated user, or `None` / `null` when nobody is logged in. Requests with no user are not reported.

1. Find where the app already stores the authenticated user for a request: `request.state.user`, a FastAPI `Depends(get_current_user)` that decodes a session or JWT, `req.user` (Passport), `req.session.userId`, a `getSession()` / `auth()` helper in Next.js, etc. Read the existing auth code; do not guess.
2. Write `user` so it returns that user's stable id (a string or number; numbers are sent as strings) and `None` / `null` when anonymous. Prefer a safe lookup:
   - Python: `user=lambda request: getattr(getattr(request.state, "user", None), "id", None)`
   - Express: `user: (req) => req.user?.id ?? null`
3. If the id only exists after a dependency or a decode step (for example a JWT that FastAPI decodes inside a dependency), reuse that same decode function in `user`; do not duplicate secret handling and do not log tokens.
4. **Never invent authentication.** If the app has no authentication, or you cannot find where the authenticated user id lives, stop and ask the human where it is. Do not add a login system, and do not use IP, headers or cookies as a stand-in user id.

Check: for a logged-in request `user` returns the id; for an anonymous request it returns `None` / `null` and does not raise.

## Step 5. Set `trusted_proxies` / `trustedProxies`

This is the number of your own proxies in front of the app that append to `X-Forwarded-For`. It decides which IP Veridian records.

- `0` (default): the connection IP is used and `X-Forwarded-For` is ignored. Correct when the app is reached directly.
- `N`: the Nth value from the right of `X-Forwarded-For` is used. Too low and the IP is your proxy's; too high and the IP is whatever the client wrote (anyone can write it, and it can dodge IP-based protections).

How to decide:

1. Look at the deployment: load balancer, CDN, ingress, PaaS router. Count each hop that appends to `X-Forwarded-For`. A single load balancer in front is `1` (Render and Heroku customer apps are documented as `1` in the SDK READMEs).
2. Do not assume. Measured values in this project differ by setup: the Veridian engine on Render needed `2` (with `3` a client-written value was trusted), and the Veridian demo app on Render measured `3`. Platform defaults can change.
3. If the app runs locally with no proxy, use `0`.
4. If you cannot tell how many proxies are in front, ask the human (one question) and leave `0` until they answer. `0` is the safe value: it never trusts a client-written header.

In Next.js a route handler has no socket, so with `0` the IP is empty unless Next provides `request.ip`, and an empty IP means critical routes are not checked. Set the real proxy count, or pass `ip: (request) => string` to `createVeridian`.

## Step 6. Critical routes

You do not configure these. At startup the SDK reports the app's routes (method, route template, summary, description; never code or bodies) and the engine classifies them automatically. Requests on routes classified `critical` are checked with the engine before they run. Two points:

- A critical request is only checked when it has a user and a session. The session is detected from a cookie (`sessionid`, `connect.sid`, `sid`, `session_id`, `PHPSESSID`, `JSESSIONID`) or an `Authorization: Bearer` token. If the app uses another cookie, pass `session` (Python) / `session` (Node): a function returning a hash of the session id, never the raw cookie.
- Next.js and Express 5 mounted routers: the SDK cannot read those routes, so pass them with the `routes` option, otherwise they are not classified.
  - Next.js: `createVeridian({ ..., routes: [{ method: 'POST', route: '/api/payouts/bank-account' }] })`
  - Express 5 (`app.use('/api', router)`): `veridian({ ..., routes: [{ method: 'POST', route: '/api/payouts/bank-account' }] })`. Add `summary` and `description` for the routes that matter.
  - Python apps that are not FastAPI have no OpenAPI document; their routes are classified in the panel.

Check: nothing to run; just make sure mounted routers or Next.js handlers are listed in `routes` when applicable.

## Step 7. Verify

1. Make sure `VERIDIAN_API_KEY` is set for the process you start (ask the human if it is not; never ask for the value).
2. Start the app the way the project normally does.
3. Make one request as an authenticated user, or ask the human to log in and open one page. A request with no user is not reported.
4. The human opens the panel's "Claves de API" page (https://veridian-panel.onrender.com) and checks the connection line says **Conectado**. It updates by itself; no reload needed. The access also appears in "Actividad".
5. If it does not connect: look for `veridian:` lines in the app's log. `401` means the key is wrong or revoked: tell the human to create a new one in the panel. No lines at all usually means `user` returned `None` for every request.

Report to the human: which stack you detected, the files you changed, where `user` reads the id from, the `trusted_proxies` value and why, and what is still pending (key, proxies question).

## Fail-open

If the Veridian engine is slow, down, rate limiting (`429`) or rejecting the key (`401`), the app keeps working: the middleware lets requests through and keeps the last blocklist. Never add code that turns this into fail-closed (no try/except that returns 403 on SDK errors, no startup check that crashes the app).

## What not to do

- Do not commit the API key or print it; do not put it in the prompt, chat, code, tests or CI logs.
- Do not wrap authentication or login routes differently from the rest, and do not exclude them with `exclude_paths` / `excludePaths`: they are where stolen credentials show up.
- Do not disable or work around fail-open.
- Do not invent a user id source or an authentication layer.
- Do not edit other dependencies, auth logic or unrelated files.
- Do not trust `X-Forwarded-For` by raising `trusted_proxies` without counting the proxies.
- Do not guess when a step is unclear: stop and ask the human one question.

## Reference

- Python SDK: https://github.com/Juanalv1/veridian/blob/main/sdk/python/README.md
- Node SDK (Express and Next.js): https://github.com/Juanalv1/veridian/blob/main/sdk/node/README.md
