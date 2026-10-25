# Guion del pitch

Unos 3 minutos en total, con la estructura que pidieron los organizadores (ver [DEMO.md](DEMO.md)). Slides: [Veridian pitch](https://claude.ai/artifact/Q8mtkH7984s4Gxry4ReXQA).

Se lee en voz alta a ritmo normal (unas 2,5 palabras por segundo). Cada bloque indica qué se ve y qué se dice. Todas las slides van antes de la demo y suman menos de 60 s.

| Bloque | Tiempo | Pantalla |
|---|---|---|
| 1. Portada | ~7 s | Logo y «Veridian» |
| 2. Problema | ~15 s | Slide del problema |
| 3. Instalación | ~15 s | Snippet y rutas clasificadas |
| 4. Cómo funciona | ~15 s | Flujo Acceso → Perfil → Regla → IA → Bloqueo |
| 5. Demo | ~130 s | App y panel, lado a lado |
| 6. Cierre | ~5 s | «Gracias.» |

## 1. Portada (~7 s)

Dato del [Verizon DBIR 2025](https://www.verizon.com/business/resources/articles/credential-stuffing-attacks-2025-dbir-research/) (22 %).

> Una de cada cinco filtraciones de datos comienza con una credencial robada.
>
> Por eso creamos Veridian.

**Cambio de slide a Problema.**

## 2. Problema (~15 s)

**Pantalla:** «Un sistema no sabe si quien entra es realmente el dueño de la cuenta», con las tarjetas «Contraseña filtrada», «Código 2FA robado» y «Sesión robada».

**Título:**

> Porque un sistema no sabe si quien entra es realmente el dueño de la cuenta. Una vez dentro, no vuelve a comprobar quién eres.

**Tarjetas:**

> Con una contraseña filtrada, un código 2FA robado o una sesión robada, el sistema deja pasar a otra persona como si fueras tú.

**Cambio de slide a Instalación.**

La frase de las tarjetas no llama «credencial» a la sesión ni la cuelga de la cifra: el dato de Verizon habla de credenciales robadas, y la sesión es lo que el sistema entrega después de usarlas.

**Apertura alternativa (no definitiva):** una pregunta al público: «¿Cuántos saben si alguien más está usando su cuenta ahora mismo?». Riesgo: puede abrir el tema de las cuentas compartidas.

## 3. Instalación (~15 s)

**Pantalla:** «Lo instalas con una llamada y entiende tu API solo». A la izquierda, el snippet de `add_middleware`; a la derecha, tres rutas con su clase: `POST /payouts/bank-account` critical, `GET /invoices/export` sensitive, `GET /invoices` normal.

> Instalarlo es una llamada en tu app: tu clave y cómo identificas al usuario.
>
> Desde ahí, una IA lee tu API y clasifica cada ruta: crítica si mueve dinero, sensible si expone datos, normal el resto.

**Cambio de slide a Cómo funciona.**

## 4. Cómo funciona (~15 s)

**Pantalla:** «Una regla ve lo raro; la IA entiende si es un robo», con el flujo Acceso → Perfil → Regla → IA → Bloqueo y el caso «red nueva + ruta critical nunca usada → Bloqueado».

> En cada acceso, Veridian lo compara con el perfil del usuario. Una regla detecta lo raro y la IA decide si es un robo.
>
> En una ruta crítica, el bloqueo llega antes de que la acción se ejecute. Es lo que van a ver ahora.

## 5. Demo (~130 s)

**Roles** (de [DEMO.md](DEMO.md)): el presentador usa su laptop con el wifi del evento; un compañero hace de atacante desde otra red; otro tiene el video de respaldo listo.

**Pantalla:** la app de la startup con la sesión del presentador y el panel de Veridian, lado a lado.

**Momento 1: el dueño entra (~30 s).** El presentador entra y revisa sus facturas.

> Esta es una app de facturas, y este es el panel de Veridian. Entro como siempre y reviso mis facturas.
>
> El panel dice «Normal»: Veridian ya conoce mi patrón. Esta red, este horario, esta sección.

**Momento 2: entra otra persona (~50 s).** El atacante entra con la contraseña del presentador y va directo a cambiar la cuenta bancaria de pagos.

> Ahora mi compañero tiene mi contraseña. Entra desde otra red y va directo a cambiar la cuenta bancaria donde cobro.
>
> El panel lo marca «Bloqueado». Y la IA explica por qué: una red que nunca usé, una sección crítica que yo nunca toco y un recorrido directo a ella. No era yo.

Leer en voz alta la explicación que muestra el panel.

**Momento 3: el dueño corrige (~20 s, opcional).** Si sobra tiempo, mostrar «Esto era normal» sobre una decisión de prueba.

> Y si Veridian se equivoca con el dueño, un clic en «Esto era normal» levanta el bloqueo y ese acceso pasa a ser parte de su perfil.

**Momento 4: qué usamos y cuánto avanzamos (~20 s).**

> Para la demo precargamos mi historial; en producción, el perfil se aprende solo. Lo construimos con Python, FastAPI, SQLite y Claude Opus 5.5. [Completar el domingo con lo que de verdad funcione en la demo.]

**Si la demo falla:** pasar al video de respaldo sin explicar el fallo.

## 6. Cierre (~5 s)

**Pantalla:** «Gracias.»

> Gracias. Somos Veridian: que tus credenciales solo te sirvan a ti.

## Preguntas probables

Hay una slide oculta al final del deck, «Sigue protegiendo aunque la IA falle o se equivoque», para mostrar si preguntan por fallos de la IA.

- **¿Y si la IA falla o no responde?** Decide una regla determinista: bloquea en rutas críticas y alerta en el resto.
- **¿Un atacante puede entrenar a Veridian para que lo vea como normal?** No: los accesos bloqueados o en alerta nunca entran al perfil.
- **¿Y si la empresa usa 2FA?** El segundo factor protege la entrada, no lo que pasa después. Los códigos se roban con phishing y las sesiones abiertas se copian con malware; Veridian mira cada acceso después del login.
- **¿Y la biometría, como Face ID?** El teléfono verifica tu cara, pero el sistema solo recibe una credencial y después una sesión. Si te roban la sesión, la cara no vuelve a pedirse.
- **¿Qué pasa si el dueño viaja o trabaja de noche?** Por eso juzga una IA y no una regla: distingue un día distinto de otra persona. Si se equivoca, el panel tiene «Esto era normal» para corregirlo.
- **¿Tienen cifras?** El 22 % de las filtraciones de datos empiezan con una credencial robada ([Verizon DBIR 2025](https://www.verizon.com/business/resources/articles/credential-stuffing-attacks-2025-dbir-research/)), y una empresa tarda en promedio 241 días en detectar y contener una filtración ([IBM 2025](https://newsroom.ibm.com/2025-07-30-ibm-report-13-of-organizations-reported-breaches-of-ai-models-or-applications,-97-of-which-reported-lacking-proper-ai-access-controls)).
- **¿En qué se diferencian de Akamai, Traceable o Salt?** Ellos lo hacen para grandes empresas. Veridian es para startups sin equipo de seguridad, instalado en minutos.

## Verificado en el código

- **El bloqueo de la demo es inmediato.** En rutas `critical`, el middleware consulta al motor (`POST /v1/check`) antes de ejecutar la ruta y responde 403 si la decisión es bloquear (`sdk/python/src/veridian_sdk/middleware.py`). La regla «ruta critical nunca usada desde una red nueva» bloquea sin esperar a la IA (`engine/veridian_engine/rules.py`); la IA solo explica ese bloqueo.
- **El snippet de la slide 3** usa la firma real del middleware: `token`, `base_url` y `user_id`. El snippet del README omite `base_url`, que es obligatorio.

## Pendiente antes de presentar

- **Tiempos.** Cronometrar cada bloque en los ensayos; las slides deben quedar por debajo de 60 s.
