# Guion del pitch

Slides 1 a 4: 45-50 s (medido en voz alta: 48,5 s, 2026-10-25).

**[Slide 1: Portada]**

> Una de cada cinco filtraciones de datos comienza con una credencial robada.
>
> Por eso creamos Veridian.

**[Slide 2: Problema]**

> Porque un sistema no sabe si quien entra es realmente el dueño de la cuenta. Una vez dentro, no vuelve a comprobar quién eres.
>
> Con una contraseña filtrada, un código 2FA robado o una sesión robada, el sistema deja pasar a otra persona como si fueras tú.

**[Slide 3: Cómo funciona]**

> Veridian sí lo comprueba, en cada petición: la compara con los datos que ya conoce de ese usuario y con el impacto de la ruta a la que va.
>
> La IA interviene cuando hay algo fuera de lo común: lo contrasta con la forma en que ese usuario suele comportarse, y decide si es la misma persona o es otra. Si es la misma, la petición sigue; si es otra, la bloquea antes de que la acción ocurra.

**[Slide 4: Instalación]**

> Y se integra en tu backend en solo tres líneas de código: le dices cómo identificas al usuario y, en la primera ejecución, clasifica automáticamente todas tus rutas.
>
> Así, cualquiera puede detener a quien usa credenciales que no son suyas, sin tener que montar toda una infraestructura de seguridad.
>
> Ahora, veámoslo en vivo.

**[Demo: app y panel lado a lado]**

**[El presentador entra y revisa sus facturas]**

> Esta es una app de facturas, y este es el panel de Veridian. Entro como siempre y reviso mis facturas.
>
> El panel dice «Normal»: Veridian ya conoce mi patrón. Esta red, este horario, esta sección.

**[El atacante entra desde otra red y va a cambiar la cuenta bancaria]**

> Ahora mi compañero tiene mi contraseña. Entra desde otra red y va directo a cambiar la cuenta bancaria donde cobro.
>
> El panel lo marca «Bloqueado». Y la IA explica por qué: una red que nunca usé, una sección crítica que yo nunca toco y un recorrido directo a ella. No era yo.

**[Leer la explicación del panel]**

> Para la demo precargamos mi historial; en producción, el perfil se aprende solo. Lo construimos con Python, FastAPI, SQLite y Claude Opus 5.5.

**[Slide 5: Gracias]**

> Gracias. Somos Veridian: que tus credenciales solo te sirvan a ti.
