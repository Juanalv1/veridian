# Handoff: motor de Veridian (rol Dev 1) para dos devs

**Fecha:** 2026-10-25, ~05:30 (hora de Caracas)
**Rama:** `main` (no se usan PR; ver `CLAUDE.md`)
**Estado:** en curso. El motor funciona y está desplegado; faltan unidades del plan de producto.

Antes de tocar código lee `INIT.md`, `docs/REPARTO.md` (contratos) y `docs/DISENO-PRODUCTO.md` (plan de producto). El progreso unidad por unidad está en `odd/tasks/engine-productization.md`.

## Objetivo

Terminar las unidades del motor de `docs/DISENO-PRODUCTO.md` (U2–U8, U12, U13, U14, U17). Convierten la demo de un solo cliente en un producto con organizaciones, proyectos, claves con hash, roles, webhook, retención y modo `observe`.

**Decisión del usuario (Juan):** terminar el producto tiene prioridad sobre mantener estable la demo actual. Los tests sí son obligatorios antes de cada merge.

## Reparto entre los dos devs

Las dos líneas casi no tocan los mismos archivos. El punto de contacto es U6: B necesita sus roles para exponer endpoints.

### Dev A: acceso, rutas y datos

1. **U6 (organizaciones, acceso del panel, roles).** Lo está haciendo un agente en la máquina de Juan; ver "En curso". Al aterrizar, A lo revisa contra el motor falso del Dev 2 (`edge/fake_engine/`) y prueba el panel real contra el motor real.
2. **U7: rutas por proyecto.**
   - Qué hace: tabla `route_rules`, sembrada desde `engine/routes.yaml`, con `PUT` para admin+.
   - Cambios en el motor: `GET /v1/routes` responde por proyecto, y `/v1/check` y las reglas leen la clasificación del proyecto, no la global.
3. **U8: batería de aislamiento entre proyectos.**
   - Recorre todas las rutas de la API, con el mismo `id` en dos proyectos.
   - Debe fallar si alguien agrega un endpoint que no cubre.
4. **U13, conexión.** El núcleo ya está en `engine/veridian_engine/retention.py`. Falta:
   - el hilo de purga;
   - los endpoints del contrato 14: borrado por usuario y por proyecto, solo owner y con auditoría;
   - **eliminar `POST /v1/admin/reset`** y `test_reset.py`, y cambiar los scripts de ensayo para que usen el borrado por proyecto.

### Dev B: notificaciones y operación

1. **U12, conexión del webhook.** Lo está haciendo un agente en la máquina de Juan; ver "En curso". Después, B expone los endpoints del contrato 13 (configurar, probar y desactivar el webhook de un proyecto, admin+), cuando U6 esté en `main`.
2. **U17, conexión del correo.**
   - El módulo `engine/veridian_engine/mailer.py` ya existe.
   - Falta encolar `kind='email'` en el mismo outbox del webhook, para los owners y admins con `notify_email`.
   - No hay proveedor real todavía (decisión 4 abierta); usa `NullMailer` en producción hasta que se elija.
3. **Operación en Render:**
   - medir cuántos proxies agregan `X-Forwarded-For` delante del motor (hoy `VERIDIAN_TRUSTED_PROXIES=3`, copiado de la medición de la app; ver "Advertencias");
   - rotar el token del cliente `demo`, que quedó expuesto en una conversación (motor: `VERIDIAN_TOKENS`; app y panel: `VERIDIAN_TOKEN`);
   - poner `VERIDIAN_SECRET_KEY` (Fernet) para el webhook.
4. **REPARTO.md:** documentar en neutral los contratos 13, 14 y 15 a medida que aterricen.

## En curso: agentes en la máquina de Juan

Trabajan en worktrees locales. **Sus ramas no están en GitHub.** Juan las integra a `main` cuando terminan, con tests en verde. Si no se integraron cuando llegues:
- pídele a Juan que haga `git push origin worktree-agent-<id>`, o que las integre;
- no las rehagas desde cero.

| Unidad | Rama local | Qué hace |
|---|---|---|
| U6 | `worktree-agent-a53044581f721d52c` | Migración `0004` (organización; miembros, invitaciones y auditoría por organización; datos actuales en la organización "Demo"), acceso del panel, guarda de rol, endpoints con las formas del motor falso del Dev 2, claves de API (contrato 12), `mark_normal` con rol y auditoría |
| U12 | `worktree-agent-a6db6cbc392a4912d` | Migración `0005` (`webhook_endpoint`, `notifications`), encolar en cada `block`, hilo de envío, dependencia `cryptography`, sin endpoints |

## Ya está en `main`

