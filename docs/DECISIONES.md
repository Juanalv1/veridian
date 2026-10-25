# Decisiones

Qué decidimos y por qué. Las cifras y fuentes están en [HALLAZGOS.md](HALLAZGOS.md).

## Producto

| Decisión | Por qué |
|---|---|
| **Un solo foco: detectar cuándo otra persona usa una credencial.** | Antes queríamos cubrir mucho (procesos, mineros, backdoors, prompt injection) y no sabíamos explicar el proyecto en 10 palabras. Quitar en vez de añadir. |
| **Frase:** "Detecta cuándo otra persona usa tus credenciales y las bloquea." | Dice el qué y la acción. El "cómo" (comportamiento, IA) se explica si preguntan. |
| **Cliente: startups que dan acceso a su sistema** a usuarios, clientes o integraciones, sin equipo de seguridad. | Las soluciones existentes (Akamai, Traceable, Salt) son enterprise. Cloudflare solo recomienda límites de volumen, y por sesión solo en Enterprise. |
| **No es necesario inventar algo nuevo:** tomamos algo que existe para grandes empresas y lo hacemos accesible. | Diferencia de mercado: instalación en minutos, sin equipo de seguridad. |
| **Nombre: Veridian.** | De *verify* + *identity*. Antes se llamaba Centinela, que sugería un vigilante general. |

## Cómo funciona

| Decisión | Por qué |
|---|---|
| **Middleware en la app del cliente, después de su login.** | Así la identidad la da la credencial que la app ya validó. La IP y el dispositivo no identifican al usuario: dicen si quien usa la credencial se comporta como su dueño. |
| **El middleware anota y bloquea; el servidor central aprende y decide.** | Veridian no entra al sistema del cliente ni tiene acceso a su base de datos. El bloqueo lo aplica la propia app con una lista que descarga. |
| **Servidor central en la nube, aislado por cliente.** | Simple para empezar. Más adelante, una versión instalable en la nube del cliente. |
| **El middleware envía en segundo plano y nunca frena una petición.** | Si Veridian se cae, la app sigue funcionando; solo deja de estar protegida. |
| **Alternativa sin código:** enviar los logs del servidor (nginx). | Para quien no quiere tocar la app. Además permite armar el perfil desde el primer día con el historial. |

## Aprendizaje e IA

| Decisión | Por qué |
|---|---|
| **El perfil lo arma código determinista, no la IA.** | Costo (millones de líneas), precisión (un modelo de lenguaje cuenta mal), auditabilidad y seguridad (el atacante controla parte de lo que llega a los logs). |
| **No se entrena ningún modelo con los datos del cliente.** | No hay ejemplos de ataques para entrenar, lo normal cambia por cliente y el cliente nuevo no tiene datos. El juicio lo hace un modelo de IA ya entrenado (Claude). |
| **Nunca aprende de lo marcado como sospechoso.** | Así un atacante no puede "enseñarle" que su comportamiento es normal. |
| **La IA solo se consulta cuando hay un desvío.** | El código revisa todo, rápido y barato; la mayoría del tráfico nunca llega a la IA. |
| **Señales del perfil:** desde dónde (red y dispositivo), cuándo, qué endpoints y datos, cuánto volumen y cómo navega (recorrido y ritmo). | El dispositivo y el ritmo delatan scripts y malware aunque usen la red del dueño. |
| **La IA entiende el sistema leyendo su documentación OpenAPI** y clasifica cada endpoint como crítico, sensible o normal. | Cambiar una cuenta bancaria es más grave que ver un catálogo. Sin IA, eso lo configuraría una persona endpoint por endpoint. |
| **Mensaje sobre la IA:** "Las señales las calcula estadística. La IA entiende qué es crítico en tu sistema, decide y te explica qué pasó." | Respuesta honesta a la crítica de que la IA está forzada (ver HALLAZGOS). |

## Respuesta

| Decisión | Por qué |
|---|---|
| **Respuesta por grados:** alerta en el panel → pedir un segundo factor en ese momento → bloquear. | Bloquear todo lo raro rompe el negocio; solo alertar no frena a nadie. |
| **Descartado: preguntar "¿fuiste tú?" al dueño.** | Si la pregunta llega a la misma sesión, el atacante responde que sí. Pedir un segundo factor en la sesión cumple el mismo rol sin ese problema. |
| **El dueño bloqueado por error se libera desde su correo** (decidido 2026-10-25, sin implementar). Le llega un correo con la marca del cliente y un botón "Fui yo, desbloquear". Detalle en [DESBLOQUEO-POR-CORREO.md](DESBLOQUEO-POR-CORREO.md). | La pregunta llega a la bandeja del dueño, no a la sesión, así que el atacante que solo tiene la contraseña no la responde. No toca la app del cliente. Requiere que el SDK mande el correo del usuario y depende de la decisión 4 (proveedor). |
| **Endpoint crítico + desvío = bloqueo inmediato,** aunque sea una sola petición. | Un cambio de cuenta bancaria no necesita volumen para ser grave. |
| **Una ráfaga de pedidos (ritmo de script) = bloqueo por regla,** aunque venga de la red y el dispositivo del dueño. Se bloquea la sesión; la IP solo si la red es nueva. | La IA respondía `step_up`, y el middleware no pide segundo factor: el script seguía descargando. La regla lo frena de forma determinista; la IA solo lo explica. |
| **Se bloquea la sesión y la IP del atacante, no el usuario.** | El atacante usa la misma credencial que el dueño: bloquear el usuario también deja afuera a la víctima. |
| **Las rutas críticas se deciden antes de ejecutarse (consulta síncrona); el resto se bloquea en la petición siguiente.** | En el ensayo del 2026-10-25 el atacante cambió la cuenta bancaria y recién después quedó bloqueado: el daño ya estaba hecho. El rollback se descartó porque Veridian no ve los datos ni sabe deshacer, y hay daños que no se deshacen (pagos, correos, descargas). |
| **En la consulta síncrona, la regla decide lo obvio al instante y la IA decide los casos grises en el momento (tope ~10 s).** | Sacar a la IA de la decisión le quitaba el valor al producto. El dueño legítimo no espera: sin desvío no se consulta la IA. |
| **Si el motor no responde, la ruta crítica pasa (*fail-open*).** | Coherente con "si Veridian se cae, tu app sigue funcionando". |
| **Las redes se comparan por `/24`, no por proveedor.** | En el ensayo, el teléfono del atacante salió del mismo rango grande (`186.164.x`) que la laptop del dueño (`186.167.x`). Comparar por `/16` o por proveedor habría ocultado el ataque. El costo: con datos móviles o CGNAT, el dueño aparece desde redes "nuevas" y genera alertas (no bloqueos). |

## Pitch y demo

| Decisión | Por qué |
|---|---|
| **Impacto contado en tiempo:** 241 días contra segundos, más 22 % y USD 4,99 M. | El ejemplo de "100.000 registros" era inventado y comparaba contra no hacer nada. |
| **Problema sin caso enterprise.** | El cliente es una startup; los casos de grandes empresas confunden el público objetivo. |
| **Demo en vivo con personas reales y redes reales,** sin "Ana" ficticia. | Ver [DEMO.md](DEMO.md). |
| **Equipo trabaja directo en `main`, sin PR.** | Velocidad de hackathon. |
