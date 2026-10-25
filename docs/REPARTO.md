# Reparto del trabajo

Hoy somos 2 devs; cada bloque está pensado para subdividirse cuando se sume el resto del equipo.

## Orden

1. **Contratos** (los dos devs juntos, ~30 min): cerrados el 2026-10-25 (ver abajo).
2. **Camino mínimo de punta a punta**, aunque sea feo: el atacante cambia la cuenta bancaria y queda bloqueado.
3. **Señales finas:** dispositivo, ritmo, recorrido.
4. **Lo que no es de la demo:** landing, docs, integraciones.

## Dev 1: Motor

Servidor central: recibe eventos, arma el perfil, consulta a la IA y publica bloqueos.

| Se divide luego en | Qué incluye |
|---|---|
| Perfil y señales | Perfil por usuario (red, dispositivo, horario, endpoints, volumen, recorrido, ritmo) y detección de desvíos |
| IA y juicio | Prompt, decisión (`allow` / `alert` / `step_up` / `block`), explicación, consistencia |
| Clasificación de endpoints | Leer OpenAPI y marcar cada ruta como `critical` / `sensitive` / `normal` |

## Dev 2: Borde y demo

Lo que toca la app del cliente y lo que se ve en el escenario.

| Se divide luego en | Qué incluye |
|---|---|
| Middleware/SDK | Enviar eventos en segundo plano y aplicar la lista de bloqueos |
| App de ejemplo + historial | Startup de facturas (login, facturas, cuenta bancaria de pagos) y el historial sembrado para la demo |
| Panel | Usuarios, estado, decisiones con su explicación y "esto era normal" |

## Para quienes se sumen después

- **Pitch:** landing, docs y slides.
- **Validación:** evaluación de la IA (ver `evals/key_judgment/` en Centinela) y ensayos de la demo.

## Contratos

**Estado:** cerrados el 2026-10-25. Cualquier cambio se propone al equipo y se actualiza aquí en el mismo commit.

### Reglas generales

- **Autenticación en todas las rutas:** `Authorization: Bearer <credencial>`. Hay dos tipos de llamante (ver `docs/DISENO-PRODUCTO.md`, "Cómo se autentica el panel contra el motor"):
  - **`sdk`:** la clave de API de un proyecto. Solo sirve en `POST /v1/events`, `POST /v1/check`, `GET /v1/blocklist` y `GET /v1/routes` (contratos 1, 2, 8 y 9) de **su** proyecto. En cualquier otro endpoint responde `403 {"error": "forbidden"}`: una clave filtrada ya no lee decisiones ni perfiles ni marca decisiones como normales.
  - **`panel`:** la credencial de servicio del panel (`VERIDIAN_PANEL_TOKEN`; `VERIDIAN_PANEL_TOKENS` admite la actual y la anterior para rotarla sin corte) más `X-Veridian-Actor: <person_id>` (la persona de la sesión). En las lecturas de datos (contratos 2 a 8) también `X-Veridian-Project: <project_id>`. El motor autoriza con el **rol del actor en la organización de ese proyecto**, leído de la base en cada petición; no hay roles por proyecto. Sin `X-Veridian-Project`: `400 {"error": "project_required"}`. Actor desconocido, no miembro, proyecto desconocido o eliminado: `403 {"error": "forbidden"}` (indistinguibles). La credencial del panel en `POST /v1/events` o `POST /v1/check`: `403`.
