# veridian-sdk

Express and Next.js middleware for [Veridian](https://github.com/Juanalv1/veridian). It reports each request of your app to the Veridian engine and answers `403 {"error": "blocked"}` to any session, IP or user that is on the engine's blocklist. Requests on routes the engine classifies as `critical` are checked with the engine before they run.

Same behavior and contracts as the Python SDK (`pip install veridian-sdk`), with no runtime dependencies.

## Install

```bash
npm install veridian-sdk
```

Requires Node.js 18 or newer (it uses the global `fetch`). The package is an ES module; a CommonJS app can `require('veridian-sdk')` on Node 20.19+ / 22.12+, and on older versions can `const { veridian } = await import('veridian-sdk')`.

## Usage with Express

Create an API key in the Veridian panel (Claves de API; one per project) and put it in the `VERIDIAN_API_KEY` environment variable of your app. Keep it out of source control; if it leaks, revoke it in the panel and create another.

This is the whole integration; it runs as is (`examples/quickstart.js`, tested against the fake engine):

```js
// 1. Instala el SDK:            npm install veridian-sdk
// 2. Pon tu clave en el entorno (nunca en el código), la que creaste en el panel:
//                               VERIDIAN_API_KEY=vrd_...
import express from 'express';
import { veridian } from 'veridian-sdk';

const app = express();

// Si tu login (Passport, express-session...) pone al usuario en req.user, regístralo
// antes que Veridian: así ya sabe quién es cuando llega la petición.
app.use(
  veridian({
    // Quién hace la petición: el id de tu usuario, o null si nadie inició sesión.
    // Si nadie inició sesión y req.user no existe, cuenta como null, sin errores.
    user: (req) => req.user.id,
    // Cuántos proxies tuyos hay delante de la app (un balanceador, Render, Heroku: 1).
    // Con 0 usa la IP de la conexión e ignora X-Forwarded-For, que cualquiera puede escribir.
    trustedProxies: 0,
  }),
);

// ...aquí van tus rutas...

// La sesión se detecta sola: cookie sessionid, connect.sid, sid, session_id, PHPSESSID
// o JSESSIONID, o el token Bearer. Veridian solo recibe un hash, nunca el valor.
// Si Veridian no responde, tu app sigue funcionando: deja pasar todas las peticiones.
// Para comprobar la conexión: entra a tu app con un usuario y, en unos segundos,
// ese acceso aparece en Actividad, en el panel de Veridian.
app.listen(3000);
```

Works with Express 4 and 5. Register Veridian **after** the middleware that identifies your user (Passport, `express-session`, your own) and **before** your routes. The `user` function runs before your routes, to enforce the blocklist and check critical routes, and again when the response is sent (your app may have just logged the user in).

| Option | Default | What it does |
|---|---|---|
| `user` | none | Function `(req) => id or null`: who makes the request (sync or async). A lookup that throws `TypeError`, like `req.user.id` with nobody logged in, counts as `null`. Numbers are sent as strings. Requests with no user are not reported. Any other error is logged and counts as `null`. |
| `session` | detected | Function `(req) => id or null`. Without it, the session is the hash of the first of these cookies: `sessionid`, `connect.sid`, `sid`, `session_id`, `PHPSESSID`, `JSESSIONID`; else the hash of an `Authorization: Bearer` token; else none. Pass your own function to use a cookie called `session` (cookie-session), which changes on every response: send a hash, never the cookie. **Critical routes are only checked when there is a session.** |
| `apiKey` | `VERIDIAN_API_KEY` | The project's key. Missing: `VeridianConfigError` when `veridian()` is called. |
| `baseUrl` | `VERIDIAN_URL`, else `https://veridian-engine.onrender.com` | The engine. A value that is not a full `http(s)` URL: `VeridianConfigError`. |
| `trustedProxies` | `0` | Number of your own proxies in front of the app. With `0` the connection IP is used and `X-Forwarded-For` is ignored, because any client can write it. With `N`, the Nth value from the right of `X-Forwarded-For` is used. Behind one load balancer (Render, Heroku), `1`. |
| `excludePaths` | `['/static']` | Path prefixes (whole segments) that pass straight through with no event, no cookie and no blocklist check. |
| `routes` | read from Express | Routes to report at startup: `[{ method, route, summary?, description? }]` or a function returning that. See "Route report". |
| `reportRoutes` | `true` | Report the app's routes at startup so the engine classifies them. |
| `routesPollInterval` | `30000` ms | How often the critical routes are read from the engine (and the report is retried until accepted). |
| `checkTimeout` | `12000` ms | How long a critical request waits for the engine's verdict. |
| `flushInterval`, `pollInterval` | `2000` ms | How often events are sent and the blocklist is read. |

Intervals are in milliseconds (the Python SDK uses seconds). `veridian()` returns the middleware; `await mw.close()` stops its timers and sends what is queued, if you want a clean shutdown. The timers are `unref`'d, so they never keep your process alive.

### Check that it is connected

Log in to your app and open a page. Within a few seconds the access shows up in Actividad, in the Veridian panel. If it does not, look for `veridian:` lines in your app's log (`401` means the key is wrong or revoked).

## Usage with Next.js

Supported: **route handlers of the App Router, on the Node.js runtime, on a server that stays running** (`next start`, Docker, a VM). Not supported: the Edge runtime (the SDK uses `node:crypto` and background timers), `middleware.ts` (it cannot see the response status or send the device cookie), and serverless platforms that freeze the process after each response (events and the blocklist are batched in memory every ~2 s, so they can be lost or stale there).

```js
// lib/veridian.js: one instance for the whole process
import { createVeridian } from 'veridian-sdk/next';

export const protect = createVeridian({
  user: async (request) => (await getSession(request))?.userId, // your own session lookup
  trustedProxies: 1, // see below
});
```

```js
// app/api/payouts/bank-account/route.js
import { protect } from '@/lib/veridian';

export const POST = protect(
  async (request) => Response.json({ ok: true }),
  { route: '/api/payouts/bank-account' }, // the route template; for [id] folders use '/api/invoices/[id]'
);
```

- Options are the same as for Express, plus `ip: (request) => string` to say where the client address is. A route handler has no socket, so with `trustedProxies: 0` the IP is empty unless Next provides `request.ip`; an empty IP means critical routes are not checked. Set `trustedProxies` to the number of proxies in front of Next, or pass `ip`.
- `route` is the template reported with each event. Without it, the raw pathname is used and it may carry IDs (`/invoices/42`), which the engine groups less well.
- Next.js has no router to read, so routes are not reported automatically: pass `routes: [{ method: 'POST', route: '/api/payouts/bank-account' }]` to `createVeridian` for the classification at startup.
- The event's `bytes` is the response's `Content-Length`, or `0` when it has none.

## Critical routes

The middleware reads `GET /v1/routes` at startup and every `routesPollInterval`, and keeps the routes classified `critical` (method plus route template, for example `POST /payouts/bank-account`). Before it passes a request on one of them to your app, it calls `POST /v1/check` with the same event that will go in the batch (same `id`) and waits for the verdict:

- `block`: answers `403 {"error": "blocked"}` and your app is never called.
- `allow`: the request continues normally.

The check needs a user, a session and an IP: a critical request without them is not checked. The event is still sent in the normal batch; the engine ignores a repeated `id`. Because the app has not run yet, the pre-check carries `status` 200 and `bytes` 0, and the engine keeps the first event it sees for an id.

Route templates use `{name}` for parameters (`/invoices/{id}`), as the Python SDK and the engine do: Express `:id` and Next `[id]` are converted.

## Route report

At startup, once (retried every `routesPollInterval` until the engine accepts it), the SDK sends the app's routes to `PUT /v1/routes/catalog` so the engine classifies them with the AI: only `method`, `route` template, `summary` and `description`, never code.

- **Express:** the SDK reads Express's own router when the first request arrives (so every route registered at startup is there). It sees top-level routes and `app.all`; on Express 4 also routers mounted on a path (`app.use('/api', router)`, with or without parameters). On **Express 5, routers mounted on a path cannot be read** and are left out instead of guessed: pass them with the `routes` option. Express routes have no summary or description, so the engine classifies them by method and path alone; add `summary` and `description` through `routes` for the ones that matter.
- **Next.js:** pass `routes` (see above).

## What is sent

One event per request, sent in the background in batches: `id`, `ts`, `user_id`, `session_id`, `ip`, `user_agent` (first 200 characters), `device_id`, `method`, `route` (the route template, for example `/items/{id}`), `status` and `bytes` (the `Content-Length` of the response, `0` when there is none).

The middleware also sets its own device cookie (`vrd_did`) in the user's browser.

At startup, once: the route report above.

## What is never sent

Request and response bodies, and passwords, are never read. Source code is never sent. A session is sent as a hash, never as the cookie or token.

## Fail-open

If the engine is slow or down, your app is not delayed or broken: the middleware keeps using the last blocklist and the last critical routes it knew. A check that fails, returns anything but `200`, returns an unknown verdict or takes longer than `checkTimeout` lets the request through. An error inside the SDK also lets the request through. If the routes were never loaded, nothing is checked and critical routes are unprotected until the engine answers.

This includes `401` (key unknown or revoked) and `429` (rate limited): your app keeps serving requests, nothing is retried in a tight loop, the last blocklist and routes stay in use, and queued events are sent again on the next flush (up to 1000 are kept; the oldest are dropped). With a revoked key the app is therefore not protected, so replace the key in the panel.

## Tests

```bash
cd edge && uv sync        # once: the fake engine the integration test runs against
cd sdk/node && npm install && node --test
```

The integration test (`test/quickstart.test.js`) starts `edge/fake_engine`, runs `examples/quickstart.js` unchanged and checks that it authenticates and sends events; it is skipped when `uv` is not installed.

## Installing with a coding agent

Give your coding agent this prompt, or use the "Copiar prompt para tu agente" button in the panel or on the landing page: it points the agent to the [install guide](https://veridian-platanus.onrender.com/install.md) (source: `landing/install.md`). The API key is never part of the prompt: the agent reads it from `VERIDIAN_API_KEY`, so put it in your gitignored env file yourself.

## License

MIT. See [LICENSE](LICENSE).
