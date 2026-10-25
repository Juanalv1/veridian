# Subencargados del tratamiento

Estado a 2026-10-25, verificado contra el código y `render.yaml`. No es texto legal.

## Lista

| Proveedor | Finalidad | Ubicación | Datos personales que recibe | Verificación |
|---|---|---|---|---|
| Render | Aloja el motor y su disco (`veridian-data`, SQLite) | Región `oregon` (`render.yaml`, `region: oregon`) | Todo lo que guarda el motor: ver `DATOS.md` | Verificado en `render.yaml` |
| Anthropic | Juez de IA que decide sobre una desviación | No verificada | Los campos de la sección siguiente | Código verificado; ubicación y contrato no verificados |

Solo se envía a Anthropic si `ANTHROPIC_API_KEY` está definida; sin ella el motor funciona solo con reglas (`engine/veridian_engine/settings.py`, `default_judge`).

## Campos enviados a la IA (verificado)

Envío único: `_data_block` serializa `{"signals": ..., "profile": ...}` como JSON dentro del mensaje de usuario (`engine/veridian_engine/ai/judge.py:123-133`, `144-148`) y lo manda con `client.beta.messages.create` (`judge.py:149-164`). El prompt de sistema (`judge.py:52-78`) no lleva datos del cliente. Los dos argumentos los arma `evaluate_event` (`engine/veridian_engine/rules.py:88-107`) y se pasan al juez en `judging.py:32` y `check.py:93-94`.

**`signals`** (`rules.py:88-101`):

| Campo | Contenido |
|---|---|
| `new_network`, `new_device`, `route_never_used`, `rate_spike`, `new_transition`, `unusual_hour` | Booleanos |
| `route` | Ruta del evento (plantilla si el emisor es el SDK) |
| `route_class` | `critical`, `sensitive` o `normal` |
| `path` | Rutas de todos los eventos de la sesión hasta el evento actual (`rules.py:94`; `session_events`, `rules.py:35-40`) |
| `rate_per_min`, `usual_max_rate_per_min` | Enteros |

**`profile`** (`engine/veridian_engine/profile.py:52-62`):

| Campo | Contenido |
|---|---|
| `user_id` | Identificador del usuario final, en claro |
| `history_days` | Número de días con historia |
| `networks` | Redes /24 (IPv4) o /64 (IPv6) conocidas del usuario (`profile.py:35-38`), no IP completas |
| `devices` | Lista de `device_id` conocidos (valor de la cookie) |
| `hours` | Horas UTC habituales |
| `routes` | Claves `MÉTODO /ruta` conocidas |
| `usual_max_rate_per_min`, `state` | Entero; `blocked` o `normal` |

**No se envían** (no aparecen en `signals` ni en `profile`): `session_id`, IP completa del evento, `user_agent`, `ts`, `status`, `bytes`, `client_id`. Verificado leyendo ambos diccionarios; el evento completo no se pasa al juez (`judging.py:32`).

Modelos: `claude-opus-5-5` por defecto (`judge.py:21`) o `VERIDIAN_MODEL`; `claude-haiku-5-5` en `POST /v1/check` o `VERIDIAN_SYNC_MODEL` (`settings.py:90`).

## Cosas a vigilar

- La ruta llega como plantilla solo si el emisor es el SDK de Python (`sdk/python/README.md:53`). El motor solo exige que empiece por `/` (`events.py:19`); un emisor propio podría mandar rutas con valores reales, y esas rutas irían a la IA. Pendiente decidir si se valida.
- `user_id` va en claro a Anthropic. Si el cliente lo usa como correo o documento, es dato personal directo. Pendiente decidir si se envía un seudónimo.
- "No se entrena con datos del cliente" está decidido como diseño en `docs/DECISIONES.md` (línea 30). **No verificado** que los términos de Anthropic aplicables a nuestra cuenta lo garanticen, ni su política de retención: falta revisar el contrato.
- La llamada pide `fallbacks="default"` (`judge.py:162-163`). **No verificado** qué modelo y qué ubicación usa ese reemplazo.

## No son subencargados (hoy)

| Elemento | Motivo |
|---|---|
| PyPI | Distribuye el SDK; no recibe datos de usuarios finales |
| Cloudflare Worker `ops/keepalive` | Solo hace GET a `/login`, `/`, `/health` de nuestros servicios (`ops/keepalive/src/index.js`); no envía ni recibe datos personales. Es operación interna |

## Previstos, aún no existen

| Proveedor | Estado |
|---|---|
| Google (login del panel) | Previsto en el diseño; recibiría el correo de quien entra al panel |
| Proveedor de correo | Decisión abierta 4 |

## Sin verificar

- Dónde se alojan el panel, la demo y la landing: los pings de `ops/keepalive/src/index.js` apuntan a `*.onrender.com`, pero `render.yaml` solo define el motor. Confirmar en Render antes de listarlos.