- **Organizaciones y roles:** los roles (`owner`, `admin`, `viewer`) viven solo en la organización: un rol por persona y organización, válido en todos sus proyectos; una persona puede estar en varias organizaciones. Leer datos: `viewer` o más. Marcar normal, editar un proyecto o la organización, crear proyectos y gestionar claves: `admin` o `owner`. Si un `viewer` puede marcar normal es la decisión abierta 3; hoy no (un solo interruptor en el motor).
- **Token de desarrollo:** el middleware lo lee de la variable `VERIDIAN_TOKEN`. El motor importa al arrancar cada token de `VERIDIAN_TOKENS` como clave de API (solo guarda su hash) y autentica contra la tabla `api_key`; el mismo token sigue funcionando. Nunca se sube al repo.
- **Errores:**
  - `401 {"error": "unauthorized"}` si falta el token o no es válido; es idéntico para clave inexistente, revocada o de un proyecto eliminado;
  - `429 {"error": "rate_limited"}` con cabecera `Retry-After` (segundos) tras demasiados fallos de autenticación desde la misma IP o con el mismo prefijo de clave; se aplica antes de validar la clave;
  - `403 {"error": "forbidden"}` si el llamante no puede usar ese endpoint o su rol no alcanza;
  - `400 {"error": "project_required"}` si el panel lee datos sin `X-Veridian-Project`;
  - `422` si el cuerpo no cumple el contrato.
- **Fechas:** `ts` siempre en UTC, ISO 8601 con `Z`.

### 1. Eventos: middleware → motor

`POST /v1/events`, en lotes, enviado en segundo plano cada ~2 s.

```json
{
  "events": [
    {
      "id": "e_01J9ZK3T",
      "ts": "2026-10-26T10:14:03Z",
      "user_id": "u_42",
      "session_id": "s_8f1",
      "ip": "190.202.14.7",
      "user_agent": "Chrome 141 / Windows",
      "device_id": "d_9f3",
      "method": "POST",
      "route": "/payouts/bank-account",
      "status": 200,
      "bytes": 512
    }
  ]
}
```

- **Respuesta:** `202 {"accepted": 1}`.
- **Idempotente por `id`:** reenviar un lote no cuenta dos veces.
- **`route` es la plantilla de la ruta** (`/invoices/{id}`), no la URL con IDs. Así el perfil agrupa bien.
- **Clasificación de rutas:** la hace el motor con `routes.yaml`, por método y plantilla (`"POST /payouts/bank-account"`); el middleware no la envía. Clasificar solo por plantilla haría `critical` también el simple hecho de ver la cuenta bancaria, que el dueño hace a menudo. Rutas de la app de ejemplo:

  | Método y ruta | Clase |
  |---|---|
  | `GET /login`, `POST /login` | `normal` |
  | `POST /logout` | `normal` |
  | `GET /invoices` | `normal` |
  | `GET /invoices/{id}` | `normal` |
  | `GET /invoices/export` (descarga todas las facturas) | `sensitive` |
  | `GET /payouts/bank-account` (ver la cuenta) | `sensitive` |
  | `POST /payouts/bank-account` (cambiar la cuenta) | `critical` |

- **`ip` es la del cliente real, y no la puede elegir el cliente:** `X-Forwarded-For` lo puede escribir cualquiera. Si el middleware tomara el primer valor, el atacante enviaría `X-Forwarded-For: <IP del dueño>` y la regla de red nueva no lo vería.
  - El middleware recibe `trusted_proxies` (cantidad de proxies propios delante de la app; `0` por defecto).
  - Con `0`, usa la IP de la conexión e ignora el encabezado.
  - Con `N`, toma el valor número `N` contando desde la derecha de `X-Forwarded-For`: el que agregó nuestro proxy más externo. Si el encabezado trae menos de `N` valores, usa el primero.
  - **Medido en Render (2026-10-25):** Render conserva el `X-Forwarded-For` del cliente y le agrega tres entradas, así que la app de ejemplo usa `trusted_proxies=3`. Con `2` el middleware tomaba una IP de Cloudflare (`172.71.x.x`), que comparten muchos usuarios; con `3` toma la IP real y los valores falsos del cliente se ignoran.
- **`device_id`:** cookie propia del middleware en el navegador del usuario. Puede venir vacío.
- **Nunca se envían** cuerpos de peticiones, contraseñas ni datos de clientes.

