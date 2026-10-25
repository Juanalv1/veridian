# Agent-oriented SDK install

## Objective

A developer can hand the Veridian install to a coding agent (Claude Code, Cursor, Codex...) and get a working, verified integration: an install guide written for agents, and a "copy prompt for your agent" button next to the snippet.

## Problem

The SDK docs (READMEs, landing, panel snippet) are written only for humans. There is no `llms.txt`, no agent install guide and no prompt to hand to an agent (checked 2026-10-25).

## Scope (authorized by the user, 2026-10-25: "haz los puntos 1 y 2")

1. Agent install guide, one source of truth, served by the landing as `/llms.txt` and `/install.md` (static files in `landing/`): detect the stack (Python FastAPI/Starlette, Node Express, Next.js route handlers), install the package, configure `VERIDIAN_API_KEY` in env, wire the `user` function from the app's own auth, `trusted_proxies`, run, and verify (panel shows "Conectado"). Imperative, verifiable steps; stop conditions; what not to touch.
2. "Copiar prompt para tu agente" button next to the snippet in the landing and in the panel (the shared snippet partial). The prompt points the agent to the guide URL and states the stack-independent goal.

## Constraints

- The API key never goes into the prompt: pasting it into an agent sends it to a third-party model. The prompt tells the agent to read it from `VERIDIAN_API_KEY` and to ask the human to set it in the gitignored env file. (Decision taken by the orchestrator for a security product; reported to the user.)
- Reuse existing landing/panel components and design tokens (Maikol owns visuals); no new visual language.
- Guide content must match the real SDK APIs (`sdk/python`, `sdk/node`); `npm install veridian-sdk` works only once 0.1.0 is approved on npm (pending, staged publish).
- Code, comments, tests and agent guide in English; UI copy in neutral Spanish. Conventional Commits, no AI attribution, on `main`.

## TDD

Mode: on. Runner: `uv run pytest -q` in `edge/` (panel). Landing is static: structural checks.

## Tasks

- [ ] T1 Agent install guide (`landing/install.md`, `landing/llms.txt`), linked from the root README and both SDK READMEs. Route: delegated (writer: guide + 3 READMEs).
- [x] T2 Copy-prompt button in the landing and the panel snippet partial, with a test in `edge/tests`. Route: delegated (same writer).

## Acceptance

- The guide's commands and code match the SDKs' actual APIs.
- Both buttons copy the same prompt text; the prompt contains no API key.
- `uv run pytest -q` in `edge/` passes.

## Progress

Created 2026-10-25. Next: T1.

## Evidence

- T2 (commit below): tests written first (6 failed = RED), then button in `landing/index.html` (codecard foot, `.copy` class, label kept by `install.js`) and `edge/panel/templates/partials/snippet.html` (own step row, `data-agent-prompt`, `data-copy`). Prompt has no key; `edge/tests/test_agent_prompt.py` checks panel, one-time reveal page and landing/panel text equality. `uv run pytest -q` in `edge/`: 798 passed.
