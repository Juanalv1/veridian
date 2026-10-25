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

- **Autenticación en todas las rutas:** `Authorization: Bearer <token del cliente>`. Cada cliente está aislado.
- **Token de desarrollo:** el middleware lo lee de la variable `VERIDIAN_TOKEN`, y el motor acepta los tokens que tenga configurados. Nunca se sube al repo.
- **Errores:**
  - `401 {"error": "unauthorized"}` si falta el token o no es válido;
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
- **Se bloquea la siguiente petición, no la sospechosa:** por los ciclos de ~2 s, la petición que dispara el bloqueo ya pasó. Una consulta síncrona para rutas `critical` queda como mejora, fuera del primer hito.
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

### Solo para ensayos: reinicio

`POST /v1/admin/reset` borra los eventos, las decisiones y lo aprendido del cliente dueño del token, y deja su lista de bloqueos vacía. No toca a otros clientes. Viene apagado: solo existe con `VERIDIAN_ALLOW_RESET=true`; si no, responde `404`. La `version` no vuelve a 0: sube en uno, así el middleware que consulta con `since=<versión vieja>` recibe la lista vacía y no un `304`. Responde `{"reset": true, "version": <n>}`.

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
