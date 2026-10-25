import threading

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from . import db
from .auth import Unauthorized, client_id_from_request
from .events import EventBatch, insert_events
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db.init_db(settings.db_path)

    app = FastAPI(title="Veridian engine")
    app.state.settings = settings
    write_lock = threading.Lock()  # single process: serialize writes that read-then-write

    @app.exception_handler(Unauthorized)
    async def unauthorized_handler(request: Request, exc: Unauthorized) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    @app.post("/v1/events", status_code=202)
    def ingest_events(batch: EventBatch, client_id: str = Depends(client_id_from_request)) -> dict:
        with write_lock, db.connect(settings.db_path) as conn:
            inserted = insert_events(conn, client_id, batch.events)
        return {"accepted": len(inserted)}

    return app
