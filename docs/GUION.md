# Guion del pitch

**[Slide 1: Portada]**

> Una de cada cinco filtraciones de datos comienza con una credencial robada.
>
> Por eso creamos Veridian.

**[Slide 2: Problema]**

> Porque un sistema no sabe si quien entra es realmente el dueño de la cuenta. Una vez dentro, no vuelve a comprobar quién eres.
>
> Con una contraseña filtrada, un código 2FA robado o una sesión robada, el sistema deja pasar a otra persona como si fueras tú.

**[Slide 3: Cómo funciona]**

> Veridian sí lo comprueba, en cada acceso. Antes de empezar, una IA recorre las rutas de tu API y entiende qué hace cada una para clasificarlas según su impacto: cambiar la cuenta bancaria no es lo mismo que ver una factura. Y Veridian aprende cómo trabajas tú: desde dónde entras, a qué hora y qué rutas usas.
>
> Si alguien entra desde una red nueva y va directo a cambiar la cuenta donde cobras, algo que tú nunca haces, la IA decide si eres tú en un día distinto o es otra persona.
>
> En una ruta crítica, el bloqueo llega antes de que la acción se ejecute.

**[Slide 4: Instalación]**

> Instalarlo es una llamada en tu app: tu clave y cómo identificas al usuario.
>
> Desde ahí, una IA lee tu API y clasifica cada ruta: crítica si mueve dinero, sensible si expone datos, normal el resto. Es lo que van a ver ahora.

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
