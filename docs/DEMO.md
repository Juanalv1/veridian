# Pitch y demo

## Estructura pedida por los organizadores

| Bloque | Tiempo | Preguntas que debe responder |
|---|---|---|
| Problema | ~20 s | ¿Cuál es? ¿A quién afecta? ¿Qué tan relevante? ¿Por qué no está resuelto? |
| Solución / impacto / ambición | ~20-30 s | ¿Cuál es? ¿Qué aporta de nuevo? ¿Qué tan ambiciosa? ¿A quién impacta? |
| Demo / cómo lo resuelven | ~125-140 s | Demo en vivo. ¿Cómo funciona? ¿Qué usaron? ¿Cuánto avanzaron? |
| Slide final | — | Dar gracias |

Regla de los títulos: cada título da un mensaje ("aumenta la productividad 10x"), nunca "Problema" o "Solución".

## Pitch

1. **"Con una clave robada, nada frena al atacante"** (20 s): startups que dan acceso a su sistema; 22 %, 241 días, USD 4,99 M; las soluciones existentes son enterprise.
2. **"Veridian detecta cuándo otra persona usa tus credenciales"** (25 s): la IA distingue un uso raro de un robo y se instala en minutos. Ambición: hoy APIs; mañana SSH, VPN y cualquier puerta con credenciales. De 241 días a segundos.
3. **Demo en vivo** (130 s), ver abajo.
4. **Slide final:** gracias y la frase.

## Demo

**Roles:**
- **Presentador ("víctima"):** su laptop, con el wifi del evento.
- **Un compañero (atacante):** otra laptop, con otra red (por ejemplo, el hotspot de su teléfono, idealmente de otro operador).
- **Otro compañero:** con el video de respaldo.

**Proyector:** la app de la startup con la sesión del presentador y el panel de Veridian, lado a lado. La pantalla del atacante no se proyecta: lo que hace aparece en el panel.

**Guion:**
1. **El presentador entra normal** y revisa sus facturas. En el panel: normal, con su perfil (esta red, este horario, facturas).
2. **(Opcional) El presentador cambia de red** y hace lo mismo. En el panel: raro, pero permitido, con la explicación. Solo entra si la evaluación muestra que la IA no bloquea estos casos.
3. **El atacante entra con la contraseña del presentador** desde su red y va directo a cambiar la cuenta bancaria de pagos. En el panel: red nunca vista, endpoint crítico que el dueño nunca usa, recorrido directo. **Bloqueado**, con la explicación de la IA.
4. **Cómo funciona:** diagrama y 3 líneas de código.
5. **Qué usamos y cuánto avanzamos:** lo que de verdad funcione el domingo.

**Antes de subir:**
- Precargar el historial del presentador con la IP del wifi del evento (medirla el domingo temprano), incluyendo uso en fin de semana a esa hora. Decirlo abiertamente: "en producción esto se aprende solo; aquí lo precargamos".
- Ensayar cada momento unas 20 veces en el lugar.
- Grabar un video de respaldo de una corrida buena.

**Riesgo principal:** la IA no es determinista. Si en los ensayos el paso 2 se bloquea alguna vez, se quita de la demo, o se exige que el bloqueo salga en dos decisiones seguidas.
