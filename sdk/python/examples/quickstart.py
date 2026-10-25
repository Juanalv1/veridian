# 1. Instala el SDK:            pip install veridian-sdk
# 2. Pon tu clave en el entorno (nunca en el código), la que creaste en el panel:
#                               VERIDIAN_API_KEY=vrd_...
from fastapi import FastAPI
from veridian_sdk import VeridianMiddleware

app = FastAPI()

app.add_middleware(
    VeridianMiddleware,
    # Quién hace la petición: el id de tu usuario, o None si nadie inició sesión.
    # Si nadie inició sesión y request.state.user no existe, cuenta como None, sin errores.
    # Corre antes de tus rutas: si tu login pone el usuario en request.state desde
    # otro middleware, agrega ese middleware después de este, así corre primero.
    user=lambda request: request.state.user.id,
    # Cuántos proxies tuyos hay delante de la app (un balanceador, Render, Heroku: 1).
    # Con 0 usa la IP de la conexión e ignora X-Forwarded-For, que cualquiera puede escribir.
    trusted_proxies=0,
)

# La sesión se detecta sola: cookie sessionid, connect.sid, sid, session_id, PHPSESSID
# o JSESSIONID, o el token Bearer. Veridian solo recibe un hash, nunca el valor.
# Si Veridian no responde, tu app sigue funcionando: deja pasar todas las peticiones.
# Para comprobar la conexión: entra a tu app con un usuario y, en unos segundos,
# ese acceso aparece en Actividad, en el panel de Veridian.
