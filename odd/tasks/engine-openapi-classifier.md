# engine-openapi-classifier

## Objective
Read the customer's OpenAPI 3 document and classify each `METHOD /path-template` as `critical` / `sensitive` / `normal` with a one-line reason (docs/DECISIONES.md, docs/REPARTO.md Dev 1).

## Interface
- `extract_operations(spec: dict) -> list[Operation]`: pure code, document order, ignores path-item keys that are not HTTP methods.
- `classify(operations, *, client=None, model=None, chunk_size=100) -> dict[str, RouteClass]`, keyed `"METHOD /path"`; one request per chunk; model from `VERIDIAN_MODEL` (default `claude-opus-5-5`); key only from `ANTHROPIC_API_KEY`. Never raises: missing/invalid entries, refusals, bad output, API errors or missing key degrade to `normal` with the cause in `reason`.
- `engine/scripts/classify_openapi.py <spec> [--out file]`: plain YAML mapping `"METHOD /path": class  # reason`.

## Tasks
- [x] T1 Extraction + fixture modeled on the demo app (route: delegated worktree writer).
- [x] T2 Classification with structured output, chunking, fallbacks, untrusted data block.
- [x] T3 CLI script.
- [ ] T4 Integrate output with the core engine's `routes.yaml` format (parent, at merge).

## Checks
- `cd engine && .venv/Scripts/python -m pytest tests/openapi -q` -> 17 passed.
- RED observed before implementation (ModuleNotFoundError; then missing-key test failing before the key guard).
- Live smoke call: skipped (`ANTHROPIC_API_KEY` not set).

## Progress
T1-T3 done on the worktree branch. Next: T4 at integration.
- 2026-10-25: wiring: `classify_openapi.py` now writes a file with the same shape as `routes.yaml` (loadable by the engine) behind a review header, and reads `.env`. Live run on the demo app spec (8 routes, generated in-process): the same classes in 5 of 5 runs, ~16 s each. It differs from the hand-written file: `GET /invoices` and `GET /invoices/{id}` sensitive (hand: normal) and `POST /login` critical (hand: normal). `routes.yaml` stays hand-reviewed: `POST /login` as critical would block any first login from a new network.
