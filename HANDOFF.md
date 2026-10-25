# Handoff: Veridian para cuatro devs (Carlos Navas: motor y SDK de Python; Christian Mendoza: notificaciones y operación; Maikol Aguilar: frontend; José Gómez: SDK de npm y registro)

**Fecha:** 2026-10-25, ~05:30 (hora de Caracas)
**Rama:** `main` (no se usan PR; ver `CLAUDE.md`)
**Estado:** en curso.
- El motor está desplegado y pasa sus tests, pero **su comportamiento no está validado por personas.** Ver "Validación humana pendiente": los ensayos reales ya mostraron cosas que había que cambiar, y quedan casos sin probar.
- Faltan unidades del plan de producto.

Antes de tocar código lee `INIT.md`, `docs/REPARTO.md` (contratos) y `docs/DISENO-PRODUCTO.md` (plan de producto). El progreso unidad por unidad está en `odd/tasks/engine-productization.md`.

## Objetivo

Terminar las unidades del motor de `docs/DISENO-PRODUCTO.md` (U2–U8, U12, U13, U14, U17). Convierten la demo de un solo cliente en un producto con organizaciones, proyectos, claves con hash, roles, webhook, retención y modo `observe`.

**Decisión del usuario (Juan):** terminar el producto tiene prioridad sobre mantener estable la demo actual. Los tests sí son obligatorios antes de cada merge.

## Reparto (actualizado 2026-10-25)

- **Carlos Navas (antes Dev 3):** motor (acceso, rutas y datos, lo que era del Dev A) y SDK/middleware/app de demo.
- **Christian Mendoza (Dev B):** notificaciones y operación.
- **Maikol Aguilar (antes Dev A):** el frontend: landing, panel y el resto del backoffice.
- **José Gómez (nuevo):** el SDK de npm y el registro y login del backoffice.

Lo que Maikol cerró en el motor queda en `main`, con tests y documentado en `REPARTO.md` y `odd/tasks/engine-productization.md`:
- el contrato 11 (`45f965a`) y `DELETE /v1/projects/{id}` (`ff891db`);
- el contrato 15 y los nombres de acción del fake engine, con la migración `0007` (`b637776`);
- U7 (`24c440b`, migración `0008`) y U8 (`ae134e2`);
- U13 parte 1: los endpoints del contrato 14 y el hilo de retención (`8ad03cb`).

Estos cambios se hicieron antes de que llegara el nuevo reparto (`1baaf95`). Suite: 665 passed.

### Carlos Navas: motor (acceso, rutas y datos)

1. **Revisar U6** (ya en `main`) con el panel real contra el motor real. Diferencias conocidas con el motor falso:
   - todos los proyectos legacy quedan en una sola organización "Demo" (`org_demo`), mientras el falso arma una por token;
   - los ids son `prj_` y `org_`, no `p_`;
   - una invitación no cambia el rol de quien ya es miembro;
   - `mark_normal` devuelve `{id, normal, version}`;
   - ~~la invitación vence a los 7 días~~: corregido, ahora son 14 (`VERIDIAN_INVITATION_DAYS`).

   El contrato 15 ya está en el motor real (`b637776`).
2. **Hecho: U7, rutas por proyecto** (`24c440b`). La clasificación automática de rutas (tarea 6 de Christian) se construye encima de `route_rules` y `PUT /v1/projects/{id}/routes`.
3. **Hecho: U8, batería de aislamiento** (`ae134e2`, `engine/tests/test_isolation.py`). Cada endpoint nuevo necesita una entrada en `OPERATIONS`, o falla `test_every_operation_is_covered`.
4. **U13.** La parte 1 está hecha (`8ad03cb`): el hilo de purga y los endpoints del contrato 14.
   - El borrado por usuario es para admin+ y el borrado del proyecto, solo para owner. Así lo dice `DISENO-PRODUCTO.md` (contrato 14); este handoff decía "solo owner" para los dos.
   - **Hecho (2026-10-25, Carlos):** se eliminaron `POST /v1/admin/reset`, `VERIDIAN_RESET_TOKEN`, `reset_client.py` y sus tests. Los ensayos usan `prepare_demo.py`, que ya borraba con `DELETE /v1/projects/{id}/data`. Borrar `VERIDIAN_RESET_TOKEN` de Render.
5. **Hecho en el motor real (2026-10-25): registro abierto.** `POST /v1/orgs` acepta a cualquier actor conocido; límite `VERIDIAN_MAX_ORGS_PER_PERSON` (3) de organizaciones vivas creadas por la persona, `409 {"error": "org_limit_reached", "limit"}`; `VERIDIAN_ORG_CREATORS` = sin límite. Contrato 10 actualizado. **Falta el mismo cambio en el motor falso (José).** Lo que se pedía: (decisión 2, resuelta por Juan el 2026-10-25): `POST /v1/orgs` lo puede usar cualquier persona autenticada, no solo los correos de `VERIDIAN_ORG_CREATORS`. Lo necesita el registro en un paso de Maikol.
   - Poner un límite de organizaciones por persona para evitar abusos (decidir el número).
   - Actualizar el contrato 10 en `REPARTO.md` y los READMEs que describen `VERIDIAN_ORG_CREATORS`.
   - **Tests:** una persona sin invitación crea su organización y queda `owner`; pasado el límite recibe un error claro.
