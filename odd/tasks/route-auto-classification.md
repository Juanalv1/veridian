# Automatic route classification (Dev B, HANDOFF item 6)

The SDK reports the app's routes at startup; the engine classifies them with the AI per project and serves them in `GET /v1/routes`.
Branch: `main` (no PRs). Delivery: work-unit commits. Tests: `engine/.venv/bin/python -m pytest -q` (engine), `edge` pytest (SDK).

## Specs

- S1: "al arrancar, el SDK envía al motor las rutas de la app (método, ruta y descripción, sacadas del OpenAPI que ya expone FastAPI)". "Solo rutas, nunca código fuente."
- S2: "El motor las clasifica con la IA (`veridian_engine.openapi.classify`), guarda el resultado por proyecto y lo sirve en `GET /v1/routes`, que el middleware ya consulta."
- S3: "clasificar solo las que cambiaron desde la última vez, no todas en cada arranque."
- S4 (user decision, 2026-10-25): if the AI fails, the route keeps its last known class and is retried on the next SDK report. A new route the AI could not classify stays `normal` and is shown as pending.
- S5 (user decision, 2026-10-25): an admin correction (the U7 `PUT`) wins; the AI never overwrites it. Assumption: rules seeded from `routes.yaml` (human-reviewed) are protected the same way.
- S6: "Contrato nuevo en `REPARTO.md` para el envío de rutas (SDK → motor)."
- S7: tests: "el SDK envía las rutas al arrancar; el motor las clasifica y las sirve por proyecto; una falla de la IA no borra la clasificación anterior; dos proyectos no ven las rutas del otro."
- S8 (found while exploring): the classifier prompt calls login `critical`, a listed failed approach (blocks the first login from a new network). Login itself must not be `critical`; credential changes still are.

## Tasks

| ID | Specs | Route | Status |
|---|---|---|---|
| T1 | Engine: catalog table, contract 16 endpoint, classifier worker, source-aware rules, panel catalog read, prompt fix | inline | [x] RED 22 failed, GREEN 684 passed (3 failing are Dev A U13 WIP) |
| T2 | SDK: report routes from the app's OpenAPI at startup; fake engine accepts it | inline | [ ] |
| T3 | Docs: contract 16 in `REPARTO.md`, HANDOFF | inline | [ ] |

## Log

- L1 (2026-10-25, user, verbatim): "tarea nueva en el HANDOFF (Dev B, punto 6): clasificación automática de rutas. [...] Depende de U7 de Maikol (route_rules por proyecto), así que coordinalo con él antes de arrancar. Hay dos cosas por decidir: qué pasa si la IA falla (hoy la ruta queda normal y se apaga la regla de bloqueo) y si las correcciones del admin sobreviven a una nueva clasificación. [...] El pitch ya lo presenta como automático, así que es importante para la demo."
- L2 (2026-10-25, user): "dale guaya" — accepted S4 and S5 as proposed.
- L3: U7 is already on `main` (`route_rules`, `PUT /v1/projects/{id}/routes`), so this builds on it without blocking Dev A.
