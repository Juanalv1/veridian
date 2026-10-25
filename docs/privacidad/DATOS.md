# Inventario de datos personales

Estado a 2026-10-25. "Hoy" es lo que hace el código; "planeado" es el diseño (`docs/DISENO-PRODUCTO.md`, "Retención y borrado", contrato 14) y **no existe aún**.

## Retención hoy

**No hay retención ni borrado automático.** Ningún dato se borra con el tiempo. La única forma de borrar es `reset_client` (`engine/veridian_engine/decisions.py:140-149`), expuesta en `POST /v1/admin/reset` solo si `VERIDIAN_RESET_TOKEN` está definido y se envía en `X-Veridian-Reset-Token` (secreto de ensayos de la demo, `sync: false` en `render.yaml`). Borra todo un cliente, no un usuario.

## Tablas del motor (`engine/veridian_engine/db.py`)

| Tabla | Datos personales | Finalidad |
|---|---|---|
| `events` | `user_id`, `session_id`, `ip` (completa), `user_agent`, `device_id`, `method`, `route`, `ts` | Aprender el perfil y evaluar cada acceso |
| `decisions` | `user_id`, `session_id`, `ip` (vacía si la red es conocida), `signals`, `rationale` | Registro de decisiones y lista de bloqueo |
| `decision_events` | `decision_id`, `event_id` (enlaza a `events`) | Evidencia de cada decisión |
| `rate_spike_judgments` | `session_id` | Juzgar una vez el primer pico de ritmo |
| `ai_judgments` | `event_id`, `ts` | Contar llamadas a la IA |
| `blocklist_versions` | Ninguno | Versión de la lista de bloqueo |

Notas:

- Los perfiles no se guardan: se calculan al leer desde `events` (`engine/veridian_engine/profile.py`), así que borrar eventos borra lo aprendido.
- `decisions.signals` contiene la ruta y las rutas de la sesión (`rules.py:88-101`); `rationale` puede nombrar la ruta y la red /24 (`rules.py:11-14`).
- `session_id` cuando el cliente no lo define: el SDK envía `s_` + los primeros 16 caracteres hexadecimales del SHA-256 de la primera cookie de sesión conocida (`sessionid`, `connect.sid`, `sid`, `session_id`, `PHPSESSID`, `JSESSIONID`) o, si no hay ninguna, del token `Authorization: Bearer`. El valor de la cookie o del token nunca sale de la app (`sdk/python/src/veridian_sdk/middleware.py:38`, `47-60`). Es un hash sin sal: quien tenga el valor original puede reconocerlo, pero no recuperarlo a partir del hash.
- Límites del evento: `user_agent` hasta 512 caracteres en el motor (`events.py:16`); el SDK envía los primeros 200 (`sdk/python/README.md:77`).

## Cookie del SDK

| Dato | Detalle |
|---|---|
| Nombre | `vrd_did` (`sdk/python/src/veridian_sdk/middleware.py:27`) |
| Valor | `d_` + 12 caracteres hexadecimales aleatorios, generado por el SDK (`middleware.py:285-288`) |
| Atributos | `Path=/; HttpOnly; SameSite=Lax; Max-Age=` un año (`middleware.py:28`, `310-311`) |
| Finalidad | Reconocer el dispositivo habitual del usuario (señal `new_device`) |
| Dónde se guarda | Navegador del usuario final; el valor se copia a `events.device_id` |
| No se emite | A un cliente bloqueado (`middleware.py:278-282`) |

La cookie la pone la app del cliente, no Veridian: el cliente debe declararla en su aviso de cookies. Pendiente de revisión legal si requiere consentimiento (no se evalúa aquí).

## Retención y borrado

| Aspecto | Hoy | Planeado (diseño, contrato 14) |
|---|---|---|
| Retención por proyecto | Ninguna | `projects.retention_days`, editable por owner y admin |
| Valor por defecto | No aplica | 90 días, **provisional y sin medición**; mínimo sin fijar (decisión abierta 5) |
| Purga automática | No existe | Hilo diario en lotes: borra `events` y `decisions` antiguos (salvo bloqueo abierto) y sus filas auxiliares; registra en `AuditLog` solo el conteo |
| Borrado por usuario | No existe | `DELETE /v1/projects/{id}/users/{user_id}`: borra eventos, decisiones y filas auxiliares; sube la versión del blocklist; audita con el hash del `user_id` |
| Borrado de proyecto | Solo `reset_client` (ensayos) | `DELETE /v1/projects/{id}/data` (owner); proyecto con `deleted_at` se purga tras un plazo de gracia configurable (plazo sin definir) |
| Auditoría | No existe | Se conserva mientras exista el proyecto, con hash del `user_id` (decisión abierta 12) |
| Copias de seguridad | **No verificado** si el disco de Render tiene copias ni cuánto duran | Por definir: un borrado no alcanza copias que existan |

## Datos de IA

Los campos que salen hacia Anthropic están en `SUBENCARGADOS.md`. No se guardan en una tabla aparte; Anthropic puede conservar la solicitud según sus términos (no verificado).
