# Borde y demo (Dev 2)

Todo lo que toca la app del cliente y lo que se ve en el escenario:

| Carpeta | Qué es |
|---|---|
| `../sdk/python/` | Middleware ASGI (paquete `veridian-sdk`, import `veridian_sdk`): envía eventos al motor en segundo plano responde `403 {"error": "blocked"}` a lo que esté en la lista de bloqueos y consulta al motor (`POST /v1/check`) antes de ejecutar las rutas `critical` |
| `demo_app/` | App de facturas de ejemplo (login, facturas, cuenta bancaria de cobros) protegida por el middleware |
| `panel/` | Página que lista `GET /v1/decisions` y se actualiza sola |
| `fake_engine/` | Motor falso en memoria que respeta los contratos de `docs/REPARTO.md`, para trabajar sin el motor real |

El middleware nunca envía cuerpos de peticiones, contraseñas ni datos de clientes, y falla abierto: si el motor no responde, la app sigue con la última lista conocida; si la consulta previa de una ruta crítica falla o tarda más de 12 s, la petición pasa.

## Instalar

Requiere [uv](https://docs.astral.sh/uv/) (descarga Python 3.12 si hace falta).

```bash
cd edge
uv sync
```

## Variables de entorno

| Variable | Quién la usa | Para qué |
|---|---|---|
| `VERIDIAN_TOKENS` | motor falso | Tokens aceptados, separados por coma. Sin tokens, todo da `401` |
| `VERIDIAN_ALLOW_DEV_TOKEN=1` | motor falso | Acepta además el token local `dev-token` (solo desarrollo) |
| `VERIDIAN_SEED=demo` | motor falso | Precarga el historial del dueño (`u_42` desde `127.0.0.1`). También acepta un JSON `[{"user_id", "ips", "routes"}]` |
| `VERIDIAN_LEARN_DELAY` | motor falso | Segundos antes de que un evento no marcado entre al perfil (por defecto 300). Evita que el login del atacante legitime su red |
| `VERIDIAN_PANEL_TOKEN` | motor falso y panel | Credencial de servicio del panel. El motor falso la exige (más `X-Veridian-Actor` y `X-Veridian-Project`) en todo lo que no es del SDK; `VERIDIAN_PANEL_TOKENS` admite una lista para rotar |
| `VERIDIAN_BOOTSTRAP_OWNERS` | motor falso | Correos que, al entrar, quedan como `owner` de las organizaciones sin owner (cada token sembrado es una organización con un proyecto) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SESSION_SECRET`, `PUBLIC_URL` | panel | Login con Google y cookie de sesión. En local se leen de `edge/.env` sin pisar el entorno. Sin ellos el panel responde 503 (no hay modo abierto) |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | panel | Opcionales: sin los dos, el login con GitHub no aparece ni responde (404). Se crean en una OAuth App de GitHub con callback `<PUBLIC_URL>/auth/github/callback`. El panel pide el permiso `user:email` y entra con el correo principal verificado |
| `VERIDIAN_URL` | demo y panel | URL del motor (por defecto `http://localhost:8000`) |
| `VERIDIAN_TOKEN` | demo | Token Bearer (clave del SDK) con el que habla con el motor. El panel ya no lo usa |
| `TRUSTED_PROXIES` | demo | Cantidad de proxies propios delante de la app (por defecto `0`: se usa la IP de la conexión y se ignora `X-Forwarded-For`). En Render es `3` (medido) |
| `DEMO_PASSWORD` | demo | Contraseña de los usuarios de ejemplo. Por defecto `demo1234`, solo para uso local |

Los tokens no se guardan en el repo; se pasan por entorno o por un `.env` (ignorado por git).

## Levantar los tres procesos

En tres terminales, desde `edge/`:

```bash
# 1. Motor falso (puerto 8000)
VERIDIAN_TOKENS=dev-local VERIDIAN_SEED=demo VERIDIAN_PANEL_TOKEN=panel-local \
  VERIDIAN_BOOTSTRAP_OWNERS=tu@correo.com uv run uvicorn fake_engine.app:app --port 8000

# 2. App de ejemplo (puerto 8001)
VERIDIAN_URL=http://localhost:8000 VERIDIAN_TOKEN=dev-local TRUSTED_PROXIES=1 \
  uv run uvicorn demo_app.main:app --port 8001

# 3. Panel (puerto 8002)
VERIDIAN_URL=http://localhost:8000 VERIDIAN_PANEL_TOKEN=panel-local \
  uv run uvicorn panel.main:app --port 8002
```

- App: <http://localhost:8001> (usuario `ana@facturas.demo`, contraseña `DEMO_PASSWORD`). También existe `luis@facturas.demo`.
- Panel: <http://localhost:8002>.

Para usar el motor real de Dev 1, apunta `VERIDIAN_URL` y `VERIDIAN_TOKEN` a él y no levantes el motor falso.

## Simular al atacante en local

`TRUSTED_PROXIES=1` hace que la app tome la IP del último valor de `X-Forwarded-For`, como si hubiera un proxy propio delante. Así `curl` puede "venir" de otra red. El dueño del historial sembrado es `127.0.0.1`.

```bash
APP=http://localhost:8001

# Dueño: entra y navega (200)
curl -s -c dueno.txt -d 'email=ana@facturas.demo&password=demo1234' -H 'X-Forwarded-For: 127.0.0.1' $APP/login -o /dev/null
curl -s -b dueno.txt -H 'X-Forwarded-For: 127.0.0.1' -o /dev/null -w '%{http_code}\n' $APP/invoices

# Atacante: misma contraseña, otra red (el primer valor es falso y se ignora)
H='X-Forwarded-For: 9.9.9.9, 203.0.113.9'
curl -s -c atacante.txt -d 'email=ana@facturas.demo&password=demo1234' -H "$H" $APP/login -o /dev/null
curl -s -b atacante.txt -d 'bank=Otro&account_number=1' -H "$H" -o /dev/null -w '%{http_code}\n' $APP/payouts/bank-account   # 403: la ruta es crítica, el motor la frena antes de que la app la ejecute
sleep 5
curl -s -b atacante.txt -H "$H" $APP/invoices                                                                              # 403 {"error":"blocked"}
curl -s -b dueno.txt -H 'X-Forwarded-For: 127.0.0.1' -o /dev/null -w '%{http_code}\n' $APP/invoices                       # 200
```

El panel muestra la decisión `block` con sus señales y la explicación. Las rutas `critical` se consultan antes de ejecutarse (contrato 9), así que la petición sospechosa ya no pasa; en las demás rutas se bloquea la petición siguiente, por los ciclos de ~2 s.

Sin proxy (`TRUSTED_PROXIES=0`), la IP es siempre la de la conexión y `X-Forwarded-For` se ignora, aunque el atacante lo falsifique.

## Tests

```bash
cd edge
uv run pytest -q
```

No usan red real ni esperas: el motor falso y las apps se conectan por `httpx.ASGITransport`, y los tests llaman a `flush()` y `refresh_blocklist()` del middleware directamente.

## Notas

- La regla del motor falso refleja el primer hito de Dev 1: ruta `critical` nunca usada del usuario, desde una red `/24` nunca vista para él → `block` de su sesión y su IP (`users` queda vacío). Un usuario sin historial no se juzga.
- El `session_id` que ve el motor es un hash de la cookie de sesión, nunca la cookie.

## Despliegue en Render

| Servicio | URL | Notas |
|---|---|---|
| Motor (Dev 1) | <https://veridian-engine.onrender.com> | Plan starter con disco |
| App de ejemplo | <https://veridian-demo.onrender.com> | raíz del repo, plan free, `TRUSTED_PROXIES=3` |
| Panel | <https://veridian-panel.onrender.com> | raíz del repo, plan free |

- La app y el panel usan la raíz del repo como directorio raíz (`rootDir` vacío), porque `edge` instala el SDK desde `../sdk/python`. Build: `cd edge && pip install uv && uv sync --frozen --no-dev`. Start: `cd edge && .venv/bin/uvicorn ...`.
- La app se redespliega cuando cambia `edge/**` o `sdk/python/**`; el panel solo cuando cambia `edge/**`.
- `VERIDIAN_TOKEN` es el token del cliente `demo` del motor; `DEMO_PASSWORD` está en el entorno de Render, no en el repo.
- **Antes de la demo:** el plan free se duerme a los 15 min sin uso y tarda ~1 min en despertar; pasar a starter o abrir ambas URLs unos minutos antes.
