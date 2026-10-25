from fastapi import FastAPI
from veridian_sdk import VeridianMiddleware

app = FastAPI()

app.add_middleware(
    VeridianMiddleware,
    # Id de tu usuario, o None si nadie inició sesión
    user=lambda request: request.state.user.id,
    # Proxies tuyos delante de la app (0 si no hay)
    trusted_proxies=0,
)