6. **La demo completa** (pedido de Juan, 2026-10-25). Carlos es responsable de que la demo en vivo funcione en el escenario:
   - **Ensayar la demo** con el sistema real en Render, contra el guion de la demo que está escribiendo Juan (`docs/GUION.md` y `docs/DEMO.md`). Mientras el guion no esté listo, ensayar el escenario de `docs/DEMO.md`: el dueño entra normal y otra persona, desde otra red, intenta cambiar la cuenta bancaria.
   - **Preparar cada ensayo** con `engine/scripts/prepare_demo.py` (ya hecho por Christian, `bbda6a7`): `--calibrar` en el wifi del evento y luego el modo normal antes de cada ensayo. Reportar lo que falle.
   - **Rotar el token `demo`**, que quedó expuesto en una conversación (motor: `VERIDIAN_TOKENS`; app y panel: `VERIDIAN_TOKEN`), y **poner `VERIDIAN_SECRET_KEY`** (Fernet) para el webhook. Las dos salen de la tarea 3 de Christian.
   - **El middleware no tomó la lista nueva después de un reset** (ver "Pendiente" en su sección de SDK): investigarlo, porque aparece justo en los ensayos.
   - **Cerrar U13** (punto 4): sacar el reset para que la demo use solo el borrado por proyecto.
   - **Medir y anotar** en `docs/PENDIENTES.md` lo que digan los ensayos: tiempos de cada momento, si la IA bloquea siempre al atacante y si nunca bloquea al dueño.
   - Preparar el video de respaldo de una corrida buena.

### Dev B: notificaciones y operación

1. **Hecho (2026-10-25): U12, endpoints del webhook** (contrato 13, documentado en `REPARTO.md`). Los proyectos en `observe` notifican con `decision.enforced: false`; la migración `0006` borró `project.webhook_url` y `project.webhook_secret_enc`.
2. **Hecho (2026-10-25): U17, conexión del correo** (documentado en `REPARTO.md`, "Correo de bloqueo y preferencia"). Falta elegir proveedor (decisión 4); hasta entonces los correos quedan `failed`. El panel (Dev 3) puede mostrar el interruptor con `GET`/`PATCH /v1/orgs/{id}/me`. Lo que se pedía:
   - El módulo `engine/veridian_engine/mailer.py` ya existe.
   - Falta encolar `kind='email'` en el mismo outbox del webhook, para los owners y admins con `notify_email`.
   - No hay proveedor real todavía (decisión 4 abierta); usa `NullMailer` en producción hasta que se elija.
3. **Operación en Render:**
   - **Hecho (2026-10-25):** medidos los proxies delante del motor: son 2 (`VERIDIAN_TRUSTED_PROXIES=2`, ver "Advertencias");
   - rotar el token `demo` y poner `VERIDIAN_SECRET_KEY` **pasaron a Carlos** (tarea 6 de Carlos, la demo completa).
4. **Hecho (2026-10-25): REPARTO.md** tiene los contratos 11, 13, 14, 15 y 16.
5. **Hecho (2026-10-25): `engine/scripts/prepare_demo.py`.** Una vez: en el panel, Miembros, invitar `ensayo@veridian.demo` como **owner** de la organización Demo (el script entra como esa persona con `VERIDIAN_PANEL_TOKEN`). En `engine/.env`: `VERIDIAN_BASE_URL`, `VERIDIAN_TOKEN` (clave `demo`), `VERIDIAN_PANEL_TOKEN` y, opcional, `DEMO_USER_ID`, `RENDER_API_KEY` + `DEMO_RENDER_SERVICE_ID` para reiniciar la app al final. Uso: el presentador navega la app unos minutos en el wifi del evento, `prepare_demo.py --calibrar`, y antes de cada ensayo `prepare_demo.py` (borra con `DELETE /v1/projects/{id}/data`, ya no usa el reset). Imprime `listo: N redes, N dispositivo, 14 días` o qué falló. Lo que se pedía: (pedido de Juan; hoy son 4 comandos con 3 secretos y las IPs copiadas a mano). Coordinar con Carlos, porque U13 reemplaza el reset por el borrado por proyecto.
   - **Script:** `engine/scripts/prepare_demo.py`, sin argumentos en el uso normal.
     - **Configuración:** lee la URL, el token `demo`, el secreto de reset (o lo que lo reemplace con U13) y el usuario del presentador de `engine/.env` (por ejemplo `DEMO_USER_ID=u_42`).
     - **Modo `--calibrar`:** el presentador navega unos minutos y el script toma de `GET /v1/activity` (con el acceso del panel) **todas** las IPs y `device_id` de ese usuario en los últimos N minutos. Así se resuelve lo del CGNAT sin copiar IPs a mano. Las guarda en `engine/.demo-presenter.json`, ignorado por git.
     - **Modo normal:**
       - reinicia el cliente;
       - siembra con las IPs y los dispositivos calibrados, en la hora UTC actual ±1;
       - verifica que el perfil quedó como se esperaba;
       - imprime un resumen de una línea: `listo: 3 redes, 1 dispositivo, 14 días`.
   - **Botón (opcional, con Maikol):** "Preparar demo" en el panel, visible solo para un owner y solo si el ensayo está habilitado en el motor. Lo respalda un endpoint del motor que hace lo mismo que el script, por ejemplo `POST /v1/projects/{id}/rehearsal/prepare`. Tiene que ir con auditoría y quedar apagado en producción.
   - **Tests:** que la calibración tome todas las IPs del usuario y no las de otros usuarios; que la preparación deje el perfil sin rastros del atacante; que sin el ensayo habilitado el endpoint responda `404`.
   - **Problema visto en el ensayo:**
     - Qué pasó: después del reset, el motor tenía la blocklist vacía (`version 14`), pero la app seguía bloqueando el teléfono del atacante anterior. Hubo que reiniciar `veridian-demo` en Render para que el middleware cargara la lista nueva.
     - Causa: no investigada. La versión había subido, así que el middleware debería haber recibido la lista nueva en ~2 s. Puede que no estuviera leyendo `/v1/blocklist`, que por contrato conserva la última lista conocida. Revisar con Carlos si U6 afectó la autenticación del middleware.
     - Lo que hace falta: que la preparación de la demo verifique que la app ya no bloquea, o que reinicie la app.


