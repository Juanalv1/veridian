# Reparto del trabajo

Hoy somos 2 devs; cada bloque está pensado para subdividirse cuando se sume el resto del equipo.

## Orden

1. **Contratos** (los dos devs juntos, ~30 min): los de abajo, en borrador. Se cierran antes de escribir código.
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

## Contratos (borrador)

Autenticación en todas las rutas: `Authorization: Bearer <token del cliente>`. Cada cliente está aislado.

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

- **Si `since` es la versión actual:** `304` sin cuerpo.
- **Antes de pasar cada petición a la app,** el middleware revisa la lista y responde `403 {"error": "blocked"}` si hay coincidencia.
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
        "path": ["/login", "/payouts/bank-account"]
      },
      "rationale": "Red y dispositivo nunca vistos, y fue directo a cambiar la cuenta bancaria, algo que este usuario nunca hace.",
      "event_ids": ["e_01J9ZK3T"]
    }
  ]
}
```

- **`decision`:** `allow` | `alert` | `step_up` | `block`. Para la demo bastan `allow`, `alert` y `block`.
- **Feedback:** `POST /v1/decisions/{id}/normal` marca la decisión como uso normal y lo aprende el perfil.

### 4. Perfil: motor → panel

`GET /v1/users/{user_id}/profile`

```json
{
  "user_id": "u_42",
  "history_days": 14,
  "networks": ["190.202.14.0/24"],
  "devices": ["d_9f3"],
  "hours": [9, 13],
  "routes": ["/invoices", "/invoices/{id}"],
  "state": "blocked"
}
```

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
