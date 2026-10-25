# Guion de la demo

Estructura y texto aprobados (2026-10-25). Falta medirlo en voz alta.

Va después de la slide 4 y antes de la slide 5 (`GUION.md`). Tiempo disponible: 125-140 s (sin medir todavía).

Depende del desbloqueo por correo, que está en desarrollo ([DESBLOQUEO-POR-CORREO.md](DESBLOQUEO-POR-CORREO.md)). Si no está listo, la demo termina en el bloqueo.

Estructura: una sola persona, la dueña de la cuenta.
1. Entra desde su teléfono, el de siempre → «Normal».
2. Entra desde una PC que Veridian no conoce → «Raro».
3. Intenta cambiar la cuenta bancaria → «Bloqueado», con un mensaje amigable.
4. Le llega el correo → «Fui yo, desbloquear» → entra con normalidad, en verde.

## Parte 1: entrada normal

**[Proyector: la pantalla del teléfono, espejada, y el panel de Veridian, lado a lado]**

> Para mostrar cómo funciona Veridian, usaremos una plataforma donde una empresa factura y cobra a sus clientes, y que ya tiene Veridian instalado.

**[Inicia sesión en el teléfono y navega unos segundos: la lista de facturas y el detalle de una]**

> Inicio sesión desde mi teléfono, el dispositivo que uso siempre, y reviso mis facturas como cualquier día.

**[Panel: «Normal»]**

> Veridian me reconoce por mi comportamiento y mi historial, así que me deja pasar.

## Parte 2: dispositivo desconocido

**[En la PC, en otro navegador, inicia sesión con la misma cuenta y navega unos segundos]**

> Ahora entro a mi misma cuenta desde otro dispositivo, uno que Veridian nunca ha visto.

**[Panel: «Raro»]**

> Veridian sospecha, porque no me estoy conectando como siempre, y la IA revisa el acceso. Por ahora me deja seguir, porque lo que hago es de bajo impacto.

## Parte 3: bloqueo

**[En la PC, va a la cuenta bancaria de cobro e intenta cambiarla]**

> Ahora voy a cambiar la cuenta bancaria donde la empresa recibe sus pagos, una ruta de alto impacto.

**[La app: «Detectamos un acceso inusual. Te enviamos un correo para confirmar que fuiste tú». Panel: «Bloqueado»]**

> Veridian bloquea el cambio antes de que ocurra. La app me avisa, y en el panel la IA explica por qué lo bloqueó:

**[Leer la explicación del panel]**

## Parte 4: desbloqueo por correo

**[En el teléfono abre su correo: llegó el aviso, con la marca de la plataforma]**

> Al mismo tiempo, me llega este correo. Como sí era yo, lo confirmo.

**[Toca «Fui yo, desbloquear», se abre la página de confirmación y toca el botón]**

**[Vuelve a la PC y cambia la cuenta bancaria]**

> Y sigo trabajando con normalidad. En el panel, el acceso queda en verde.

**[Panel: «Desbloqueado por el usuario», en verde (texto de la especificación; confirmar cuando esté implementado)]**

## Antes de subir

- Espejar el teléfono en el proyector.
- Antes de cada corrida, abrir `https://veridian-demo.onrender.com/preparar` desde el teléfono, con el wifi del evento, y tocar «Preparar demo» (la primera vez pide el PIN). Borra los datos y precarga 14 días de historial con la red y el dispositivo del teléfono. Nunca abrirla desde la PC: la PC quedaría como conocida y no habría bloqueo.
- En Render (servicio de la demo): `DEMO_PREPARE_PIN` y `VERIDIAN_PANEL_TOKEN`; `ensayo@veridian.demo` debe ser owner de la organización Demo.
- Ensayar el bloqueo sin VPN unas 20 veces en el lugar: con un dispositivo nuevo en la misma red, lo decide la IA (3 de 3 medido el 2026-10-25). Si falla una vez, agregar la VPN: con una red nueva bloquea la regla fija.
- Medir cuánto tarda el correo en llegar con la red del evento.
- Tener grabado el video de respaldo de una corrida buena.