### 2. Bloqueos: motor → middleware

`GET /v1/blocklist?since=<version>`. El middleware la consulta cada ~2 s y la guarda en memoria.

```json
{
  "version": 17,
  "users": ["u_42"],
  "sessions": ["s_8f1"],
  "ips": ["203.0.113.9"]
}
```

- **Siempre es la lista completa,** nunca solo los cambios. Sin `since`, devuelve la versión actual.
- **Si `since` es la versión actual:** `304` sin cuerpo.
- **Un bloqueo frena al atacante, no al dueño:** el motor bloquea la `session_id` y la `ip` del acceso sospechoso. Si el acceso viene de una red conocida del dueño (`new_network` en `false`), solo bloquea la `session_id`: esa IP la comparte el dueño. `users` queda en el contrato para bloqueos de cuenta completa, pero el primer hito lo deja vacío, porque bloquear el `user_id` también deja afuera a la víctima.
- **Antes de pasar cada petición a la app,** el middleware revisa la lista y responde `403 {"error": "blocked"}` si hay coincidencia.
- **En rutas normales y sensibles se bloquea la siguiente petición, no la sospechosa:** por los ciclos de ~2 s, la petición que dispara el bloqueo ya pasó. Las rutas `critical` se consultan antes de ejecutarse (contrato 9).
- **Si el motor no responde,** el middleware sigue con la última lista conocida y nunca frena la app.

### 3. Decisiones: motor → panel

`GET /v1/decisions?limit=50`

```json
{
  "decisions": [
    {
      "id": "d_311",
      "ts": "2026-10-26T10:14:05Z",
      "user_id": "u_42",
      "decision": "block",
      "signals": {
        "new_network": true,
        "new_device": true,
        "route": "/payouts/bank-account",
        "route_class": "critical",
        "route_never_used": true,
        "path": ["/login", "/payouts/bank-account"],
        "rate_per_min": 2,
        "usual_max_rate_per_min": 6,
        "rate_spike": false,
        "new_transition": true,
        "unusual_hour": false
      },
      "rationale": "Red y dispositivo nunca vistos, y fue directo a cambiar la cuenta bancaria, algo que este usuario nunca hace.",
      "event_ids": ["e_01J9ZK3T"],
      "status": "open",
      "access": {
        "ts": "2026-10-26T10:14:03Z",
        "ip": "203.0.113.9",
        "device_id": "d_77a",
        "session_id": "s_8f1",
        "method": "POST",
        "route": "/payouts/bank-account"
      }
    }
  ]
}
```

- **`decision`:** `allow` | `alert` | `step_up` | `block`. Para la demo bastan `allow`, `alert` y `block`.
- **`status`:** en qué quedó la decisión, no qué se decidió. `open` (sigue en pie; en un `block`, el bloqueo está activo) | `marked_normal` (alguien la marcó como normal; en un `block`, el bloqueo se levantó). Así el panel distingue un bloqueo activo de uno levantado. Se pueden sumar valores más adelante (por ejemplo `expired`); el panel trata un valor desconocido como `open`.
- **Ritmo y camino en `signals`:** `rate_per_min` son los eventos de la sesión en los 60 s hasta el acceso; `usual_max_rate_per_min`, el máximo por minuto de cualquier sesión en lo aprendido; `rate_spike` es `true` si `rate_per_min` supera `max(VERIDIAN_RATE_FACTOR × usual_max_rate_per_min, VERIDIAN_RATE_FLOOR)` (3 y 30 por defecto, sin medir). Un `rate_spike` es desvío aunque la red y el dispositivo sean del dueño. `new_transition` (el par ruta anterior → ruta, como `"METHOD /ruta"`, nunca visto) y `unusual_hour` (hora UTC fuera de `hours`; `false` sin historial) son contexto para la IA y nunca disparan una decisión solos. Son claves nuevas: el panel ignora las que no conoce.
- **`access`:** el acceso que disparó la decisión (los campos del evento de `event_ids`, sin `user_agent` ni `bytes`). El panel lo compara lado a lado con el perfil: "lo normal para el usuario" frente a "este acceso". Es opcional: si falta, o falta un campo, el panel muestra "—".
- **Orden:** de la más nueva a la más vieja.
- **Feedback:** `POST /v1/decisions/{id}/normal` marca la decisión como uso normal y lo aprende el perfil. Si había un bloqueo, quita su sesión y su IP de la lista y sube la `version`.

