# Guion del pitch

Unos 3 minutos en total, con la estructura que pidieron los organizadores (ver [DEMO.md](DEMO.md)). Slides: [Veridian pitch](https://claude.ai/artifact/Q8mtkH7984s4Gxry4ReXQA).

Se lee en voz alta a ritmo normal (unas 2,5 palabras por segundo). Cada bloque indica qué se ve y qué se dice.

| Bloque | Tiempo | Pantalla |
|---|---|---|
| 1. Portada | ~5 s | Logo y «Veridian» |
| 2. Problema | ~20 s | Slide del problema |
| 3. Solución | ~25 s | Slide verde de la solución |
| 4. Demo | ~130 s | App y panel, lado a lado |
| 5. Cierre | ~5 s | «Gracias.» |

## 1. Portada (~5 s)

> Hola, somos Veridian.

## 2. Problema (~22 s)

**Pantalla:** «Un sistema no sabe si quien entra es realmente el dueño de la cuenta», con las tarjetas «Contraseña filtrada», «Código 2FA robado» y «Sesión robada».

**Escena** (es un ejemplo, no un caso real; es el mismo ataque que muestra la demo):

> Son las 3 de la mañana. Alguien entra a tu sistema con la contraseña de tu contadora y cambia la cuenta donde cobras. Para el sistema, es ella.

**Título:**

> Porque un sistema no sabe si quien entra es realmente el dueño de la cuenta. Y una vez dentro, no vuelve a comprobar quién eres.

**Tarjetas:**

> Y no importa cuánto protejas la entrada: la contraseña se filtra, el código de verificación se roba, la sesión se copia. En los tres casos, el sistema ve credenciales válidas y deja pasar.

Son unas 55 palabras. Si hay que recortar para llegar a 20 s, se quita «Y una vez dentro, no vuelve a comprobar quién eres», que ya está escrita en la slide.

**Apertura alternativa (no definitiva):** en lugar de la escena, una pregunta al público: «¿Cuántos saben si alguien más está usando su cuenta ahora mismo?». Riesgo: puede abrir el tema de las cuentas compartidas.

## 3. Solución (~25 s)

**Pantalla:** «Veridian aprende cómo trabajas y nota cuando no eres tú».

> Por eso creamos Veridian. Aprende cómo usa cada persona su cuenta: desde dónde entra, a qué hora y qué partes del sistema usa.
>
> Cuando algo no encaja, una IA mira el contexto y decide si es otra persona o solo un día distinto. Si es otra persona, le corta el acceso.
>
> Está hecho para startups sin equipo de seguridad. Hoy protege APIs; mañana, cualquier acceso con credenciales. Se lo mostramos.

## 4. Demo (~130 s)

**Roles** (de [DEMO.md](DEMO.md)): el presentador usa su laptop con el wifi del evento; un compañero hace de atacante desde otra red; otro tiene el video de respaldo listo.

**Pantalla:** la app de la startup con la sesión del presentador y el panel de Veridian, lado a lado.

**Momento 1: el dueño entra (~25 s).** El presentador entra y revisa sus facturas.

> Esta es una app de facturas, y este es el panel de Veridian. Entro como siempre y reviso mis facturas.
>
> El panel dice «Normal»: Veridian ya conoce mi patrón. Esta red, este horario, esta sección.

**Momento 2: entra otra persona (~45 s).** El atacante entra con la contraseña del presentador y va directo a cambiar la cuenta bancaria de pagos.

> Ahora mi compañero tiene mi contraseña. Entra desde otra red y va directo a cambiar la cuenta bancaria donde cobro.
>
> El panel lo marca «Bloqueado». Y la IA explica por qué: una red que nunca usé, una sección crítica que yo nunca toco y un recorrido directo a ella. No era yo.

Leer en voz alta la explicación que muestra el panel.

**Momento 3: cómo funciona (~25 s).**

> Funciona así: un middleware en la app le reporta a Veridian cada acceso. Veridian aprende el perfil de cada usuario, una IA juzga lo que se sale de lo normal y el middleware bloquea.
>
> Para la demo precargamos mi historial; en producción, el perfil se aprende solo.

**Momento 4: qué usamos y cuánto avanzamos (~20 s).**

> Lo construimos con Python, FastAPI, SQLite y Claude Opus 5.5. [Completar el domingo con lo que de verdad funcione en la demo.]

**Si la demo falla:** pasar al video de respaldo sin explicar el fallo.

## 5. Cierre (~5 s)

**Pantalla:** «Gracias.»

> Gracias. Somos Veridian: que tus credenciales solo te sirvan a ti.

## Preguntas probables

- **¿Y si la empresa usa 2FA?** El segundo factor protege la entrada, no lo que pasa después. Los códigos se roban con phishing y las sesiones abiertas se copian con malware; Veridian mira cada acceso después del login.
- **¿Y la biometría, como Face ID?** El teléfono verifica tu cara, pero el sistema solo recibe una credencial y después una sesión. Si te roban la sesión, la cara no vuelve a pedirse.
- **¿Qué pasa si el dueño viaja o trabaja de noche?** Por eso juzga una IA y no una regla: distingue un día distinto de otra persona. Si se equivoca, el panel tiene «Esto era normal» para corregirlo.
- **¿Tienen cifras?** El 22 % de las filtraciones de datos empiezan con una credencial robada ([Verizon DBIR 2025](https://www.verizon.com/business/resources/articles/credential-stuffing-attacks-2025-dbir-research/)), y una empresa tarda en promedio 241 días en detectar y contener una filtración ([IBM 2025](https://newsroom.ibm.com/2025-07-30-ibm-report-13-of-organizations-reported-breaches-of-ai-models-or-applications,-97-of-which-reported-lacking-proper-ai-access-controls)).
- **¿En qué se diferencian de Akamai, Traceable o Salt?** Ellos lo hacen para grandes empresas. Veridian es para startups sin equipo de seguridad, instalado en minutos.

## Pendiente de verificar antes de presentar

- **Cuándo bloquea.** El guion dice que corta el acceso, sin decir «al instante». La IA juzga en segundo plano; confirmar en los ensayos si el bloqueo de la demo lo aplica la regla de rutas críticas (inmediato) o la IA (después de su decisión), y ajustar la frase.
- **Tiempos.** Cronometrar cada bloque en los ensayos; el de solución está justo en 25 s.
