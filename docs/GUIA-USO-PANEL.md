# Guía de uso del panel de Veridian

Mapa rápido de cada menú del panel y para qué sirve.

## Mapa general

```
┌──────────────────────────────┐
│ ◆ Veridian            [≡]    │ ← Logo: vuelve al Resumen · [≡] colapsa el menú
├──────────────────────────────┤
│ Org · Rol                    │
│ Proyecto actual  [MODO]      │ ← Selector: cambia de organización o proyecto
├──────────────────────────────┤
│ ACCESOS                      │
│   Resumen                    │ → Estado general del proyecto
│   Actividad          (3)     │ → Accesos en vivo · (n) = bloqueos recientes
│   Usuarios                   │ → Perfil aprendido de cada usuario
│   Rutas críticas             │ → Rutas sensibles y su clasificación
│ PROYECTO                     │
│   Claves de API              │ → Credenciales del middleware
│   Webhook                    │ → Avisos firmados hacia tu sistema
│ ORGANIZACIÓN                 │
│   Proyectos                  │ → Crear o elegir proyectos
│   Miembros                   │ → Invitar personas y asignar roles
│   Auditoría                  │ → Historial de cambios de configuración
├──────────────────────────────┤
│ ● En vivo · último acceso…   │ ← Conexión con el motor
│ correo@…   [Cerrar sesión]   │ ← Tu cuenta
└──────────────────────────────┘
```

## ACCESOS — lo que pasa con las credenciales

- **Resumen** → Titular del estado actual del proyecto y gráfico de **accesos por hora**. Es la primera pantalla: responde "¿está todo bien?".
- **Actividad** → Lista de cada acceso reportado por el middleware con la decisión del motor (permitido / bloqueado) y su motivo. El número rojo en el menú indica bloqueos recientes. Aquí se ve el robo de credenciales en acción.
- **Usuarios** → Cada usuario con su perfil determinista aprendido (desde dónde y cómo suele entrar). Sirve para entender por qué una desviación se marcó como sospechosa.
- **Rutas críticas** → Rutas de tu app agrupadas por sensibilidad. Se clasifican automáticamente en la primera ejecución; un acceso inusual a una ruta crítica pesa más.

## PROYECTO — conectar tu aplicación

- **Claves de API** → Crear y revocar las claves que usa el middleware para reportar accesos. La clave se muestra **una sola vez** al crearla: cópiala en ese momento.
- **Webhook** → URL a la que Veridian avisa de cada bloqueo, con un **secreto de firma** (también visible una sola vez) para verificar que el aviso es auténtico.

## ORGANIZACIÓN — equipo y gobierno

- **Proyectos** → Lista de proyectos de la organización; crear uno nuevo (p. ej. una app distinta o un entorno).
- **Miembros** → Invitar personas por correo y ver quién tiene acceso y con qué rol.
- **Auditoría** → Registro de quién cambió qué (claves, webhook, miembros). Filtrable.

> Claves, Webhook y Auditoría dependen del rol: si no tienes permiso verás "No tienes acceso…".

## Pie del menú

- **● En vivo / Motor sin conexión** → indica si el panel recibe datos del motor; "último acceso hace…" muestra la frescura.
- **Cerrar sesión** → sale de tu cuenta.

## Flujo recomendado (primera vez)

```
Proyectos → Claves de API → (instalar middleware) → Webhook → Actividad → Resumen
 crear        copiar clave     reporta accesos       avisos     ver en vivo   estado
```