### 4. Perfil: motor → panel

`GET /v1/users/{user_id}/profile`

```json
{
  "user_id": "u_42",
  "history_days": 14,
  "networks": ["190.202.14.0/24"],
  "devices": ["d_9f3"],
  "hours": [9, 13],
  "routes": ["GET /invoices", "GET /invoices/{id}"],
  "usual_max_rate_per_min": 6,
  "state": "blocked"
}
```

- **`usual_max_rate_per_min`:** el máximo de eventos en 60 s de cualquier sesión del usuario, con las mismas reglas de aprendizaje del perfil. `0` sin historial.

- **`routes` incluye el método** (`"GET /invoices"`), igual que la clasificación de rutas: `GET` y `POST` sobre la misma ruta son usos distintos.
- **`hours` va en UTC,** igual que `ts`: horas enteras de 0 a 23 (`[9, 13]` = de 09:00 a 13:00 UTC). El panel las convierte a la hora local de quien mira.

### 5. Actividad: motor → panel

`GET /v1/activity?limit=50`

Los últimos accesos del cliente, **todos**, no solo los que generaron una decisión. Sirve para que el panel muestre que Veridian vigila todo el tráfico y que la IA solo actúa ante lo raro (paso 1 del guion de la demo: "en el panel: normal").

```json
{
  "activity": [
    {
      "event_id": "e_01J9ZK3T",
      "ts": "2026-10-26T10:14:03Z",
      "user_id": "u_42",
      "session_id": "s_8f1",
      "ip": "203.0.113.9",
      "device_id": "d_77a",
      "method": "POST",
      "route": "/payouts/bank-account",
      "verdict": "block",
      "status": 200,
      "decision_id": "d_311"
    },
    {
      "event_id": "e_01J9ZK2A",
      "ts": "2026-10-26T10:13:58Z",
      "user_id": "u_42",
      "session_id": "s_7c2",
      "ip": "190.202.14.7",
      "device_id": "d_9f3",
      "method": "GET",
      "route": "/invoices",
      "verdict": "normal",
      "status": 200,
      "decision_id": null
    }
  ]
}
```

- **Orden:** de la más nueva a la más vieja. `limit` entre 1 y 200, por defecto 50.
- **`verdict`:** `normal` (sin decisión, o `allow`) | `review` (`alert` o `step_up`) | `block`. Si la decisión fue marcada como normal, vuelve a `normal`.
- **`decision_id`:** la decisión que generó ese acceso, o `null`. El panel enlaza la fila con el detalle.
- **Accesos posteriores a un bloqueo:** si hay un `block` abierto, salen como `block`, con el `decision_id` de ese bloqueo, todos los accesos desde el que lo disparó:
  - los de la misma sesión;
  - los de la misma IP, aunque vengan con otra sesión, si el bloqueo incluye la IP. Un bloqueo solo de sesión (red conocida, la IP es del dueño) no la cubre.

  Así el panel no muestra como `normal` al atacante ya bloqueado.
- **`status`:** el código HTTP que respondió la app, o el middleware (`403`) si la rechazó.
  - `200` en un `block`: la petición pasó antes de que el middleware recibiera la lista (se bloquea la petición siguiente).
  - `403`: el middleware la rechazó.

  Así el panel muestra cuál pasó y cuál se detuvo.
