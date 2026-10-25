# Pendientes

## Por construir (mínimo para la demo)

1. **App de ejemplo de una startup:** login, facturas y cuenta bancaria de pagos.
2. **Middleware:** después del login, envía cada acceso (identificador del usuario, IP, dispositivo, endpoint, hora, bytes) en segundo plano y aplica la lista de bloqueo.
3. **Servidor:** hecho y desplegado en Render (`engine/`).
   - recibe eventos;
   - arma el perfil por usuario;
   - llama a la IA solo ante un desvío;
   - publica la lista de bloqueo.

   Falta: las señales finas (ritmo y recorrido).
4. **Panel:**
   - usuarios y su estado;
   - la explicación de cada decisión;
   - "esto era normal".
5. **Clasificación de endpoints** a partir de la especificación OpenAPI (crítico, sensible, normal).
   - El módulo existe (`engine/scripts/classify_openapi.py`), pero no está conectado.
   - Hoy manda `engine/routes.yaml`, escrito a mano.
   - Si la IA falla, todo queda `normal`: su salida se usa como borrador que revisa una persona.
   - **Decisión (2026-10-25):** debe ser automático. Tarea 6 del Dev B en `HANDOFF.md`.
6. **Historial sembrado** para la demo.
   - Herramientas listas:
     - `engine/scripts/prepare_demo.py` borra los datos del proyecto (`DELETE /v1/projects/{id}/data`), siembra y verifica; el reset se eliminó con U13;
     - `engine/scripts/seed_demo_history.py` carga el historial.
   - Falta medir la IP del wifi del evento y el `device_id` del presentador.

Referencia reutilizable en el repo de Centinela:
- recepción de eventos: `POST /api/ingest/access`;
- perfil por credencial: `domain/keywatch.py`;
- lector de logs de nginx: `forward_nginx.py`;
- evaluación de la IA: `evals/key_judgment/`.

## Por medir

- **Evaluación v2** (en curso): ¿la IA le gana a un puntaje con pesos y a una regresión logística? ¿Permite los viajes si no puede preguntar?
- **Ensayo de la demo,** unas 20 veces por momento, con el sistema real.
  - **Primera medición del juez solo,** el 2026-10-25, con un perfil armado a mano y sin servidor:
    - paso 2 (dueño en otra red): `alert` en 10 de 10 corridas;
    - paso 3 (atacante): `block` en 10 de 10 corridas.
  - **La explicación de la IA llega entre 3 y 12 s después;** el bloqueo es inmediato.
- **Primer ensayo real (2026-10-25),** con la app, el middleware, el motor y el panel en Render, el dueño en una laptop y el atacante en un teléfono con datos móviles:
  - **El atacante quedó bloqueado:**
    - la decisión llegó 1 s después de cambiar la cuenta;
    - al reintentar recibió `403`;
    - la explicación es de la IA.
  - **Pero el cambio de cuenta se aplicó.** Se corrigió con la consulta síncrona (contrato 9), que falta conectar en el middleware.
  - **La IP pública del dueño rotó entre 3 redes en minutos:** `38.121.211.x`, `186.167.82.x` y `186.167.53.x`. El primer acceso de cada sesión salió `review`.
    - En la demo, el presentador usa una red estable.
    - En la calibración, se navega unos minutos y se siembran todas las IPs.
  - **Por cada ataque, el panel muestra una escalada** `review` → `step_up` → `block`, en 3 o 4 decisiones. Puede contarse como "respuesta por grados", pero el panel tiene que mostrarlo claro.
- **Consulta síncrona contra Render** (sin middleware, haciendo de middleware):
  - dueño `allow` en 0,4 s;
  - caso obvio `block` en 0,6 s;
  - casos grises `block` por la IA en 3,0 y 7,8 s.
  - **Estabilidad medida después,** con el juez solo: 3 casos grises de ruta crítica × 10 corridas por modelo.
    - **Decisión:** Opus, Sonnet y Haiku respondieron `block` en los 3 casos, 10 de 10.
    - **Tiempos (mediana):**
      - Opus, el caso "dueño con laptop nueva": 11,7 s. Pasa el tope de 10 s, así que la regla de respaldo dejaría pasar la petición.
      - Haiku: unos 2 s en todos los casos.
    - **Por eso la consulta síncrona usa Haiku** (`VERIDIAN_SYNC_MODEL`), y el juicio en segundo plano sigue con Opus.
    - **En rutas críticas, la IA bloquea todo desvío,** incluido el dueño en otra red con su propio dispositivo.
- **Tiempo de instalación** del middleware en una app nueva.
- **Validación del comportamiento** (tarea 7, 2026-10-25): 17 de 17 casos sintéticos como se esperaba, 3 corridas cada uno (`HALLAZGOS.md`). Falta repetirla con personas y redes reales: `docs/PRUEBAS-MANUALES.md`.
- **Robo lento real,** repartido en horas o días: la evaluación solo cubrió 8 minutos. Pasada la espera de aprendizaje (10 min), lo que no tuvo una decisión abierta se aprende.

## Riesgos

- **La IA no es determinista:** puede bloquear un uso legítimo en vivo.
- **El dueño cambiando la cuenta bancaria fuera de lo habitual queda bloqueado, por decisión** (2026-10-25, ver `HALLAZGOS.md`, "Validación del motor real"): de viaje con su dispositivo o con un dispositivo nuevo en su red. No se distingue de un infostealer ni de otra persona en su red. El costo es un falso positivo en el caso más caro; la salida es "Esto era normal" y el autodesbloqueo por correo.
- **Usuarios sin historial:** el primer cambio de cuenta de un usuario que Veridian todavía no conoce se bloquea por regla (caso N1). El día que un cliente instala Veridian, **todos** sus usuarios están así. Hace falta decidir un periodo de solo observar por usuario, o confiar en el autodesbloqueo.
- **Umbral de ritmo sin medir con personas reales:** 28 accesos por minuto no lo disparan (L6), pero una SPA con muchas llamadas por pantalla puede pasar de 30 por minuto (`VERIDIAN_RATE_FLOOR`). Probarlo con la app de un cliente real.
- **Hecho (2026-10-25, 12:45 UTC): token del cliente `demo` rotado.** Clave nueva `key_10bfa24bc88bfc38bf7600a8` (contrato 12) puesta en `VERIDIAN_TOKEN` de `veridian-demo`; las dos claves heredadas de `VERIDIAN_TOKENS` quedaron revocadas (el token viejo responde `401`). `VERIDIAN_RESET_TOKEN` se borró del motor. Quien usara el token viejo en un `.env` local tiene que pedir la clave nueva.
- **Si la IA no le gana a un puntaje sin IA,** el mensaje se limita a "entiende tu sistema y explica".
- **Datos personales:** las IPs cuentan como datos personales en muchas leyes. Hay que cuidar la retención y el cifrado.
- **Claves de integraciones:** no hay persona a quien pedirle un segundo factor; habría que escalar al administrador.

## Preguntas abiertas

- ¿Cuántos días de historial necesita un perfil para ser confiable?
- ¿Qué pasa con un usuario nuevo, sin historial?
- ¿Se puede ver la huella TLS desde el middleware, o hace falta el servidor web?
