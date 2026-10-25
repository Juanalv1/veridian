# Procedimientos de borrado e incidente

Descritos contra el contrato 14 **planeado** (`docs/DISENO-PRODUCTO.md`, "Retención y borrado"). **Los endpoints no existen todavía**: los implementa Dev 1 (U13). Hasta entonces, ver "Mientras tanto".

## Borrado por solicitud de un usuario final

La solicitud llega al cliente (responsable); este la pasa a un admin del proyecto.

| Paso | Acción | Estado |
|---|---|---|
| 1 | Recibir la solicitud y registrar fecha y canal | Manual |
| 2 | Verificar que quien pide es el titular o su representante (lo hace el cliente) | Manual |
| 3 | Un admin ejecuta en el panel el borrado de ese `user_id` | Planeado: `DELETE /v1/projects/{id}/users/{user_id}` |
| 4 | El motor borra eventos, decisiones y filas auxiliares de ese `user_id`, sube la versión del blocklist y audita con el hash del `user_id` | Planeado |
| 5 | Confirmar al cliente; indicar que las copias, si existen, no se alcanzan (no verificado si existen) | Manual |
| 6 | Si un bloqueo del usuario estaba abierto, queda levantado al borrarse sus decisiones; avisar al cliente | Por confirmar al implementar |

La cookie `vrd_did` vive en el navegador del usuario: el motor no puede borrarla; la app del cliente puede hacerlo.

## Borrado de un proyecto completo

| Paso | Acción | Estado |
|---|---|---|
| 1 | Solicitud de un owner del proyecto | Manual |
| 2 | El owner ejecuta el borrado | Planeado: `DELETE /v1/projects/{id}/data` |
| 3 | Si se elimina el proyecto (`deleted_at`), se purga tras un plazo de gracia configurable (plazo sin definir) | Planeado |
| 4 | Revocar las claves de API del proyecto y confirmar por escrito | Manual |

## Notificación de incidentes

Un incidente es acceso, pérdida o exposición no autorizados de datos de un proyecto.

| Paso | Acción | Estado |
|---|---|---|
| 1 | Detectar y contener (rotar `VERIDIAN_TOKENS` o claves, aislar el servicio) | Manual |
| 2 | Determinar qué proyectos y qué tablas se afectaron | Manual; la auditoría planeada ayudará |
| 3 | Avisar a los clientes afectados con los hechos conocidos | Manual |
| 4 | Plazo de aviso | **Por definir** en el DPA; no se fija aquí |
| 5 | Registrar causa, alcance y medidas; revisar el procedimiento | Manual |

Contenido mínimo del aviso: qué pasó, qué datos y proyectos, qué se hizo, a quién contactar. Los plazos legales y la obligación de avisar a una autoridad dependen de la jurisdicción y de la revisión legal; no se afirman aquí.

## Mientras tanto (hoy)

- **Actualizado con U13 (2026-10-25):** el reinicio de ensayos se eliminó. Borrar un usuario: `DELETE /v1/projects/{id}/users/{user_id}` (admin+); borrar los datos de un proyecto: `DELETE /v1/projects/{id}/data` (owner). Los dos quedan en la auditoría.
- No hay registro de auditoría ni retención automática (`DATOS.md`).
- `VERIDIAN_RESET_TOKEN` ya no existe: si sigue definido en Render, borrarlo (no tiene efecto).