- **Lo que no incluye:** `user_agent` ni `bytes`; nunca cuerpos ni datos de clientes, igual que el contrato 1.

### 6. Resumen: motor → panel

`GET /v1/summary?since=<ISO>&until=<ISO>`

Los números de la portada del panel. El panel pide su "hoy" local (por ejemplo `since=2026-10-26T04:00:00Z` para Caracas); sin parámetros, las últimas 24 h.

```json
{
  "since": "2026-10-26T04:00:00Z",
  "until": "2026-10-27T04:00:00Z",
  "accesses": 46,
  "users": 2,
  "reviewed_by_ai": 2,
  "decisions": {"allow": 0, "alert": 1, "step_up": 0, "block": 1},
  "blocks_active": {"decisions": 1, "sessions": 1, "ips": 1},
  "by_hour": [
    {"hour": "2026-10-26T13:00:00Z", "accesses": 3, "blocks": 0},
    {"hour": "2026-10-26T14:00:00Z", "accesses": 11, "blocks": 1}
  ]
}
```

- **`accesses`:** todos los eventos del rango, incluidos los rechazados con `403`.
- **`reviewed_by_ai`:** los accesos del rango que el juez de IA evaluó, es decir, cada consulta real al modelo.
  - No es lo mismo que los desvíos: el motor consulta una vez por sesión y no repite lo ya juzgado.
  - Sin `ANTHROPIC_API_KEY` es `0`.
  - Es el argumento del costo: la mayoría nunca llega a la IA.
- **Rango:** `since` y `until` en ISO 8601 UTC. `since` tiene que ser anterior a `until`; si no, `422`.
- **`blocks_active`:** el estado actual, no el del rango: decisiones `block` con `status: open`, y cuántas sesiones e IPs hay en la lista de bloqueos.
- **`by_hour`:** una fila por hora UTC del rango que tenga accesos, en orden; `blocks` cuenta las decisiones `block` creadas en esa hora. El panel convierte a hora local.
- **Tiempo de detección:** no va aquí; el panel lo calcula de cada decisión (`ts` − `access.ts`).

### 7. Usuarios: motor → panel

`GET /v1/users`

```json
{
  "users": [
    {"user_id": "u_42", "state": "blocked", "history_days": 14, "networks": 1, "devices": 1, "last_seen": "2026-10-26T14:14:03Z", "open_blocks": 1},
    {"user_id": "u_17", "state": "normal", "history_days": 9, "networks": 2, "devices": 1, "last_seen": "2026-10-26T13:40:10Z", "open_blocks": 0}
  ]
}
```

- **Orden:** primero los que tienen bloqueos abiertos, después por `last_seen` de la más nueva a la más vieja.
- **`state`:** el mismo del contrato 4. `networks` y `devices` son cantidades; el detalle está en `GET /v1/users/{user_id}/profile`.

### 8. Rutas: motor → panel

`GET /v1/routes`

La clasificación de rutas que el motor está usando, para que el panel la muestre. Solo lectura.

```json
{
  "routes": [
    {"method": "POST", "route": "/payouts/bank-account", "class": "critical"},
    {"method": "GET", "route": "/invoices/export", "class": "sensitive"},
    {"method": "GET", "route": "/payouts/bank-account", "class": "sensitive"}
  ],
  "default": "normal"
}
```

- **Mismo contenido para todos los clientes:** hoy la clasificación es una sola (`routes.yaml`) y es global al motor. La credencial se exige igual (clave `sdk` o panel con `X-Veridian-Project` y rol `viewer`), pero no cambia la respuesta.
- **Orden:** primero `critical`, después `sensitive`; dentro de cada clase, por ruta y luego por método.
- **Solo lo clasificado:** las rutas `normal` no se listan. Cualquier ruta que no aparezca recibe la clase `default`.
- **`route`:** la plantilla, igual que en el contrato 1. El panel no puede editar la clasificación.

