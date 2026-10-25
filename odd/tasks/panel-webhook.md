# Webhook del proyecto en el panel (T6, parte 1)

**Objetivo:** que un admin u owner configure en el panel la URL a la que Veridian avisa cuando bloquea un acceso (contrato 13), vea el secreto de firma una sola vez, pause, elimine, envíe una prueba y vea los envíos recientes.

## Tareas

- [x] W1 — Contrato 13 en el motor falso (`edge/fake_engine/app.py`): GET/PUT/PATCH/DELETE `/v1/projects/{id}/webhook`, `GET .../deliveries`, `POST .../test` simulado (sin red, sobrescribible con `app.state.webhook_test_result`), validación de URL sin DNS, entrega registrada al crear un bloqueo con webhook activo, auditoría `webhook.set|enable|delete|test` sin secreto. Ruta: inline (un archivo).
- [x] W2 — Página "Webhook" del panel (`edge/panel/team.py`, `templates/webhook.html`, nav en `main.py` + icono en `macros.html`, `.pill.st-failed` en `panel.css`), siguiendo el patrón de Claves de API. Auditoría: el filtro "Qué" incluye Webhook.

## Checks

- `cd edge && uv run pytest tests/test_fake_engine_webhook.py -q`
- `cd edge && uv run pytest tests/test_panel_webhook.py -q`
- `cd edge && uv run pytest -q -p no:warnings`

## Evidencia

- RED: motor falso 24 fallidos; panel 16 fallidos, 1 pasado.
- GREEN: motor falso 24 pasados; panel 17 pasados. Suite completa: ver abajo.

## Siguiente paso

Commit de la unidad (lo hace quien coordina).
