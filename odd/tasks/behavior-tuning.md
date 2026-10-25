# Behavior testing and tuning (Dev B, HANDOFF task 7)

Make Veridian decide well outside the demo script: measure legitimate-but-unusual uses and attacks against the real engine and AI, fix what fails, pin each fix with a test.
Branch: `main`. Delivery: work-unit commits. Tests: `engine/.venv/bin/python -m pytest -q`. Measurement: `engine/scripts/behavior_eval.py` against production (throwaway project).

## Specs

- S1: "Prueba usos legítimos pero raros que no deberían bloquearse (viaje, dispositivo nuevo, de noche, VPN, IP que rota)".
- S2: "y ataques que sí (otra red a una ruta crítica, descarga masiva, robo lento, script desde la red del dueño, sesión robada)."
- S3: "Ajusta lo que falle: umbrales del perfil y del ritmo, el prompt de la IA, los tiempos de la consulta síncrona y la regla de respaldo."
- S4: "Mide decisión, consistencia entre corridas y tiempo en cada caso, y anótalo en docs/HALLAZGOS.md y docs/PENDIENTES.md."
- S5: "Cada ajuste, con un test que fije el caso."

## Tasks

| ID | Specs | Route | Status |
|---|---|---|---|
| T1 | Evaluator script and baseline measurement (12 cases × 3 runs) | inline | [x] 9/12 as expected |
| T2 | Fixes for the failing cases, each with a pinning test | inline | [x] A3 `d20663e`; A7 and L7 `eb6585d`; L1b/L2b block by decision, pinned in `6d0125e` |
| T3 | Re-measure, write HALLAZGOS (validation) and PENDIENTES (remaining risks) | inline | [x] 17/17 × 3 runs |

## Log

- L1 (2026-10-25, user, verbatim): "Christian, te asigné pruebas y ajuste del funcionamiento (tarea 7 de tu sección en el HANDOFF). La idea es que Veridian decida bien fuera del guion de la demo; el ensayo de la demo lo tiene Carlos. - Prueba usos legítimos pero raros que no deberían bloquearse (viaje, dispositivo nuevo, de noche, VPN, IP que rota) y ataques que sí (otra red a una ruta crítica, descarga masiva, robo lento, script desde la red del dueño, sesión robada). - Ajusta lo que falle: umbrales del perfil y del ritmo, el prompt de la IA, los tiempos de la consulta síncrona y la regla de respaldo. - Mide decisión, consistencia entre corridas y tiempo en cada caso, y anótalo en docs/HALLAZGOS.md y docs/PENDIENTES.md. Cada ajuste, con un test que fije el caso."
- L2: already fixed before this task (commit on main, 2026-10-25): the owner's first use of a critical route from a known network and device was AI-blocked 3/3; it is no longer a deviation.
- L3: baseline against a local engine on `main` with the production AI (prod runs were cut by teammates' engine deploys). Failing: A3 slow theft (AI step_up, never enforced), L1b travel + bank change (critical rule), L2b new device + bank change (AI block). A3 fixed by enforcing background step_up as block, re-measured 3/3.
- L4 (2026-10-25, user, verbatim): "lo de la tarea 7 hazlo de una guaya"
- L5: since `42976b5` new projects start with every route normal, so the evaluator now PUTs the demo app's classes. Added cases L6 (busy page), L7 (sensitive read abroad), A6 (attacker on the owner's /24), A7 (returning attacker), N1 (no history). L1b/L2b now expect a block: Juan's self-unlock decision (`docs/DESBLOQUEO-POR-CORREO.md`) covers the owner's way out, and both look like A5/A6.
- L6: run 1 (local engine, production AI): 16/17. L7 blocked 3/3 (the judge never saw the HTTP method and read a sensitive GET as the critical change). A7 passed only because the AI blocked: the attacker's late-delivered session had learned itself. Fixed both in `eb6585d` (signals carry `method`, prompt keeps reads apart; a session's events within the learn delay of the judged event never vouch for it). A first attempt (judging against the profile at the event's own time) made seeded history self-block; rejected.
- L7: run 2: 17/17, same decision in 3/3 runs of every case. Engine suite 797 passed, edge 824. Written to HALLAZGOS ("Validación del motor real") and PENDIENTES (no-history users, rate floor with real SPAs). Manual guide: `docs/PRUEBAS-MANUALES.md`.
