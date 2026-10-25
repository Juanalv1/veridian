# Backoffice self-signup

## Objective

Anyone can sign up alone in the panel and leave Veridian wired into their app: sign in with Google or GitHub, name their company once, create an API key with a ready snippet, and see the connection confirmed by the first event.

## Problem

The panel only signs in with Google (T3), organization creation is closed to `VERIDIAN_ORG_CREATORS`, and nothing tells a new customer whether their app is connected. Source: `HANDOFF.md`, section "José Gómez: SDK de npm y registro del backoffice", item 2 (request from Juan, 2026-10-25).

## Scope (authorized)

- Panel (`edge/panel/`): GitHub OAuth next to Google (scope `user:email`, primary verified email); one account per verified email across providers; first-run step asking only the company name, which creates the org (person as `owner`) and its first project; then the existing API keys page (contract 12) with the snippet and the new key filled in; a "waiting for the first event" state that turns "connected" when the project receives its first access.
- Fake engine (`edge/fake_engine/`): open org creation to any signed-in person (the real engine is Carlos's task 5).
- Docs: `docs/DISENO-PRODUCTO.md` / `docs/REPARTO.md` if a contract changes; `HANDOFF.md` progress.
- Out of scope: the real engine, the rest of the panel (Maikol), the snippet component design (Maikol owns it; reuse it).

## Constraints

- Styles from the design system (https://claude.ai/artifact/2JFynja7aQQqX1oJN6qmK2) and the existing panel templates; coordinate with Maikol.
- GitHub OAuth app credentials are created by a human; until configured, GitHub sign-in is hidden or disabled, never open.
- Code, comments, tests in English; team docs in neutral Spanish. Conventional Commits, no AI attribution, work on `main`.

## TDD

Mode: on. Source: `INIT.md` (same as the other edge task docs). Runner: `uv run pytest -q` in `edge/`.

## Tasks

- [ ] T1 Fake engine: org creation open to any signed-in person; persons identified by verified email across providers. Route: delegated (writer + preparation triggers: fake engine, panel auth, tests).
- [ ] T2 Panel: GitHub OAuth (`user:email`, primary verified email), same account as Google for the same email. Route: delegated.
- [ ] T3 Panel: first-run company-name step creating org + first project; people with an org never see it again. Route: delegated.
- [ ] T4 Panel: after first run, land on key creation with the snippet filled in; "waiting for the first event" → "connected". Route: delegated.

## Acceptance

- A new person signs in with Google or GitHub, creates their org and key without an invitation.
- A person who already has an org never sees the company-name step again.
- The same person through both providers is one account.
- `uv run pytest -q` in `edge/` passes.

## Progress

Created 2026-10-25. Next: T1.
