# Node SDK (npm)

## Objective

A Node package in `sdk/node/` for Express and Next.js with the same behavior and contracts as `sdk/python/`, so a Node app can install Veridian with `npm install` and one line.

## Problem

Only the Python SDK exists. Source: `HANDOFF.md`, "José Gómez: SDK de npm y registro del backoffice", item 1, and "SDK para npm, además del de Python".

## Scope (authorized)

- New package `sdk/node/` (name to confirm on npm availability, e.g. `veridian-sdk`): batched events every ~2 s in the background; `/v1/blocklist` polling with `since`; critical routes from `/v1/routes` and `POST /v1/check` before them (12 s timeout, fail-open); device cookie `vrd_did`; client IP with `trusted_proxies`; pass-through on `401`/`429`; route report at startup (as Python `967ee9e`).
- Simple API as Python `48011c2`: key from `VERIDIAN_API_KEY`, URL from `VERIDIAN_URL` with the production default, a `user` parameter returning `null` without errors, detected session, clear startup errors. Express: `app.use(veridian({...}))`; Next.js adapter.
- Tests equivalent to the Python SDK's; README; docs next to the Python one (README, panel and landing copy coordinated with Maikol).
- Out of scope: publishing to npm (immutable; only after the user confirms), changing the Python SDK (Carlos).

## Constraints

- Contracts 1, 2, 8 and 9 of `docs/REPARTO.md`, unchanged.
- Code, comments, tests in English; team docs in neutral Spanish. Conventional Commits, no AI attribution, work on `main`.

## TDD

Mode: on (project convention, `INIT.md`). Runner: `node --test` in `sdk/node` (Node 24, no test dependencies; `express` only as a dev dependency for adapter tests). Package: plain JavaScript ESM with JSDoc, no build step, no runtime dependencies (global `fetch`, Node >= 18).

## Tasks

- [x] T1 Package scaffold, config/env defaults and startup errors, client IP and device cookie. Route: delegated (single writer). Evidence: 264e741, 29 tests pass (RED first: modules missing).
- [x] T2 Event batching, blocklist polling and enforcement, 401/429 pass-through. Route: delegated (single writer). Evidence: a79ac65, 54 tests pass in `sdk/node` (RED first: `src/core.js` missing), against a local fake HTTP engine.
- [ ] T3 Critical routes + `POST /v1/check` pre-check, route report at startup, Express and Next.js adapters. Route: delegated.
- [ ] T4 README quickstart run as-is against the fake engine; docs. Route: delegated.

## Acceptance

- The package's tests pass; the README quickstart, run as-is against the fake engine, authenticates and sends events.

## Progress

Created 2026-10-25. Next: T1, after the backoffice signup.
