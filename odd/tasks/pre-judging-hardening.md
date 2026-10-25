# pre-judging-hardening

## Objective

Fix what a technical reviewer would find first, before the hackathon's AI review of the repo.

## Problem

A blind dual review of `73eb1ce` found an event-ingest bug that stalls detection, two security holes in the product's own controls, and cheap deploy, docs and CI gaps.

## Scope

- T1 One bad event must never stall reporting. The engine rejects a whole batch (422) when one event has `session_id: null` (both SDKs send it) or `status: 0` (Python SDK when the app raises). The SDK never dequeues a non-202 batch, so it re-sends it forever.
- T2 The webhook SSRF guard must connect to the addresses it checked. Today `HttpxTransport.send` resolves the host again, which allows DNS rebinding.
- T3 The self-unlock email must go to an address known before the incident. Today it goes to the address in the latest event, which a thief with the session can change first.
- T4 Cheap fixes: the env vars missing from `render.yaml`, the `trusted_proxies` value for Render in the README, a CI workflow that runs the tests, `pytest` removed from the runtime requirements, and security headers on the panel.

## Out of scope (noted, not fixed here)

- The SDKs fall back to the first `X-Forwarded-For` entry, which the client writes, when there are fewer entries than `trusted_proxies`.
- The auth failure limiter is keyed by the visible key prefix, so anyone who knows it can lock out the real key.
- Critical-route checks fail open when the engine errors.
- Blocks never expire.
- Rebuilding the profile under the write lock.

## Checklist

- [ ] T1 Ingest tolerance and SDK event fixes, with tests against the real engine contract
- [ ] T2 Pin webhook connections to the checked addresses
- [ ] T3 Pin the self-unlock recipient to an address from before the incident
- [ ] T4 Fix render.yaml, the README proxies line, add a CI workflow, clean up requirements, add panel headers

## Acceptance

Every task: a RED test where one is meaningful, the full suite of the affected package green, one Conventional Commit on `main`.

## Progress

- Route: delegated direct, one writer per task, run in sequence. RDD is off globally, so there is no native review.
- Commits use explicit paths, never `git add -A`, so teammates' changes are not swept in.
