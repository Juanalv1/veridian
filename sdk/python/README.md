# veridian-sdk

ASGI middleware for [Veridian](https://github.com/Juanalv1/veridian). It reports each request of your app to the Veridian engine and answers `403 {"error": "blocked"}` to any session, IP or user that is on the engine's blocklist. Requests on routes the engine classifies as `critical` are checked with the engine before they run.

## Install

```bash
pip install veridian-sdk
```

Requires Python 3.12 or newer.

## Usage

Create an API key in the Veridian panel (Claves de API; one per project) and put it in the `VERIDIAN_API_KEY` environment variable of your app. Keep it out of source control; if it leaks, revoke it in the panel and create another.

Works with any ASGI app (FastAPI, Starlette). This is the whole integration; it runs as is (`examples/quickstart.py`, tested against a fake engine):

```python
from fastapi import FastAPI
from veridian_sdk import VeridianMiddleware

app = FastAPI()

app.add_middleware(
    VeridianMiddleware,
    # Id de tu usuario, o None si nadie inició sesión
    user=lambda request: request.state.user.id,
    # Proxies tuyos delante de la app (0 si no hay)
    trusted_proxies=0,
)
```

The session is detected on its own and Veridian only receives a hash, never the value. If Veridian does not answer, your app keeps working: every request passes. To check the connection, log in to your app: within seconds the access appears in Actividad in the panel, and the connection line says Conectado.

| Argument | Default | What it does |
|---|---|---|
| `user` | none | Function `request -> id or None`: who makes the request. It runs before your routes (to enforce the blocklist and check critical routes) and again after them (your app may have just logged the user in). A lookup that raises `AttributeError` or `LookupError`, like `request.state.user.id` with nobody logged in, counts as `None`. Ints are sent as strings. Requests with no user are not reported. |
| `user_email` | none | Optional function `request -> email or None`, like `user`. With it, Veridian can email a blocked user a link to unlock themselves (self-unlock by email, turned on per project in the panel). It is personal data: Veridian keeps only the last one per user. `None`, a failing lookup or something that is not an address means "no email". |
| `session` | detected | Function `request -> id or None`. Without it, the session is the hash of the first of these cookies: `sessionid`, `connect.sid`, `sid`, `session_id`, `PHPSESSID`, `JSESSIONID`; else the hash of an `Authorization: Bearer` token; else none. Starlette's `session` cookie is ignored because it changes on every response: with it, pass `session` yourself (a hash, never the cookie). **Critical routes are only checked when there is a session.** |
| `api_key` | `VERIDIAN_API_KEY` | The project's key. Missing: `VeridianConfigError` when the app starts. |
| `base_url` | `VERIDIAN_URL`, else `https://veridian-engine.onrender.com` | The engine. A value that is not a full `http(s)` URL: `VeridianConfigError`. |
| `trusted_proxies` | `0` | Number of your own proxies in front of the app. With `0` the connection IP is used and `X-Forwarded-For` is ignored, because any client can write it. Behind one load balancer (Render, Heroku), `1`. |
| `exclude_paths` | `("/static",)` | Path prefixes that pass straight through with no event and no blocklist check. |
| `routes_poll_interval` | `30.0` s | How often the critical routes are read from the engine. |
| `check_timeout` | `12.0` s | How long a critical request waits for the engine's verdict. |
| `report_routes` | `True` | Send the app's routes (method, route template, OpenAPI summary and description) once at startup, so the engine classifies them. Retried every `routes_poll_interval` until accepted. |

Middleware order: in Starlette, the middleware added last runs first. If your login puts the user in `request.state` from another middleware, add that one after Veridian's.

Self-unlock by email: when the engine says a blocked session was sent the unlock email, the `403` body adds `"message": "Detectamos un acceso inusual. Te enviamos un correo para confirmar que fuiste tú."` for your app to show. Otherwise the body is exactly `{"error": "blocked"}`.

The older names still work: `token` for `api_key`, `user_id` for `user`, `session_id` for `session`, and wrapping the app (`app = VeridianMiddleware(app, ...)`).

### Check that it is connected

Log in to your app and open a page. Within a few seconds the access shows up in Actividad, in the Veridian panel. If it does not, look for `veridian:` lines in your app's log (`401` means the key is wrong or revoked).

## Critical routes

The middleware reads `GET /v1/routes` at startup and every `routes_poll_interval` seconds, and keeps the routes classified `critical` (method plus route template, for example `POST /payouts/bank-account`). Before it passes a request on one of them to your app, it calls `POST /v1/check` with the same event that will go in the batch (same `id`) and waits for the verdict:

- `block`: answers `403 {"error": "blocked"}` and your app is never called.
- `allow`: the request continues normally.

The check needs a user and a session: a critical request without them is not checked. The event is still sent in the normal batch; the engine ignores a repeated `id`. Because the app has not run yet, the pre-check carries `status` 200 and `bytes` 0, and the engine keeps the first event it sees for an id.

## What is sent

One event per request, sent in the background in batches: `id`, `ts`, `user_id`, `session_id`, `ip`, `user_agent` (first 200 characters), `device_id`, `method`, `route` (the route template, for example `/items/{item_id}`), `status` and `bytes`; plus `user_email` only if you pass `user_email`.

The middleware also sets its own device cookie (`vrd_did`) in the user's browser.

At startup, once: the app's routes from its OpenAPI document (`method`, `route`, `summary`, `description`), so the engine can classify them. Apps without an OpenAPI document (anything that is not FastAPI) send nothing, and their routes are classified in the panel.

## What is never sent

Request and response bodies, and passwords, are never read into events. Source code is never sent: the route report only carries what the OpenAPI document says.

## Fail-open

If the engine is slow or down, your app is not delayed or broken: the middleware keeps using the last blocklist and the last critical routes it knew. A check that fails, returns anything but `200`, returns an unknown verdict or takes longer than `check_timeout` lets the request through. If the routes were never loaded, nothing is checked and critical routes are unprotected until the engine answers.

This includes `401` (key unknown or revoked) and `429` (rate limited): your app keeps serving requests, nothing is retried in a tight loop, the last blocklist and routes stay in use, and queued events are sent again on the next flush. With a revoked key the app is therefore not protected, so replace the key in the panel.

## Installing with a coding agent

Give your coding agent this prompt, or use the "Copiar prompt para tu agente" button in the panel or on the landing page: it points the agent to the [install guide](https://veridian-platanus.onrender.com/install.md) (source: `landing/install.md`). The API key is never part of the prompt: the agent reads it from `VERIDIAN_API_KEY`, so put it in your gitignored env file yourself.

## License

MIT. See [LICENSE](LICENSE).