### 9. Consulta previa: middleware → motor (rutas críticas)

`POST /v1/check`, **síncrono**, solo para rutas `critical` según `GET /v1/routes` (contrato 8). El middleware lo llama **antes** de pasar la petición a la app y espera la respuesta.

```json
{"event": { ...mismo objeto que un evento del contrato 1... }}
```

Respuesta `200`:

```json
{"verdict": "block", "decision_id": "d_311"}
```

- **`verdict`:** `allow` (la app atiende la petición) o `block` (el middleware responde `403 {"error": "blocked"}` y la petición **nunca llega a la app**).
- **Cómo decide el motor:**
  1. Sin desvío (el dueño desde su red y su dispositivo): `allow` de inmediato, sin IA.
  2. Desvío obvio (ruta crítica nunca usada + red nueva): `block` de inmediato por regla. La IA escribe la explicación después.
  3. Desvío gris (por ejemplo red conocida con dispositivo nuevo): **la IA decide en ese momento**, con un tope de ~10 s. `block` o `step_up` → `block` (el middleware no pide segundo factor); `allow` o `alert` → `allow`. Si la IA falla o pasa el tope, decide la regla de respaldo del juez.
- **El evento queda registrado:** el motor lo guarda como si hubiera llegado por el contrato 1. El middleware lo vuelve a enviar en su lote normal sin problema: es idempotente por `id`. Si la respuesta es `block`, el motor lo guarda con `status: 403`, porque la app nunca lo ejecutó.
- **Un `block` también entra en la lista** (contrato 2): sesión e IP, o solo sesión si la red es conocida.
- **Timeout del middleware:** 12 s. Si el motor no responde, falla o da timeout, la petición **pasa** (*fail-open*): si Veridian se cae, la app sigue funcionando; esas rutas quedan sin protección mientras tanto.
- **Latencia:** el dueño legítimo casi nunca espera (sin desvío no hay IA). Solo espera lo sospechoso y crítico.

### Solo para ensayos: reinicio

`POST /v1/admin/reset` borra los eventos, las decisiones y lo aprendido del cliente dueño del token, y deja su lista de bloqueos vacía. No toca a otros clientes. Viene apagado: solo existe con `VERIDIAN_RESET_TOKEN` definido en el motor y el header `X-Veridian-Reset-Token` con ese valor (además del Bearer normal); si no, responde `404`. La `version` no vuelve a 0: sube en uno, así el middleware que consulta con `since=<versión vieja>` recibe la lista vacía y no un `304`. Responde `{"reset": true, "version": <n>}`.

El reinicio sigue usando la clave `sdk` del proyecto (no la credencial del panel).

### 10. Personas, organizaciones y proyectos: panel → motor

Solo llamante `panel`. Las formas son las del motor falso (`edge/fake_engine/app.py`), para que el panel funcione igual contra los dos.

| Endpoint | Rol | Respuesta |
|---|---|---|
| `POST /v1/people/login` `{"google_sub", "email", "name"?}` | solo credencial (sin actor) | `200 {"person": {"id", "email", "name"}, "orgs": [{"id", "name", "role", "projects": [{"id", "name"}]}]}` |
| `GET /v1/me/orgs` | actor conocido | `{"orgs": [...]}`, igual que en el login |
| `POST /v1/orgs` `{"name"}` | correo en `VERIDIAN_ORG_CREATORS` | `201`, la organización como en `orgs`, con el creador `owner` |
| `GET /v1/orgs/{id}` | `viewer` | la organización con su `role`, `projects` y `created_at` |
| `PATCH /v1/orgs/{id}` `{"name"?}` | `admin` | igual que `GET` |
| `GET /v1/orgs/{id}/projects` | `viewer` | `{"projects": [proyecto]}` |
| `POST /v1/orgs/{id}/projects` `{"name"}` | `admin` | `201`, proyecto. Empieza en `observe` (decisión 6) |
| `GET /v1/projects/{id}` | `viewer` | proyecto |
| `PATCH /v1/projects/{id}` `{"name"?, "retention_days"?, "enforcement"?, "ai_enabled"?}` | `admin` | proyecto; solo cambia lo enviado |

