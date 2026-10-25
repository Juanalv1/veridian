# INIT: arranque para agentes

Lee esto completo antes de escribir código. Después pregúntale al usuario solo lo que falte.

## 1. Lee, en este orden

1. `README.md`: qué es Veridian.
2. `CLAUDE.md`: convenciones (inglés en código, Conventional Commits sin atribución de IA, directo a `main`).
3. `docs/REPARTO.md`: tu rol, los **contratos** y el primer hito.
4. `docs/DECISIONES.md`: lo que ya está decidido. No lo reabras sin que el usuario lo pida.
5. `docs/DEMO.md` y `docs/PENDIENTES.md`: para qué es todo esto y qué falta.

`docs/HALLAZGOS.md` es contexto de pitch: léelo solo si tu tarea lo necesita.

## 2. Identifica tu rol

El usuario te dice si es **Dev 1 (Motor)** o **Dev 2 (Borde y demo)**. Si no lo dice, pregunta. Trabaja solo en tu carpeta:

```
engine/   Dev 1: servidor central (eventos, perfil, IA, bloqueos, decisiones)
edge/     Dev 2: middleware/, demo_app/, panel/
docs/     compartido
```

## 3. Reglas de trabajo

- **Los contratos de `docs/REPARTO.md` son la frontera entre los dos devs.** Si necesitas cambiar uno, propónselo al usuario y actualiza `REPARTO.md` en el mismo commit. Nunca cambies un contrato en silencio.
- **Stack por defecto:** Python 3.12 y FastAPI, con tests en pytest. Para empezar, SQLite; Postgres cuando se despliegue. Si el usuario elige otro stack, anótalo en `docs/DECISIONES.md`.
- **Primero el camino mínimo de punta a punta, después la IA.** El primer hito de cada dev en `REPARTO.md` funciona sin IA.
- **Test primero** cuando haya un comportamiento claro que probar.
- **Haz `git pull --rebase` antes de cada push:** el otro dev también trabaja en `main`.
- **Nada de secretos en el repo.** La clave de Anthropic se lee de `ANTHROPIC_API_KEY` o de un `.env` (ignorado por git).
- **Referencia, no copia:** el repo de Centinela (`../centinela`) tiene piezas parecidas (ingesta en `centinela_svc/web/api.py`, perfil en `centinela_svc/domain/keywatch.py`, evaluación en `evals/key_judgment/`). Léelas como inspiración; Veridian empieza limpio.

## 4. Si eres Dev 1 (Motor)

Primer hito (ver `REPARTO.md`):
1. `POST /v1/events`: guarda eventos, idempotente por `id`, autenticado con token por cliente.
2. Perfil mínimo por `user_id`: redes (`/24`) y rutas usadas, aprendido de eventos de más de N minutos que no fueron marcados.
3. Regla inicial: ruta `critical` nunca usada + red nueva → decisión `block`, que aparece en `GET /v1/blocklist` y `GET /v1/decisions`.
4. Clasificación de rutas por ahora en un archivo de configuración (`routes.yaml`); la lectura de OpenAPI con IA viene después.
5. Tests de cada paso y un script que envía eventos de ejemplo para probar sin el middleware.

Después: la IA como juez de los desvíos (prompt, decisión, explicación), las señales finas (dispositivo, ritmo, recorrido) y la clasificación por OpenAPI.

## 5. Si eres Dev 2 (Borde y demo)

Primer hito (ver `REPARTO.md`):
1. App de ejemplo: login, facturas y la pantalla de cuenta bancaria de pagos.
2. Middleware que envía eventos en segundo plano y aplica la lista de bloqueos (responde `403`).
3. Panel mínimo que lista `GET /v1/decisions`.

Mientras el motor no exista, usa un servidor falso que respete los contratos.

## 6. Al terminar cada tarea

- Tests verdes y commit en `main`.
- Si cambió algo de lo pendiente, actualiza `docs/PENDIENTES.md`.
- Resume al usuario qué funciona y qué no, sin adornos.
