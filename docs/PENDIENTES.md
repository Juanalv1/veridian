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
6. **Historial sembrado** para la demo.
   - Herramientas listas:
     - `engine/scripts/reset_client.py` reinicia los datos del cliente;
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
- **Tiempo de instalación** del middleware en una app nueva.

## Riesgos

- **La IA no es determinista:** puede bloquear un uso legítimo en vivo.
- **Si la IA no le gana a un puntaje sin IA,** el mensaje se limita a "entiende tu sistema y explica".
- **Datos personales:** las IPs cuentan como datos personales en muchas leyes. Hay que cuidar la retención y el cifrado.
- **Claves de integraciones:** no hay persona a quien pedirle un segundo factor; habría que escalar al administrador.

## Preguntas abiertas

- ¿Cuántos días de historial necesita un perfil para ser confiable?
- ¿Qué pasa con un usuario nuevo, sin historial?
- ¿Se puede ver la huella TLS desde el middleware, o hace falta el servidor web?