6. **Hecho (2026-10-25): clasificación automática de rutas** (contrato 16 en `REPARTO.md`; seguimiento en `odd/tasks/route-auto-classification.md`). Si la IA falla, la ruta conserva su última clase y se reintenta en el próximo envío; las correcciones del admin y `routes.yaml` mandan sobre la IA. El prompt ya no marca el login como `critical`. Falta en el panel (Dev 3): mostrar `GET /v1/projects/{id}/routes/catalog` (estado y motivo de cada ruta). Lo que se pedía: Hoy es manual: `engine/scripts/classify_openapi.py` genera un borrador de `routes.yaml` que alguien revisa y sube. Debe ser automático, porque el pitch lo presenta así.
   - **Flujo:** al arrancar, el SDK envía al motor las rutas de la app (método, ruta y descripción, sacadas del OpenAPI que ya expone FastAPI). El motor las clasifica con la IA (`veridian_engine.openapi.classify`), guarda el resultado por proyecto y lo sirve en `GET /v1/routes`, que el middleware ya consulta.
   - **Solo rutas, nunca código fuente.**
   - **Depende de U7 (Carlos):** la tabla `route_rules` por proyecto. Coordinar con Carlos: construirlo encima de U7 o acordar quién arma la tabla. El panel que muestra la clasificación es de Maikol.
   - **Rutas nuevas o cambiadas:** clasificar solo las que cambiaron desde la última vez, no todas en cada arranque.
   - **Decidir:**
     - qué pasa si la IA falla; hoy la ruta queda `normal` y eso apaga la regla de bloqueo para ella. Una opción es mantener la última clasificación conocida y reintentar;
     - si un admin puede corregir una clase desde el panel (el `PUT` de U7) y si esa corrección sobrevive a la próxima clasificación.
   - **Contrato nuevo** en `REPARTO.md` para el envío de rutas (SDK → motor).
   - **Tests:** el SDK envía las rutas al arrancar; el motor las clasifica y las sirve por proyecto; una falla de la IA no borra la clasificación anterior; dos proyectos no ven las rutas del otro.
7. **Hecho (2026-10-25): pruebas y ajuste del funcionamiento.** 17 de 17 casos como se esperaba, 3 corridas cada uno (`docs/HALLAZGOS.md`, "Validación del motor real"); correcciones en `d20663e` y `eb6585d`; riesgos en `docs/PENDIENTES.md`; guía para probar con personas: `docs/PRUEBAS-MANUALES.md`. Lo que se pedía: (pedido de Juan, 2026-10-25). Probar Veridian con casos variados y ajustar lo que falle, para que decida bien fuera del guion de la demo. El ensayo de la demo es de Carlos (su tarea 6); esto es todo lo demás.
   - **Usos legítimos pero raros** que no deben bloquearse: el dueño de viaje (otra red), con un dispositivo nuevo, trabajando de noche, detrás de una VPN, con la IP que rota entre redes (ya visto en el ensayo: tres redes en minutos). Hoy hay un riesgo anotado en `docs/PENDIENTES.md`: el dueño con un dispositivo nuevo en una ruta crítica queda bloqueado.
   - **Ataques** que sí deben bloquearse: otra persona desde otra red yendo a una ruta crítica, una descarga masiva de datos, un robo lento (pocas peticiones, repartidas), un script desde la misma red del dueño y una sesión robada.
   - **Ajustar** según lo que salga: los umbrales del perfil y del ritmo (`rate_factor`, `rate_floor`, `VERIDIAN_LEARN_DELAY_MIN`), el prompt de la IA, los tiempos de la consulta síncrona y la regla de respaldo cuando la IA no responde.
   - **Medir** en cada caso la decisión, si se repite igual en varias corridas y cuánto tarda. Anotar resultados y cambios en `docs/HALLAZGOS.md` (sección de validación) y los riesgos que queden en `docs/PENDIENTES.md`.
   - **Tests:** cada ajuste va con un test que fije el caso que corrige.