- **Proyecto:** `{"id", "org_id", "name", "retention_days", "enforcement", "ai_enabled", "created_at", "role"}`; `role` es el del actor en la organización.
- **Login:** alta o actualización por `google_sub` (el correo se guarda en minúsculas; sin `name` se conserva el anterior). Después: si el correo está en `VERIDIAN_BOOTSTRAP_OWNERS`, la persona pasa a `owner` de **cada organización sin owner** (nunca de una que ya lo tiene); y acepta las invitaciones vigentes de ese correo (una invitación nunca cambia el rol de quien ya es miembro).
- **Organización `Demo`:** los proyectos que ya existían (y los que se crean al arrancar desde `VERIDIAN_TOKENS` o desde datos existentes) están en la organización `Demo` (`org_demo`), sin miembros hasta que entra un bootstrap owner.
- **Validación:** `name` de 1 a 80 caracteres; `retention_days >= 1`; `enforcement` `observe` | `enforce`; si no, `422`.
- **Auditoría:** crear organización o proyecto, renombrar, cambiar un proyecto y el bootstrap quedan en `audit_log` con el actor.
- `DELETE /v1/projects/{id}` (solo owner): borrado lógico (`deleted_at`), responde `204` sin cuerpo y se anota en la auditoría (`project.delete`). El proyecto desaparece de las lecturas y sus claves del SDK reciben `401`. Los datos quedan hasta la purga de U13.
- Contrato 11 (miembros e invitaciones de la organización): el motor implementa las formas de `DISENO-PRODUCTO.md` ("Formas de respuesta de 11, 12 y 15"), igual que el fake engine.
  - `GET .../members` y `GET .../invitations`: cualquier miembro.
  - `PATCH`/`DELETE .../members/{person_id}`, `POST`/`DELETE .../invitations`: admin; solo un owner toca owners.
  - Errores: `404 not_found`, `409 cannot_change_self`, `409 last_owner`, `409 already_member`.
  - Reinvitar el mismo correo reemplaza la pendiente. La vigencia es `VERIDIAN_INVITATION_DAYS` (14 por defecto, no medido).
- Contrato 15 (auditoría, solo lectura): `GET /v1/orgs/{id}/audit?project=&limit=&before=`, solo admin+. Usa las formas y los nombres de acción del fake engine.
  - Devuelve `{entries, next_before}`, la más nueva primero; `limit` va de 1 a 200 (50 por defecto).
  - Las acciones del motor que el fake no tiene (`org.create`, `project.update`, `project.delete`, `invitation.accept`, `retention.purge`, etc.) salen con su nombre.
  - La migración `0007` renombra las filas escritas antes con los nombres viejos.

### 12. Claves de API: panel → motor

Solo `panel`, rol `admin` de la organización del proyecto de la ruta.

- `POST /v1/projects/{id}/api-keys` `{"name"}` → `201 {"id", "name", "prefix", "key"}`. **`key` se muestra solo esta vez**; el motor guarda su hash. Formato `vrd_<8 hex de prefijo>_<32 bytes aleatorios en base64url>`.
- `GET /v1/projects/{id}/api-keys` → `{"api_keys": [{"id", "name", "prefix", "created_by", "created_at", "last_used_at", "revoked_at"}]}`, de la más nueva a la más vieja, incluidas las revocadas y la importada de `VERIDIAN_TOKENS` (`prefix: "legacy"`). Nunca devuelve `key` ni su hash.
- `POST /v1/projects/{id}/api-keys/{key_id}/revoke` → `200` con la clave como en el listado. Vale desde la petición siguiente (`401`). Una clave ya revocada, o de otro proyecto: `404 {"error": "not_found"}`.
- Crear y revocar quedan en `audit_log`. `last_used_at` se escribe como mucho una vez por minuto por clave.

