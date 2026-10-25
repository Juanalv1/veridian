# Pruebas a mano

Guía para probar Veridian con personas, la app real y redes reales. Complementa la medición automática de `engine/scripts/behavior_eval.py` (resultados en `HALLAZGOS.md`, "Validación del motor real"): esa medición usa casos sintéticos; esta guía prueba lo que solo se ve con un navegador y una red de verdad.

## Qué se necesita

- **App de demo:** https://veridian-demo.onrender.com (contraseña: `DEMO_PASSWORD`, pedirla al equipo; no va en el repo).
- **Panel:** https://veridian-panel.onrender.com, proyecto de la app de demo, con rol admin u owner.
- **Una laptop** en una red estable (el "dueño").
- **Un teléfono con datos móviles,** sin wifi (el "atacante" u "otra red").
- **Usuario de prueba:** `luis@facturas.demo` (`u_7`). Ana (`u_42`) queda para la demo: no la uses aquí.

## Cómo se simula cada cosa

| Quiero simular | Cómo |
|---|---|
| Otra red | El teléfono con datos móviles (apaga el wifi) |
| Otro dispositivo | Otro navegador o una ventana privada: el dispositivo es la cookie `vrd_did`, y una ventana privada no la tiene |
| El mismo dispositivo en otra red | No se puede con dos aparatos; usa la laptop conectada al hotspot del teléfono |
| Volver a empezar | Borrar los datos de Luis con la API (el panel todavía no tiene ese botón). Ver "Al terminar" |

## Antes de empezar

1. En el panel, **Proyectos**: el proyecto de la demo tiene que estar en **enforce**. En `observe` nada se bloquea.
2. En **Rutas críticas** tienen que estar `POST /payouts/bank-account` como crítica, y `GET /payouts/bank-account` y `GET /invoices/export` como sensibles.
3. **Crea el historial de Luis:** entra con la laptop y navega unos 5 minutos (facturas, el detalle de una factura). Espera **10 minutos** antes de seguir: es lo que tarda el motor en aprender lo nuevo (`VERIDIAN_LEARN_DELAY_MIN`). Sin historial, cualquier cambio de la cuenta se bloquea (caso 9).

## Casos

Para cada caso anota en la tabla del final lo que pasó y el `decision_id` que muestra el panel (Actividad → la decisión).

### Usos legítimos (no deberían bloquearse)

1. **Uso normal.** La laptop, navega facturas 3 minutos.
   - Esperado: Actividad sin decisiones; Resumen dice "Todo normal hoy".
2. **El dueño en otra red.** La laptop con el hotspot del teléfono, mira facturas.
   - Esperado: una decisión `alert` o `review` con explicación de la IA (llega entre 3 y 12 s después). Sin `403`.
   - Fíjate: ¿la explicación nombra lo que coincide con Luis (su dispositivo)?
3. **Dispositivo nuevo, misma red.** Ventana privada en la laptop, mira facturas.
   - Esperado: `alert`, sin bloqueo.
4. **El dueño solo mira la cuenta bancaria desde otra red.** Hotspot, abre la página de cuenta bancaria sin cambiarla.
   - Esperado: no bloquear (ver la medición, caso L7).
5. **Muchos clics seguidos.** La laptop, abre y cierra facturas lo más rápido que puedas durante un minuto.
   - Esperado: nada. El límite de ritmo es 30 accesos por minuto como mínimo (`VERIDIAN_RATE_FLOOR`). Si te bloquea, anótalo: el umbral es bajo para personas reales.

### Ataques (deberían bloquearse)

6. **Otra persona cambia la cuenta.** El teléfono con datos, entra como Luis y cambia la cuenta bancaria.
   - Esperado: `403` al guardar y **la cuenta no cambia** (recárgala desde la laptop para confirmarlo). En el panel, un bloqueo que cita la regla crítica; la explicación de la IA la reemplaza segundos después.
7. **El atacante vuelve.** Pasados 15 minutos, con el mismo teléfono, apaga y prende los datos (cambia la IP), entra de nuevo y repite.
   - Esperado: bloqueo otra vez. El teléfono no se aprende mientras el primer bloqueo siga abierto.
8. **Descarga masiva.** El teléfono, abre la exportación de facturas (`/invoices/export`) y muchas facturas seguidas.
   - Esperado: bloqueo por ritmo o por la IA.

### Casos que hoy se bloquean por decisión

9. **El dueño cambia la cuenta desde otra red, o desde un dispositivo nuevo.**
   - Esperado: **bloqueo.** Para Veridian es igual que alguien que robó la cookie del dispositivo (un infostealer) o que alguien en la misma red del dueño. La salida es "Esto era normal" en el panel; el autodesbloqueo por correo está en camino (`docs/DESBLOQUEO-POR-CORREO.md`).
   - Fíjate: ¿el mensaje que ve el usuario bloqueado le dice qué hacer?

### El panel

10. **"Esto era normal".** Sobre el bloqueo del caso 9, toca el botón.
    - Esperado: el bloqueo se levanta y, en menos de ~2 s, la app deja de devolver `403`. La próxima vez, esa red o ese dispositivo ya no son nuevos.
11. **Lo que se ve durante un ataque (caso 6).** Cuenta cuántas decisiones aparecen y en qué orden.
    - Fíjate: ¿se entiende la escalada `review` → `step_up` → `block`, o confunde?
12. **Modo `observe`.** Pasa el proyecto a observe, repite el caso 6 y vuelve a enforce.
    - Esperado: la cuenta **sí** cambia, y el panel deja claro que no se bloqueó.
    - Al terminar, vuelve a dejarlo en **enforce**.

## Al terminar

- Marca "Esto era normal" en los bloqueos que sigan abiertos, o el teléfono seguirá bloqueado también para Ana en la demo (los bloqueos de IP son de la IP, no del usuario).
- Borra los datos de Luis. Lo hace quien tenga `VERIDIAN_PANEL_TOKEN` y sea admin del proyecto:

  ```sh
  curl -X DELETE https://veridian-engine.onrender.com/v1/projects/<id del proyecto>/users/u_7 \
    -H "Authorization: Bearer $VERIDIAN_PANEL_TOKEN" -H "X-Veridian-Actor: <tu person id>"
  ```
- Antes de un ensayo de la demo, corre `engine/scripts/prepare_demo.py` (ver su docstring).

## Resultados

Copia la tabla en `PENDIENTES.md` ("Por medir") con fecha y redes.

| # | Fecha y hora | Red (operador) | Lo que pasó | Esperado | `decision_id` |
|---|---|---|---|---|---|
| 1 | | | | sin decisiones | |
| 2 | | | | `alert`, sin `403` | |
| 3 | | | | `alert`, sin `403` | |
| 4 | | | | sin bloqueo | |
| 5 | | | | nada | |
| 6 | | | | `403`, la cuenta no cambia | |
| 7 | | | | bloqueo | |
| 8 | | | | bloqueo | |
| 9 | | | | bloqueo | |
| 10 | | | | se levanta en ~2 s | |
| 11 | | | | | |
| 12 | | | | la cuenta cambia, el panel lo dice | |
