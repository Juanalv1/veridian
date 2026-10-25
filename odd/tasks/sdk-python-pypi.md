# Python SDK on PyPI

## Objective

Anyone can install the Veridian middleware with `pip install veridian-sdk`, published from this public repo through GitHub Actions trusted publishing.

## Problem

The middleware lives in `edge/middleware/` inside the `veridian-edge` project (`packages = []`), next to the demo, panel and fake engine. It cannot be installed on its own.

## Scope (authorized)

- New package `sdk/python/` (`veridian-sdk`, import `veridian_sdk`) holding the middleware; dependencies limited to what it imports (`httpx`, `starlette`).
- `edge` depends on it through a uv path source; demo and tests import from `veridian_sdk`; `edge/middleware/` removed.
- Workflow `.github/workflows/publish-sdk-python.yml`, triggered by tags `sdk-py-v*`, environment `pypi`, trusted publishing (no token).
- Docs: `edge/README.md` (Render notes), SDK README.
- Out of scope: other languages, PyPI release automation beyond the tag, changing middleware behavior.

## Constraints

- Render demo and panel must keep deploying. Done (2026-10-25, via API): both services now use repo root (`rootDir: ""`), build `cd edge && pip install uv && uv sync --frozen --no-dev`, start `cd edge && .venv/bin/uvicorn ...`. Build filters: demo `edge/**` + `sdk/python/**`, panel `edge/**`. Both redeployed live with the current code (demo `/login` 200, panel `/` 200).
- PyPI pending publisher configured by the user: project `veridian-sdk`, owner `Juanalv1`, repo `veridian`, workflow `publish-sdk-python.yml`, environment `pypi`.
- Pushing the tag publishes an immutable version: only after the user confirms.
- Code, comments, tests in English; team docs in neutral Spanish. Conventional Commits, no AI attribution, work on `main`.

## TDD

Mode: on. Source: `INIT.md` (same as `edge-first-milestone.md`). Runner: `uv run pytest` in `edge/`. This is a move with no behavior change: the existing middleware tests are the safety net (must pass unchanged except the import).

## Tasks

- [x] T1 Render: demo and panel see the whole repo. Route: inline (API calls). Evidence: above.
- [ ] T2 Package `sdk/python/`, wire `edge` to it, publish workflow, docs. Route: delegated (writer trigger: pyproject x2, lock, demo, tests, workflow, READMEs).
- [ ] T3 Tag `sdk-py-v0.1.0` after user confirmation; verify the PyPI page and a clean `pip install veridian-sdk`.

## Acceptance

- `uv run pytest -q` in `edge/` passes.
- `uv build` in `sdk/python/` produces a wheel and sdist; the wheel installs in a clean venv and `from veridian_sdk import VeridianMiddleware` works.
- Demo redeploys live after the push.