### Maikol Aguilar: frontend (landing, backoffice y panel)

**Qué toma:** `landing/`, `edge/panel/` (backoffice y panel) y `edge/fake_engine/`, el motor falso del que salen las formas de los contratos del panel.

**Design system:** [Veridian](https://claude.ai/artifact/2JFynja7aQQqX1oJN6qmK2) (colores, tipografía, logo, botones). Es la base para la landing y el panel, y es libre de añadirle lo que haga falta: componentes, estados, patrones.

**Pendiente:**
- **El registro y el login pasaron a José Gómez** (ver su sección). Coordinar con él los estilos y las plantillas compartidas del panel.
- **T6:** el panel para el webhook (contrato 13, ya en el motor real: `cb1b125`) y para borrar datos de un usuario o de un proyecto (contrato 14, depende de U13 de Carlos).
- **T4, seguimiento:** una revisión visual. La decisión abierta 2 (quién crea organizaciones) queda resuelta: cualquiera con cuenta (ver el registro).
- **El snippet en el panel y la landing** (puntos 2 y 3 de "Mejorar la integración del SDK", más abajo): el texto explicativo, las pestañas Python / Node, el resaltado, el botón de copiar y la clave recién creada ya puesta. La API del SDK la define Carlos; coordinar el snippet con él.
- **La clasificación de rutas en el panel:** mostrarla cuando esté lista la tarea 6 de Christian.

**Ya hecho por el borde, en `main`:** T1–T5, T7–T10 (ver la lista de Carlos). T5 quedó cerrado con la página de claves de API y la de auditoría (`bb6aea1`).

### José Gómez: SDK de npm y registro del backoffice

**Qué toma:** un paquete nuevo en `sdk/node/` y, en el panel (`edge/panel/`) y el motor falso, el registro y el login. El resto del panel es de Maikol: coordinar con él los estilos y plantillas, con el [design system](https://claude.ai/artifact/2JFynja7aQQqX1oJN6qmK2) como base.

1. **SDK para npm** (la especificación completa está más abajo, en "SDK para npm, además del de Python", dentro de la sección de Carlos). Mismo comportamiento y contratos que el SDK de Python, con la API simple que ya tiene ese SDK (`48011c2`): la clave sale de `VERIDIAN_API_KEY`, un parámetro para identificar al usuario y la sesión detectada sola. Incluye reportar las rutas al arrancar para la clasificación automática, como hace el de Python (`967ee9e`). Coordinar con Carlos, que mantiene el de Python.
2. **Registro en un paso y primera clave** (pedido de Juan, 2026-10-25; lo más importante del backoffice). Cualquier persona tiene que poder registrarse sola y dejar Veridian configurado en su app:
   1. **Entrar con Google o con GitHub** (OAuth). Google ya existe desde T3; falta GitHub. Sin formulario de registro aparte.
      - GitHub puede ocultar el correo del perfil: pedir el permiso `user:email` y usar el correo principal verificado.
      - La misma persona entrando con Google y con GitHub (mismo correo verificado) es una sola cuenta, no dos.
   2. **Primera vez, sin organización:** el panel pide solo el nombre de la empresa y crea la organización (con la persona como `owner`) y su primer proyecto. En el motor real ya está abierto (`7e18a33`, tarea 5 de Carlos): cualquier actor conocido, hasta `VERIDIAN_MAX_ORGS_PER_PERSON` (3) organizaciones creadas por persona; pasado el límite, `409 {"error": "org_limit_reached", "limit"}`, y el panel debe mostrar ese caso. Falta el mismo cambio en el motor falso (José), con esas formas exactas (contrato 10 de `REPARTO.md`).
   3. **Crear la clave de API** (la página de claves ya existe, contrato 12) y mostrar el snippet con esa clave ya puesta, listo para copiar. El snippet lo diseña Maikol; usar el mismo componente.
   4. **Confirmar la conexión:** un estado «esperando el primer evento» que cambia a «conectado» cuando llega el primer acceso desde su app.
   - **Tests:** una persona nueva entra con Google o GitHub, crea su organización y su clave sin que nadie la invite; una persona con organización no vuelve a ver el paso 2; la misma persona con los dos proveedores es una sola cuenta.
   - **Avance de José (2026-10-25):** hecho el punto 2 completo en el panel y el motor falso (T1-T4 de `odd/tasks/backoffice-self-signup.md`, 747 tests pasan): login con GitHub, misma persona por correo verificado, paso de «nombre de la empresa» que crea organización y primer proyecto, y la página de claves con el estado «Esperando el primer evento» / «Conectado». Falta: crear la OAuth App de GitHub (callback `<PUBLIC_URL>/auth/github/callback`) y poner `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`; y que el motor real identifique a las personas por correo verificado entre proveedores (`docs/REPARTO.md`, «Login»).
- **Motor real, login por correo verificado (José, 2026-10-25, `170a3ca`):** `POST /v1/people/login` ya une Google y GitHub con el mismo correo en una persona (migración `0011`). Queda solo la OAuth App de GitHub.
- **Avance de José en el SDK de npm (`sdk/node/`, plan `odd/tasks/sdk-node.md`):**
  - T1 hecho (`a10c038`): paquete `veridian-sdk` (JavaScript ESM sin dependencias), configuración, errores al arrancar, IP del cliente, cookie `vrd_did` y sesión detectada.
  - T2 hecho (`54e8daa`): núcleo con lotes cada ~2 s en segundo plano (timers `unref`), consulta de `/v1/blocklist` con `since`, `403` a lo bloqueado y pasar ante `401`/`429`.
  - T3 hecho (`4eb2d29`, subido por la sincronización automática): rutas críticas y `POST /v1/check` (12 s, fail-open), reporte de rutas al arrancar (se lee del router de Express; en Express 5 los routers montados en una ruta hay que pasarlos con `routes`), adaptadores de Express 4/5 y de Next.js (route handlers, runtime Node); 128 tests.
  - T4 hecho (`310b453`): README, `examples/quickstart.js` ejecutado tal cual contra `edge/fake_engine` (autentica, envía eventos y reporta rutas), instalación de Node en el `README.md` raíz; 134 tests en `sdk/node`, 747 en `edge`. Falta publicar en npm (el nombre `veridian-sdk` estaba libre) y la pestaña Node del panel y la landing (Maikol).
  - **Desbloqueo por correo, lado panel y SDK (José, 2026-10-25, T6 y T7 de `odd/tasks/email-self-unlock.md`):** pantalla `/marca` (marca y interruptor), página pública `/desbloquear/<token>` (el `GET` solo muestra; el botón hace `POST`), etiqueta «Desbloqueado por el usuario» en la auditoría, motor falso con la misma API, y `user_email`/`userEmail` más el mensaje de bloqueo en los dos SDK (npm 0.2.0, Python 0.4.0, sin publicar).

### Carlos Navas: SDK, middleware y app de demo (lo que era del Dev 2)

**Qué toma:** middleware, SDK y app de demo (`sdk/`, `edge/demo_app/`). El panel, la landing y el motor falso pasaron a Maikol.
- Plan: `odd/tasks/edge-product-accounts.md`.
- Otras tareas: `middleware-critical-check.md`, `panel-routes-readonly.md`, `sdk-python-pypi.md`, `edge-first-milestone.md`.
- Diseño: `docs/DISENO-PRODUCTO.md`; ver la nota "Revisión 2026-10-25" sobre la capa de organización.

**Ya hecho, en `main`:**
- **T1:** el reset de ensayos pide un secreto aparte (`VERIDIAN_RESET_TOKEN`).
- **T2:** el motor falso tiene el acceso del panel (credencial de servicio + `X-Veridian-Actor` + `X-Veridian-Project`) y el contrato 10.
- **T3:** el panel tiene login con Google, sesión firmada, CSRF y logout. No hay modo abierto.
- **T4:** el panel tiene selector de organización y proyecto, creación de ambos y borrado de organizaciones vacías, con el estilo de los mockups aprobados.
- **T7:** el SDK (`veridian-sdk`) deja pasar las peticiones si el motor responde `401` o `429`, y el README y la landing ya dicen "crea una clave en el panel".
- **T8:** documentos de privacidad en `docs/privacidad/`.
- **T9 y T10:** la capa de organización (roles solo por organización) y `DELETE /v1/orgs/{id}`.
- **Consulta previa en el middleware:** antes de una ruta crítica llama a `POST /v1/check`; si responde `block`, devuelve `403` (contrato 9).

- **T5:** el motor falso tiene los contratos 11, 12 y 15, y el panel las páginas de miembros, claves de API y auditoría (`bb6aea1`).

**Pendiente:**
- **El middleware no tomó la lista nueva después de un reset.** En el ensayo hubo que reiniciar `veridian-demo` en Render. No está investigado; ver la tarea 5 de Christian.

**Para coordinar con el motor real:**
- El motor real tiene los contratos 10 a 15 y el acceso del panel (U6), con las mismas formas que el motor falso (el 15 es `b637776`).
- Diferencias conocidas con el motor falso:
  - todos los proyectos legacy quedan en una sola organización, "Demo";
  - los ids son `prj_` y `org_`;
  - una invitación no cambia el rol de quien ya es miembro.
- Para que el panel desplegado funcione contra el motor real, hay que configurar `VERIDIAN_PANEL_TOKEN` en los dos servicios de Render, con el mismo valor.

**Nuevo (pedido de Juan):**
- **SDK para npm, además del de Python (lo toma José Gómez).** Un paquete de Node (por ejemplo `veridian-sdk`, para Express y Next.js) con el mismo comportamiento que `sdk/python/`:
  - envía los eventos en lotes cada ~2 s, en segundo plano;
  - consulta `/v1/blocklist` con `since`;
  - lee las rutas críticas de `/v1/routes` y consulta `POST /v1/check` antes de ejecutarlas, con timeout de 12 s y *fail-open*;
  - usa la cookie de dispositivo `vrd_did`;
  - toma la IP real con `trusted_proxies`;
  - deja pasar las peticiones ante `401` o `429`.

  Mismos contratos (1, 2, 8 y 9 de `REPARTO.md`) y tests equivalentes a los del SDK de Python. Documentarlo junto al de Python en el panel, la landing y los READMEs.
- **Mejorar la integración del SDK: el código mismo, no solo cómo se muestra.** Hoy:
  - hay dos formas distintas de instalarlo: `sdk/python/README.md` envuelve la app (`app = VeridianMiddleware(app, ...)`) y el panel (`edge/panel/templates/claves.html`) muestra `app.add_middleware(...)`;
  - el snippet del panel no tiene `base_url`, que es obligatorio, así que **copiado tal cual no funciona**;
  - identificar al usuario se escribe `lambda request: request.state.user_id if hasattr(request.state, "user_id") else None`;
  - nada explica qué hace cada parámetro.

  Lo que hace falta:
  1. **API más simple en el SDK** (`sdk/python/src/veridian_sdk/`; lo mismo en el de npm).
     - **Valores por defecto:** la clave sale de `VERIDIAN_API_KEY` y la URL de `VERIDIAN_URL`, y si no hay URL se usa la del motor en producción. Así el caso mínimo es una línea más la función que identifica al usuario.
     - **Identificar al usuario:** un parámetro claro, por ejemplo `user=lambda request: request.state.user.id`, con `None` sin errores cuando no hay sesión.
     - **La sesión:** que se detecte sola cuando se pueda.
     - **Una sola forma de instalarlo:** `app.add_middleware(VeridianMiddleware, ...)` en Python y `app.use(veridian({...}))` en Node.
     - **Errores claros al arrancar** si falta la clave o la URL; hoy falla con `KeyError`.

     Mantener la API actual funcionando, porque la usa la app de la demo.
  2. **Un snippet completo y explicativo,** igual en el panel, la landing, `README.md` y los READMEs de los SDK. Que sea código que funcione copiado tal cual, comentado línea por línea, con:
     - cómo instalar (`pip install veridian-sdk` / `npm install ...`);
     - dónde poner la clave;
     - cómo identificar al usuario según el framework;
     - qué es `trusted_proxies` y cuándo cambiarlo;
     - qué pasa si Veridian no responde (la app sigue funcionando);
     - cómo comprobar que quedó conectado (qué aparece en el panel).
  3. **Presentación (Maikol):** pestañas Python / Node, resaltado de sintaxis, botón de copiar y, en el panel, la clave recién creada ya puesta en el snippet (se muestra una sola vez), con el design system.
  4. **Tests:**
     - el snippet publicado, ejecutado tal cual contra el motor falso, autentica y envía eventos;
     - la API vieja sigue funcionando.

**Ojo:** el último push del Dev 2 fue a las 06:02. Si dejó cambios sin commit en su laptop después de esa hora, no están en el repo.

## Ya está en `main`

- [x] **U2 + U3:** migraciones numeradas (`engine/veridian_engine/migrations/`) y `client_id` → `project_id`. La base de producción se migra sola al arrancar y deja una copia `<db>.pre-NNNN`.
- [x] **U4 + U5:**
  - personas, proyectos, membresías, invitaciones, claves y auditoría (`0003`);
  - claves con hash SHA-256 y comparación en tiempo constante;
  - `429` con `Retry-After` por IP real y por prefijo;
  - los tokens de `VERIDIAN_TOKENS` se importan como claves `legacy`, así que la demo sigue autenticando.
- [x] **U6:**
  - migración `0004` (organización; miembros, invitaciones y auditoría por organización);
  - acceso del panel: `VERIDIAN_PANEL_TOKEN` + `X-Veridian-Actor` + `X-Veridian-Project`;
  - las claves del SDK **ya no leen** los endpoints del panel (`403`);
  - contrato 10 (login, organizaciones, proyectos con `observe`/`ai_enabled`) y contrato 12 (claves de API);
  - `mark_normal` exige admin y queda auditado; la decisión 3 es `roles.VIEWER_CAN_MARK_NORMAL = False`.

  Probé el orden real de producción (`0005` ya aplicada, `0004` después) sobre una base con datos: migra bien.
- [x] **U12, conexión:** migración `0005`, notificación en cada `block`, hilo de envío (`VERIDIAN_WEBHOOKS`, `VERIDIAN_PANEL_URL`), `cryptography` en `requirements.txt`. Sin endpoints.
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
- Suite completa: **480 passed** (`cd engine && .venv/Scripts/python -m pytest -q`).

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

## Render: el motor solo se redespliega con su código (2026-10-25)

El servicio `veridian-engine` tiene filtro de build: `engine/**` y `render.yaml`, sin `engine/tests/**`, `engine/scripts/**` ni `engine/README.md`. Antes, cualquier push (también de documentación o del panel) reiniciaba el motor con unos segundos de `502`, y un login en el panel durante ese corte falla con "Veridian no pudo confirmar tu cuenta". El filtro está en la configuración del servicio en Render, no en `render.yaml`.

## Hallazgo del testing en producción (2026-10-25)

- **El dueño quedaba bloqueado la primera vez que cambiaba su cuenta bancaria**, aun desde su red y su dispositivo, a su hora: 3 de 3 veces en producción. "Ruta crítica nunca usada" alcanzaba para consultar a la IA, y Haiku bloqueaba aunque su propio motivo decía que todo coincidía con el titular.
- **Corregido** (`rules.py`): con la red **y** el dispositivo conocidos, el primer uso de una ruta `critical` no es un desvío y no se consulta a la IA. Sin `device_id`, con un dispositivo nuevo o desde una red nueva se sigue juzgando. Las rutas `sensitive` (exportar) se siguen juzgando en segundo plano.
- Riesgo aceptado: alguien que opere desde la propia máquina y red del dueño pasa. Ese caso queda fuera del alcance (otra persona usando la credencial desde otro lado).

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
| `edge/fake_engine/` | Motor falso (lo mantiene Maikol): **las formas de los contratos del panel salen de acá** |
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
   - Esperado: todo en verde (480 o más).
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
5. **Push: solo al repo personal** (`https://github.com/Juanalv1/veridian.git`), que es el `origin` por defecto al clonar. Render despliega desde ahí.
   - **No hagas push directo** al repo de la organización (`platanus-hack/platanus-hack-26-ve-team-21`). Ese es solo un espejo para los jueces, y lo actualiza la máquina de Juan, que sube a los dos.
   - Subir a los dos repos desde varias máquinas causó carreras de push y commits que estaban en un repo y no en el otro.

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

## Corregido (2026-10-25): el perfil aprendía de la sesión del atacante

**Qué pasaba:** `build_profile` (`engine/veridian_engine/profile.py`) solo descartaba los eventos ligados a una decisión `block`/`alert` por `decision_events`, es decir, el evento que **disparó** la decisión. El resto de la sesión del atacante (su `POST /login`, las páginas después del bloqueo) se aprendía pasada la espera. Así, el perfil de Ana (`u_42`, cliente `demo`) terminó con la red `186.164.35.0/24` y el dispositivo `d_11623911ab28` del atacante. Eso rompía el principio de `docs/DECISIONES.md`: *"Nunca aprende de lo marcado como sospechoso"*.

**Corrección:** un evento no se aprende si:
- su sesión tiene una decisión `block`, `step_up` o `alert` abierta (toda la sesión, no solo el evento que la disparó); o
- viene de una IP que incluye un `block` abierto (el atacante que vuelve en una sesión nueva).

Si la decisión se marca como normal, la sesión y la IP se aprenden (contrato 3). `review` no excluye nada: es el dueño con la IP rotando. Tests en `engine/tests/test_decisions.py`.

**Base de la demo:** no hace falta limpiarla. Los perfiles se calculan al leer desde `events`, así que el de Ana deja de tener la red y el dispositivo del atacante en cuanto el motor despliega, mientras ese bloqueo siga abierto. Si alguien lo marcó como normal, sí se aprenden: en ese caso, reiniciar y sembrar `demo`.

**Lo que no cubre:** el atacante que vuelve con el mismo teléfono desde **otra** IP, en una sesión nueva, sin decisión todavía. Excluir por dispositivo bloquearía al dueño en el caso del script en su laptop (bloqueo solo de sesión). Esa sesión nueva se juzga como cualquier otra: red nueva, sin aprender hasta que pasa la espera.

## Validación humana pendiente

Los tests prueban que el código hace lo que se diseñó, **no que el diseño tenga sentido con uso real.** Cada ensayo con personas cambió algo:
- el atacante alcanzaba a cambiar la cuenta;
- la IP del dueño rotaba;
- el dueño con laptop nueva quedaba bloqueado;
- la IA respondía `step_up`, que nadie aplica.

Hay que probar estos casos **con personas, la app real y redes reales**, y anotar qué pasó contra lo esperado. Cada caso es una pregunta de producto, no solo de código.

| # | Caso | Lo esperado hoy | Qué hay que decidir u observar |
|---|---|---|---|
| 1 | Dueño en su red estable y su dispositivo, uso normal | Todo `normal`, sin decisiones | ¿Aparecen alertas falsas? |
| 2 | Dueño con datos móviles o CGNAT (la IP rota) | `review` en el primer acceso de cada sesión | ¿Cuánto ruido es aceptable en el panel? |
| 3 | Dueño en otra red (viaje, café) con su dispositivo, revisa facturas | `review` con la explicación de la IA, sin bloqueo | ¿La explicación convence? |
| 4 | Dueño con **dispositivo nuevo** (laptop nueva) que cambia la cuenta bancaria | La IA bloquea (10 de 10 en la medición) | ¿Está bien bloquear al dueño legítimo? ¿Cómo se desbloquea a sí mismo? |
| 5 | Dueño de viaje (otra red y otro dispositivo) en una ruta crítica | Bloqueo | Igual que el caso 4: es el falso positivo más caro |
| 6 | Atacante (otra red y otro dispositivo) va directo a cambiar la cuenta | `block` **antes** de ejecutar (`403`, la cuenta no cambia) | Confirmar en cada ensayo |
| 7 | Atacante que solo **mira** la cuenta bancaria (ruta `sensitive`) | La IA lo bloqueó en el ensayo | ¿Es demasiado agresivo si el dueño la mira desde otra red? |
| 8 | Atacante con el mismo operador que el dueño (`/24` cercana) | Depende de que caiga en otra `/24` | ¿Pasa seguido? |
| 9 | Atacante que repite desde el mismo teléfono, pasados 10 min | Corregido: no se aprende mientras el bloqueo siga abierto | Validar la corrección en un ensayo |
| 10 | Script en la laptop del dueño (`--scenario bot`) | Bloqueo solo de sesión; el dueño sigue entrando | ¿El dueño realmente no queda afuera? |
| 11 | Usuario legítimo muy rápido, o app con muchas llamadas por página | No debería disparar la ráfaga (piso 30/min) | Los umbrales (3× lo habitual, 30/min) **no están medidos** |
| 12 | Usuario **sin historial** (nuevo) que usa una ruta crítica | Bloqueo por regla (red nueva + ruta nunca usada) | ¿Qué hacemos con los usuarios nuevos? Es pregunta abierta en `PENDIENTES.md` |
| 13 | "Esto era normal" en el panel | Levanta el bloqueo y el perfil lo aprende | ¿Quién puede hacerlo? (decisión 3) ¿El dueño entiende que enseña al perfil? |
| 14 | Motor caído o lento | La ruta crítica pasa (*fail-open*, timeout de 12 s) | ¿Aceptable para un cliente real? |
| 15 | Proyecto en `observe` | Nada se bloquea, todo se registra | ¿El panel deja claro que no se bloqueó? (`/v1/activity` sigue mostrando `block`) |
| 16 | Proyecto con `ai_enabled = 0` | Solo reglas | ¿Se pierde mucho sin IA? |
| 17 | Lo que ve el panel durante un ataque | Escalada `review` → `step_up` → `block` (3 o 4 decisiones) | ¿Se entiende o confunde? |
| 18 | La explicación de la IA en el panel | Llega entre 3 y 12 s después del bloqueo; antes se ve el texto de la regla | ¿Se nota el cambio? ¿Confunde? |

Anota los resultados en `docs/PENDIENTES.md` (sección "Por medir"), con fecha, redes y `decision_id`. `DEMO.md` pide unas 20 vueltas por momento de la demo.

## Advertencias

- **El panel desplegado necesita `VERIDIAN_PANEL_TOKEN`.** Desde U6, la clave del SDK recibe `403` en decisiones, actividad, resumen, usuarios y perfil. Hay que configurarlo en el motor de Render, con el mismo valor en el panel. Sin eso, el panel recibe `401`. Configurar también:
  - `VERIDIAN_BOOTSTRAP_OWNERS`: los correos del equipo;
  - `VERIDIAN_ORG_CREATORS`, opcional.
- **Los scripts de ensayo** (`seed_demo_history.py`, `send_sample_events.py`) siguen enviando bien. Pero al final, cuando leen el perfil y las decisiones, ahora muestran `403`: hay que pasarlos al acceso del panel.
- **Cada push a `main` despliega el motor en Render,** con unos segundos de `502`: el disco impide desplegar sin corte.
- **No hay vuelta atrás automática de las migraciones.** Volver a código viejo exige restaurar la copia `.pre-NNNN` del disco.
- **`VERIDIAN_TRUSTED_PROXIES=2` en el motor (medido el 2026-10-25).**
  - Cómo se midió: 31 claves malas con un `X-Forwarded-For` falso y fijo de tres valores.
  - Con `3`, el motor tomaba el valor falso: esa IP inventada quedaba en `429` y con otra IP inventada se volvía a entrar. Cualquiera podía esquivar el límite de fallos rotando el encabezado.
  - Render agrega dos entradas delante del motor. La app de demo mide 3 (con navegadores, `edge/README.md`); no se volvió a medir.
- **Quitar un token de `VERIDIAN_TOKENS` ya no lo revoca:** queda importado como clave. Para revocarlo hay que usar el contrato 12 (U6) o poner `revoked_at` en `api_key`.
- **`docs/PENDIENTES.md` tiene los hallazgos del ensayo real:**
  - la IP del dueño rota por CGNAT;
  - la escalada `review` → `step_up` → `block` en el panel;
  - el dueño con dispositivo nuevo queda bloqueado en rutas críticas.
