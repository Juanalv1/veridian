# Guion de la demo

Va después de la slide 4 y antes de la slide 5 (`GUION.md`). Tiempo disponible: 125-140 s (sin medir todavía).

Depende del desbloqueo por correo, que está en desarrollo ([DESBLOQUEO-POR-CORREO.md](DESBLOQUEO-POR-CORREO.md)). Si no está listo, la demo termina en el bloqueo y se cierra con el stack.

**[Proyector: la app de facturas y el panel de Veridian, lado a lado. La bandeja de correo del presentador, abierta en otra pestaña]**

**[El presentador entra con su cuenta y revisa sus facturas]**

> Esta es una app de facturas que ya tiene Veridian instalado, y este es su panel. Entro con mi cuenta, como todos los días, y reviso mis facturas.

**[Panel: «Normal», en verde]**

> Veridian reconoce mi red, mi dispositivo y mi horario, así que me deja pasar. Para la demo precargamos mi historial; en producción, lo aprende solo.

**[Activa la VPN, abre la app en otro navegador, entra con la misma contraseña y va a cambiar la cuenta bancaria]**

> Ahora imaginen que alguien tiene mi contraseña. Entra desde otra red y otro dispositivo, y va directo a cambiar la cuenta bancaria donde cobro.

**[Panel: «Bloqueado»]**

> Veridian lo bloquea antes de que el cambio ocurra. Y la IA explica por qué:

**[Leer la explicación del panel]**

**[Abre la bandeja: llegó el correo]**

> Al mismo tiempo, me llega este correo, con la marca de la app: detectaron un acceso sospechoso a mi cuenta.
>
> Pero esta vez sí era yo, con una VPN y otro navegador. Así que hago clic en «Fui yo, desbloquear».

**[Clic. Vuelve a la app y cambia la cuenta bancaria]**

> Y sigo trabajando con normalidad. En el panel, el acceso queda en verde, desbloqueado por mí.

**[Panel: «Desbloqueado por el usuario», en verde (texto de la especificación; confirmar cuando esté implementado)]**

> Lo construimos con Python, FastAPI, SQLite y Claude Opus 5.5.

## Antes de subir

- Medir en los ensayos cuánto tarda el correo en llegar con la red del evento.
- Precargar el historial del presentador con la IP del wifi del evento.
- Tener grabado el video de respaldo de una corrida buena.