- [x] **U2 + U3:** migraciones numeradas (`engine/veridian_engine/migrations/`) y `client_id` → `project_id`. La base de producción se migra sola al arrancar y deja una copia `<db>.pre-NNNN`.
- [x] **U4 + U5:**
  - personas, proyectos, membresías, invitaciones, claves y auditoría (`0003`);
  - claves con hash SHA-256 y comparación en tiempo constante;
  - `429` con `Retry-After` por IP real y por prefijo;
  - los tokens de `VERIDIAN_TOKENS` se importan como claves `legacy`, así que la demo sigue autenticando.
- [x] **U14:** `enforcement` (`observe`/`enforce`) y `ai_enabled` por proyecto (`project_settings.py`). Sin endpoints: los expone U6 con `PATCH /v1/projects/{id}`.
- [x] **Módulos listos, todavía sin conectar:**
  - U12: `notifications/webhook.py`, `secret_box.py`;
  - U13: `retention.py`;
  - U17: `mailer.py`.
- [x] **Motor de la demo:**
  - contratos 1–9 de `REPARTO.md`;
  - juez de IA en segundo plano;
  - consulta síncrona para rutas críticas (`POST /v1/check`; la IA decide los casos grises con Haiku);
  - señales de ritmo y recorrido;
  - actividad, resumen, usuarios;
  - clasificador OpenAPI como borrador.
- Suite completa: **366 passed** (`cd engine && .venv/Scripts/python -m pytest -q`).

## Enfoques fallidos (no repetir)

- **Bloquear solo la petición siguiente en rutas críticas.**
  - Qué pasó: en el ensayo real el atacante cambió la cuenta bancaria (`200`) y el bloqueo llegó 1 s después, con el daño ya hecho.
  - Lo que lo reemplazó: el contrato 9 (`POST /v1/check` antes de ejecutar).
- **Rollback de la petición bloqueada.** Descartado por diseño:
  - Veridian no ve los cuerpos ni la base del cliente, así que no puede saber qué deshacer;
  - hay daños que no se deshacen (pagos, correos, descargas).
- **Dejar la ráfaga de pedidos en manos de la IA.**
  - Qué pasó: en vivo respondió `step_up`, y el middleware no lo aplica, así que el script siguió descargando.
  - Lo que lo reemplazó: ahora es una regla determinista (`rules.py`).
- **Opus en la consulta síncrona.**
  - Qué pasó: en el caso "dueño con laptop nueva" tardó 11,7 s de mediana. Eso pasa el tope de 10 s, y la regla de respaldo dejaba pasar la petición.
  - Lo que lo reemplazó: Haiku (`VERIDIAN_SYNC_MODEL`), que tomó las mismas decisiones en ~2 s (90 corridas, `docs/PENDIENTES.md`).
- **Volver a juzgar cada lote de ~2 s.**
  - Qué pasó: multiplicaba las llamadas al modelo y le daba a la IA muchas oportunidades de bloquear al dueño.
  - Lo que lo reemplazó: un juicio por sesión (`judging.needs_judgment`).
- **Comparar redes por `/16` o por proveedor** para evitar las alertas por CGNAT. En el ensayo, el teléfono del atacante salió del mismo rango grande que la laptop del dueño: con `/16` el ataque no se habría visto como red nueva.
- **Clasificar con el clasificador OpenAPI sin revisión.** Marcó `POST /login` como `critical`, lo que bloquearía el primer login desde una red nueva. Su salida es un borrador que revisa una persona.
- **Límite de fallos por `request.client.host`.** Detrás del proxy de Render todos comparten esa dirección, así que 30 claves malas de cualquiera dejaban a todos los SDK en `429`. Ahora se usa `client_ip()` con `VERIDIAN_TRUSTED_PROXIES`.

## Decisiones clave

| Decisión | Por qué |
|---|---|
| Roles **solo por organización** (decisión final del usuario) | Un rol por persona y organización, válido para todos sus proyectos. Ver `odd/tasks/edge-product-accounts.md` |
| Rutas críticas: la regla decide lo obvio al instante, la IA decide los grises en el momento (tope 10 s), *fail-open* si el motor no responde | El dueño legítimo no espera (sin desvío no hay IA). Si Veridian se cae, la app sigue funcionando |
| Bloqueo desde red conocida = solo la sesión, nunca la IP | Esa IP es la del dueño |
| Decisiones abiertas de `DISENO-PRODUCTO.md` | Se toma la recomendación del documento como valor por defecto. La 3 (si un `viewer` puede marcar normal) queda en `no` y se cambia en una línea de la matriz de roles |
| Proyectos nuevos en `observe`; el de la demo en `enforce` | Decisión 6 del diseño |

## Archivos clave

