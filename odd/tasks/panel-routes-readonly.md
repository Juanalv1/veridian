# Panel: read-only route classification

## Objective

The "Rutas críticas" page shows the route classification the engine is actually using, read-only, instead of promising "Pronto aquí".

## Problem

The classification lives in `engine/routes.yaml`, loaded once at start. The panel cannot see it, so the page only says it lives in the engine. The owner cannot check which routes Veridian treats as critical or sensitive.

## Scope (authorized)

- Engine: new read-only endpoint exposing the loaded classification; contract added to `docs/REPARTO.md`.
- Edge: `fake_engine/` honors the new contract; panel `/rutas` renders it.
- Out of scope: per-client classification, editing from the panel, wiring the OpenAPI classifier.

## Constraints

- The classification is global to the engine (one file for every client); the endpoint and page must not claim it is per-client.
- Panel fails honestly: if the engine is down or lacks the endpoint, say so; never invent routes.
- Code, comments and tests in English; team docs (`docs/`) in neutral Spanish. Conventional Commits, no AI attribution, work on `main`.

## Contract (proposed)

`GET /v1/routes` with the client bearer token, same auth as the other panel endpoints:

```json
{"routes": [{"method": "POST", "route": "/payouts/bank-account", "class": "critical"}], "default": "normal"}
```

Sorted critical → sensitive, then by route and method. Only classified routes; anything else is `default`.

## TDD

Mode: on. Source: `INIT.md` ("test first when there is clear behavior"). Runners: `uv run pytest` in `engine/` and in `edge/`.

## Tasks

- [ ] T1 Engine `GET /v1/routes` + contract 8 in `docs/REPARTO.md`. Route: delegated (writer trigger: engine code, tests and docs).
- [ ] T2 `fake_engine` endpoint + panel `/rutas` renders the classification grouped by class, with "todo lo demás: normal", a note that it is configured in the engine, and an honest fallback. Route: delegated (same writer).

## Acceptance

- `GET /v1/routes` returns the loaded file's classification; 401 without a valid token.
- `/rutas` lists POST /payouts/bank-account as critical and the two sensitive routes from the real engine; no "Pronto aquí".
- Engine unavailable or endpoint missing → honest message, no invented data.
- Both test suites green.

## Review

RDD: off (decided by global). No native review.

## Progress

Created 2026-10-25. Next: T1.
