# Esquema de DPA y política de privacidad

**Esto no es texto legal.** Es un esquema de qué debe cubrir cada documento. El texto lo redacta y revisa quien corresponda. Fuente: "Artefactos de privacidad y DPA" de `docs/DISENO-PRODUCTO.md`.

## A. Acuerdo de encargo del tratamiento (DPA)

Veridian como encargado; el cliente como responsable de los datos de sus usuarios.

| # | Sección | Debe cubrir | Insumo |
|---|---|---|---|
| 1 | Objeto y duración | Detectar uso de credenciales por otra persona; vigencia ligada al contrato | `README.md` |
| 2 | Tipos de datos y titulares | Tablas y cookie del inventario; titulares: usuarios finales del cliente | `DATOS.md` |
| 3 | Instrucciones del responsable | Solo tratar para detectar y bloquear; configuración (retención, IA) | Diseño |
| 4 | Confidencialidad | Quién accede a los datos (roles owner/admin/viewer) | Diseño, "Roles" |
| 5 | Medidas de seguridad | Autenticación, aislamiento entre proyectos, claves, cifrado | Diseño, "Seguridad" |
| 6 | Subencargados | Lista, finalidad, ubicación, aviso de cambios | `SUBENCARGADOS.md` |
| 7 | Transferencias y ubicación | Región declarada; sin promesa de residencia (decisión 9) | `SUBENCARGADOS.md` |
| 8 | Derechos de los titulares | Cómo el cliente pide acceso o borrado y en cuánto tiempo (plazo por definir) | `PROCEDIMIENTOS.md` |
| 9 | Notificación de incidentes | Quién avisa, a quién y en qué plazo (plazo por definir) | `PROCEDIMIENTOS.md` |
| 10 | Devolución o borrado al terminar | Borrado de proyecto, plazo de gracia, copias | `DATOS.md` |
| 11 | Auditoría | Qué puede pedir el cliente y cómo | Diseño |
| 12 | Responsabilidad y ley aplicable | Fuera de este esquema | Revisión legal |

## B. Política de privacidad

| # | Sección | Debe cubrir |
|---|---|---|
| 1 | Qué se recoge | Campos de `events` y `decisions`, cookie `vrd_did` |
| 2 | Para qué | Detección y bloqueo; no se entrena ningún modelo con datos del cliente (`DECISIONES.md`, pendiente de verificar con el proveedor) |
| 3 | Base de uso | Por definir con revisión legal |
| 4 | Con quién se comparte | Subencargados |
| 5 | Retención | Valor por defecto y cómo se cambia (decisión 5) |
| 6 | Derechos y cómo ejercerlos | Canal y plazos |
| 7 | IA | Campos que recibe el modelo (`SUBENCARGADOS.md`) y opción de apagarla (decisión 10) |
| 8 | Contacto | Por definir |

## C. Anexos

Flujo de datos y medidas de seguridad (el diseño sirve de base); procedimiento de borrado e incidentes (`PROCEDIMIENTOS.md`).

## Decisiones abiertas que lo bloquean

Todas **pendientes**; la recomendación es la del diseño, no una decisión tomada.

| # | Pregunta | Recomendación del diseño |
|---|---|---|
| 5 | Días de retención por defecto y mínimo permitido | 90 días como valor provisional, a revisar con la medición del piloto; el mínimo no se puede fijar sin ese dato |
| 9 | ¿Prometemos ubicación de los datos? | No prometer residencia; declarar región y subencargados en el DPA |
| 10 | ¿Un proyecto puede apagar la IA (`ai_enabled`)? | Sí, por proyecto |
| 12 | Retención de la auditoría y `user_id` en claro | Conservarla mientras exista el proyecto y guardar siempre el hash del `user_id` |
