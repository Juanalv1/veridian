# Panel routes editable (per project)

## Objective
Route classification is per project and editable from the panel `/rutas`; new projects do not inherit the demo routes.

## Problem
`/rutas` was read-only and every new project (e.g. "elve-lorio") was seeded with `engine/routes.yaml`, the demo app's routes.

## Scope / decisions
- Engine already stored rules per project (`route_rules`, migration 0008) with a full-replace `PUT /v1/projects/{id}/routes`. No new migration needed.
- New projects start empty (`replace_rules(..., {})`, marked seeded). `routes.yaml` is only seeded at startup into pre-existing unseeded projects (the legacy demo projects).
- New endpoints (role `manage_routes` = admin/owner, audited): `PUT /v1/projects/{id}/routes/rule` (`routes.rule_set`), `DELETE /v1/projects/{id}/routes/rule?method=&route=` (`routes.rule_deleted`; keeps an admin `normal` rule for catalog routes via `protect_unlisted`).
- No version bump: the SDK re-reads `GET /v1/routes` every 30 s (`ROUTES_POLL_INTERVAL`).
- Not done: projects created before this change in a live DB keep their template rules; an admin removes them from `/rutas`.

## Tasks
- [x] T1 Engine: empty new projects, rule set/delete endpoints, tests (inline). 
- [x] T2 Fake engine mirror + panel `/rutas` editable + panel tests (inline).
- [x] T3 Docs (REPARTO contract 8, DISENO-PRODUCTO, engine README).

## Verification
- Engine RED: 9 failures in `tests/test_project_routes.py` before implementation; GREEN: `cd engine && .venv/Scripts/python -m pytest -q` -> 707 passed.
- Panel RED: 6 of 7 in `edge/tests/test_panel_routes.py` failed; GREEN: `cd edge && .venv/Scripts/python -m pytest -q` -> 805 passed, 1 failed (`test_panel_perf.py::test_engine_calls_overlap_when_the_user_is_in_the_url`, timing; passes alone, 14/14, with and without this change).

## Commits
- 42976b5 feat(engine): per-project rule endpoints, new projects empty (T1)
- fc1b808 feat(panel): /rutas editable, fake engine mirror, docs (T2, T3)