### 13. Webhook: panel → motor

Solo `panel`, rol `admin` de la organización del proyecto de la ruta. Un proyecto tiene como mucho un webhook. Cada decisión `block` encola una notificación firmada (carga y firma en `DISENO-PRODUCTO.md`, "Notificaciones de bloqueo").

| Endpoint | Respuesta |
|---|---|
| `GET /v1/projects/{id}/webhook` | `{"webhook": {"url", "enabled", "created_at"}}`, o `{"webhook": null}` si no hay. Nunca devuelve el secreto |
| `PUT /v1/projects/{id}/webhook` `{"url"}` | `200 {"url", "enabled", "created_at", "secret"}`. Crea o reemplaza, queda activo y con un secreto nuevo. **`secret` se muestra solo esta vez** |
| `PATCH /v1/projects/{id}/webhook` `{"enabled"}` | `{"webhook": {...}}`; sin webhook, `404` |
| `DELETE /v1/projects/{id}/webhook` | `204`; sin webhook, `404`. Las notificaciones pendientes quedan `failed` sin enviarse |
| `GET /v1/projects/{id}/webhook/deliveries?limit=50` | `{"deliveries": [{"id", "kind", "decision_id", "status", "attempts", "next_attempt_at", "last_error", "created_at", "updated_at"}]}`, de la más nueva a la más vieja. `limit` de 1 a 200 |
| `POST /v1/projects/{id}/webhook/test` | `200 {"delivered", "status_code", "error"}`; sin webhook, `404` |

- **URL:** `https`, sin usuario ni contraseña, y el nombre tiene que resolver solo a direcciones públicas. Si no: `422 {"error": "unsafe_url", "detail"}` y no se guarda nada. Hasta 2048 caracteres.
- **Secreto:** formato `whsec_...`. El motor lo guarda cifrado con `VERIDIAN_SECRET_KEY`. Sin esa variable, `PUT` y `test` responden `503 {"error": "secret_key_missing"}`.
- **`status`:** `pending` (en cola o esperando reintento), `delivered` o `failed`.
- **Proyectos en `observe`:** también notifican. La carga lleva `decision.enforced: false` (el bloqueo se registró, pero no se aplicó); en `enforce`, `true`.
- **Prueba:** envía en el momento un evento `webhook.test` (cabecera `X-Veridian-Event: webhook.test`), firmado igual que un bloqueo: `{"id", "type": "webhook.test", "created_at", "project": {"id", "name"}}`. No pasa por la cola, no se reintenta y no aparece en `deliveries`. Un receptor que falla no es un error de la API: `delivered: false` con el motivo.
- **Auditoría:** `webhook.set` (con la URL, nunca el secreto), `webhook.enable`, `webhook.delete` y `webhook.test` quedan en `audit_log` con el actor.

### Marcar normal (contrato 3)

`POST /v1/decisions/{id}/normal` es solo `panel` con `X-Veridian-Project` y rol `admin` u `owner`; queda en `audit_log` (`decision.mark_normal`, con el actor) en la misma transacción. Un `viewer` recibe `403`; una decisión inexistente, `404`.

## Primer hito de cada dev

- **Dev 1:**
  - `POST /v1/events` guarda los eventos;
  - un perfil mínimo (redes y rutas usadas);
  - si una ruta `critical` nunca usada llega desde una red nueva → `block` en `/v1/blocklist`.

  Con eso ya funciona la demo, todavía sin IA.
- **Dev 2:**
  - la app de ejemplo con login y la pantalla de cuenta bancaria;
  - el middleware enviando eventos y aplicando la lista;
  - un panel que lista `/v1/decisions`.
