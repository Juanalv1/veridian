# veridian-sdk

ASGI middleware for [Veridian](https://github.com/Juanalv1/veridian). It reports each request of your app to the Veridian engine and answers `403 {"error": "blocked"}` to any session, IP or user that is on the engine's blocklist. Requests on routes the engine classifies as `critical` are checked with the engine before they run.

## Install

```bash
pip install veridian-sdk
```

Requires Python 3.12 or newer.

## Usage

Works with any ASGI app (FastAPI, Starlette). Wrap the app and tell the middleware who the user and the session are:

```python
import os

from fastapi import FastAPI
from veridian_sdk import VeridianMiddleware

app = FastAPI()

app = VeridianMiddleware(
    app,
    token=os.environ["VERIDIAN_TOKEN"],
    base_url=os.environ["VERIDIAN_URL"],
    user_id=lambda request: request.state.user_id if hasattr(request.state, "user_id") else None,
    session_id=lambda request: request.cookies.get("sid"),
    trusted_proxies=0,
)
```

- `user_id` and `session_id` are callables that take a Starlette `Request` and return a string or `None`. They run before the app (to enforce the blocklist) and again after it (the app may have just logged the user in). Requests with no user are not reported.
- Pass a hash of the session cookie as `session_id`, never the cookie itself.
- `trusted_proxies` is the number of your own proxies in front of the app. With `0` the connection IP is used and `X-Forwarded-For` is ignored, because any client can write it.
- `exclude_paths` (default `("/static",)`) lists path prefixes that pass straight through with no event and no blocklist check.
- `routes_poll_interval` (default `30.0` seconds) is how often the critical routes are read from the engine.
- `check_timeout` (default `12.0` seconds) is how long a critical request waits for the engine's verdict.

## Critical routes

The middleware reads `GET /v1/routes` at startup and every `routes_poll_interval` seconds, and keeps the routes classified `critical` (method plus route template, for example `POST /payouts/bank-account`). Before it passes a request on one of them to your app, it calls `POST /v1/check` with the same event that will go in the batch (same `id`) and waits for the verdict:

- `block`: answers `403 {"error": "blocked"}` and your app is never called.
- `allow`: the request continues normally.

The check needs a user and a session: a critical request without them is not checked. The event is still sent in the normal batch; the engine ignores a repeated `id`. Because the app has not run yet, the pre-check carries `status` 200 and `bytes` 0, and the engine keeps the first event it sees for an id.

## What is sent

One event per request, sent in the background in batches: `id`, `ts`, `user_id`, `session_id`, `ip`, `user_agent` (first 200 characters), `device_id`, `method`, `route` (the route template, for example `/items/{item_id}`), `status` and `bytes`.

The middleware also sets its own device cookie (`vrd_did`) in the user's browser.

## What is never sent

Request and response bodies, and passwords, are never read into events.

## Fail-open

If the engine is slow or down, your app is not delayed or broken: the middleware keeps using the last blocklist and the last critical routes it knew. A check that fails, returns anything but `200`, returns an unknown verdict or takes longer than `check_timeout` lets the request through. If the routes were never loaded, nothing is checked and critical routes are unprotected until the engine answers.

## License

MIT. See [LICENSE](LICENSE).