| Archivo | Para qué |
|---|---|
| `engine/veridian_engine/api.py` | Todas las rutas HTTP y el arranque |
| `engine/veridian_engine/auth.py` | Claves con hash, `429`, `client_ip()` |
| `engine/veridian_engine/db.py` + `migrations/` | Ejecutor de migraciones (una transacción por archivo, copia previa) |
| `engine/veridian_engine/control_plane/` | Organizaciones, proyectos, roles, invitaciones, claves, auditoría, siembra legacy |
| `engine/veridian_engine/rules.py` | Regla crítica y regla de ráfaga (bloqueos síncronos) |
| `engine/veridian_engine/check.py` | Contrato 9 |
| `engine/veridian_engine/judging.py` | Juez en segundo plano, deduplicación y `ai_judgments` |
| `engine/veridian_engine/project_settings.py` | `observe`/`enforce`, `ai_enabled` |
| `engine/veridian_engine/retention.py` | U13 (sin conectar) |
| `engine/veridian_engine/notifications/` | U12 |
| `engine/veridian_engine/mailer.py` | U17 (sin conectar) |
| `edge/fake_engine/` | Motor falso del Dev 2: **las formas de los contratos del panel salen de acá** |
| `odd/tasks/engine-*.md` | Diseño, RED/GREEN y commits de cada tarea |

## Cómo retomar

1. **Preparar el entorno:**
   ```
   git pull --rebase
   cd engine
   py -3.12 -m venv .venv
   .venv/Scripts/pip install -r requirements.txt
   .venv/Scripts/python -m pytest -q
   ```
   - Esperado: todo en verde (366 o más).
   - Si falla `cryptography`: la agrega U12; mientras U12 no esté en `main`, un test de `notifications` se omite.
2. **Levantar en local:**
   ```
   VERIDIAN_TOKEN=dev .venv/Scripts/uvicorn veridian_engine.api:create_app --factory --reload
   ```
   El motor lee `.env` buscando hacia arriba desde la carpeta actual.
3. **Probar el escenario de punta a punta, sin middleware:**
   ```
   py -3.12 scripts/send_sample_events.py --base-url http://localhost:8000 --token dev
   ```
   - Esperado: un `block` con la sesión y la IP del atacante.
   - Con `--scenario bot`: un `block` solo de sesión.
4. **Cada unidad en la que trabajes:**
   - test primero;
   - un commit con Conventional Commits, sin atribución a IA y con la línea `Status: done|in-progress`;
   - actualizar `odd/tasks/engine-productization.md`.
   - Los hooks de `.claude/` hacen pull y push solos.
5. **Push:** configura los dos destinos, para que tus commits lleguen al repo que evalúan:
   ```
   git remote set-url --add --push origin https://github.com/Juanalv1/veridian.git
   git remote set-url --add --push origin https://github.com/platanus-hack/platanus-hack-26-ve-team-21.git
   ```

## Configuración (Render, servicio `veridian-engine`)

- **URL:** https://veridian-engine.onrender.com. Plan `starter` con disco; SQLite en `/var/data/veridian.db`.
- **Clientes:** `demo` (la app de la demo) y `smoke` (pruebas).
- **Variables:**
  - `VERIDIAN_TOKENS` y `ANTHROPIC_API_KEY`: secretos, en Render;
  - `VERIDIAN_MODEL` (Opus) y `VERIDIAN_SYNC_MODEL` (Haiku);
  - `VERIDIAN_TRUSTED_PROXIES`;
  - límites de autenticación: `VERIDIAN_AUTH_MAX_FAILURES_PER_IP`, `VERIDIAN_AUTH_MAX_FAILURES_PER_PREFIX`, `VERIDIAN_AUTH_FAILURE_WINDOW_S`;
  - `VERIDIAN_RATE_FACTOR` y `VERIDIAN_RATE_FLOOR`;
  - el secreto del reset (ver `render.yaml`).

  Hay una tabla completa en `engine/README.md`.
- **Ensayos:**
  - `engine/scripts/reset_client.py`, `seed_demo_history.py` y `send_sample_events.py` leen `.env` desde `engine/`;
  - el reset desaparece con U13.

## Advertencias

- **Cada push a `main` despliega el motor en Render,** con unos segundos de `502`: el disco impide desplegar sin corte.
- **No hay vuelta atrás automática de las migraciones.** Volver a código viejo exige restaurar la copia `.pre-NNNN` del disco.
- **`VERIDIAN_TRUSTED_PROXIES=3` no está medido para el motor.** Si en realidad hay menos proxies, la IP del límite de fallos sale del valor de la conexión (compartido). Si hay más, sale de una entrada que el cliente controla.
- **Quitar un token de `VERIDIAN_TOKENS` ya no lo revoca:** queda importado como clave. Para revocarlo hay que usar el contrato 12 (U6) o poner `revoked_at` en `api_key`.
- **La venv compartida de la máquina de Juan ya tiene `cryptography` instalada.** Puede que en tu máquina falte hasta que U12 la agregue a `requirements.txt`.
- **`docs/PENDIENTES.md` tiene los hallazgos del ensayo real:**
  - la IP del dueño rota por CGNAT;
  - la escalada `review` → `step_up` → `block` en el panel;
  - el dueño con dispositivo nuevo queda bloqueado en rutas críticas.
