# Desbloqueo por correo con la marca del cliente

Estado: **decidido (Juan, 2026-10-25), sin implementar.** Es la opción 2 de cómo dejar pasar al dueño cuando el motor lo bloquea por error.

## Qué resuelve

En los casos grises de ruta crítica (el dueño viaja con su dispositivo, o estrena laptop en su red), el motor puede bloquear al dueño legítimo. Hoy solo un admin del cliente lo libera desde el panel. Con esto, el propio dueño se libera desde su correo en un clic.

## Cómo funciona

1. El motor decide `block` sobre un acceso del usuario `u_42`.
2. El middleware bloquea esa sesión, como hoy.
3. Veridian envía un correo **al usuario final**, con la marca del cliente (nombre, logo, color). Dice: "Detectamos un acceso sospechoso a tu cuenta" y tiene un botón **"Fui yo, desbloquear"**.
4. El usuario abre el enlace y llega a una página con la misma marca, con el detalle del acceso (hora, ciudad aproximada o dispositivo; nunca la IP completa) y el botón de confirmar.
5. Al confirmar, Veridian marca la decisión como normal (`POST /v1/decisions/{id}/normal`, que ya existe): la sesión y la IP salen de la lista de bloqueo, sube la `version` y el perfil lo aprende.
6. El dueño vuelve a la app y sigue. Pierde el primer intento, pero no depende de un admin.

**Por qué basta un clic:** el correo es el segundo factor. Solo quien controla esa bandeja recibe el enlace, y el ladrón que tiene la contraseña no tiene el correo.

**No contradice la decisión "Descartado: preguntar '¿fuiste tú?'"** (ver [DECISIONES.md](DECISIONES.md)): esa se descartó porque la pregunta llegaba a la misma sesión, y ahí el atacante responde que sí. Aquí la pregunta llega por otro canal, al correo.

## Requisito nuevo: Veridian necesita el correo del usuario

Hoy el middleware solo manda `user_id` ([REPARTO.md](REPARTO.md), contrato de eventos). El correo que ya existe (U17) va a los admins del cliente, no al usuario final. Sin el correo del usuario no hay a quién escribir.

**Propuesta:** el SDK acepta un campo opcional `user_email` junto al `user_id`, en eventos y en la consulta síncrona. El motor guarda solo el último correo por usuario y proyecto.

- Si un usuario no tiene `user_email`, todo sigue como hoy: bloqueo y alerta a los admins, sin correo al usuario.
- Es un dato personal nuevo: actualizar [privacidad/DATOS.md](privacidad/DATOS.md), incluirlo en el borrado por usuario (`DELETE /v1/projects/{id}/users/{user_id}`) y en la retención. No se envía a la IA.
- Alternativa descartada por ahora: que Veridian le pida el correo al cliente por webhook en el momento del bloqueo. Evita guardar el dato, pero obliga al cliente a exponer un endpoint y suma un punto de falla.

## Marca blanca

Cada proyecto configura su marca desde el panel (admin o superior):

| Campo | Uso | Si falta |
|---|---|---|
| `brand_name` | Remitente visible, asunto y texto | Nombre del proyecto |
| `brand_logo_url` | Cabecera del correo y de la página | Sin logo |
| `brand_color` | Botón y acentos (hex) | Color de Veridian |
| `brand_support_url` o `brand_support_email` | "¿No fuiste tú? Contacta a soporte" | Se omite la línea |
| `self_unlock_enabled` | Interruptor de la función | Apagado |

- **Remitente:** el primer hito envía desde un dominio de Veridian con el nombre del cliente visible (`Acme <seguridad@...veridian...>`). Enviar desde el dominio del cliente exige que el cliente verifique su dominio con el proveedor (SPF y DKIM); queda para después.
- El texto del correo y la página no menciona a Veridian, salvo un pie pequeño opcional.
- El logo se sirve por URL `https`; validarla igual que la URL del webhook (sin direcciones internas).

## Seguridad del enlace

- **Token firmado de un solo uso**, atado a `decision_id`, `project_id` y `user_id`. Guardar en la base solo su hash.
- **Vence** a las 24 h (valor de diseño, sin medir) o cuando la decisión deja de estar `open` (por ejemplo, si un admin ya la marcó normal).
- **El `GET` del enlace nunca desbloquea.** Solo muestra la página; el desbloqueo es un `POST` desde el botón. Los filtros de correo abren los enlaces para revisarlos, y un `GET` que desbloquea lo dispararían ellos.
- **Límite de envíos:** como máximo un correo por usuario cada 15 min (sin medir). Si el atacante insiste, no se inunda la bandeja del dueño.
- **Auditoría:** el desbloqueo queda como `decision.self_unlocked`, distinto de `decision.mark_normal` de un admin, y el panel lo muestra como "Desbloqueado por el usuario".
- **Riesgo conocido:** si el atacante también controla el correo del dueño, puede desbloquearse. Es el mismo límite de cualquier recuperación por correo. Se mitiga en parte porque el desbloqueo queda visible para los admins.

## Qué ve el usuario bloqueado en la app

El middleware hoy responde el bloqueo con un mensaje genérico. Cuando `self_unlock_enabled` está activo y el usuario tiene correo, el mensaje pasa a: "Detectamos un acceso inusual. Te enviamos un correo para confirmar que fuiste tú." El texto también se ve en la marca del cliente, porque lo muestra su app.

## Piezas a construir

| Pieza | Dónde | Notas |
|---|---|---|
| `user_email` opcional en el SDK y en los contratos de eventos y consulta | SDK, engine | Guardar el último por usuario |
| Campos de marca y `self_unlock_enabled` en el proyecto, con `PATCH` | engine, panel | Pantalla de configuración en el panel |
| Plantilla de correo HTML con la marca | engine | `Mailer.send` hoy solo acepta texto: agregar `html` |
| Encolar el correo al usuario en cada `block` | engine | Misma bandeja de salida que el webhook y U17 |
| Token: emisión, hash, vencimiento, un solo uso | engine | |
| Página pública de confirmación con la marca | panel (edge) | `GET` muestra, `POST` llama al motor con el token |
| Endpoint de desbloqueo con token | engine | Reutiliza la lógica de `POST /v1/decisions/{id}/normal` y audita `decision.self_unlocked` |
| Mensaje de bloqueo en el middleware | SDK | Solo si la función está activa |

## Dependencia abierta

Sin proveedor de correo (**decisión 4**, ver [DISENO-PRODUCTO.md](DISENO-PRODUCTO.md)) el motor usa `NullMailer` y nada sale. Se puede construir todo con `FakeMailer` en las pruebas, pero para la demo hay que elegir proveedor.

## Pruebas mínimas

- Un `block` con `user_email` y la función activa encola un correo con la marca del proyecto; sin `user_email` o con la función apagada, no.
- El `GET` del enlace no cambia el estado de la decisión; el `POST` la deja en `marked_normal` y sube la `version` de la lista.
- Un token vencido, ya usado o de una decisión que ya no está `open` responde error y no desbloquea.
- Dos bloqueos seguidos del mismo usuario dentro del límite envían un solo correo.
- El correo y la página no incluyen la IP completa.
- El borrado por usuario elimina su `user_email`.

## Fuera de alcance

- Botón "No fui yo" que cambie la contraseña o cierre sesiones: lo hace la app del cliente, no Veridian.
- Enviar desde el dominio del cliente.
- SMS u otros canales.
