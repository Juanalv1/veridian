import threading

from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse

from . import db
from .auth import Unauthorized, client_id_from_request
from .decisions import blocklist, current_version, list_decisions, mark_normal
from .events import EventBatch, insert_events
from .judging import judge_deviations, latest_per_session
from .profile import build_profile, is_blocked
from .route_classes import RouteClasses
from .rules import evaluate_event
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db.init_db(settings.db_path)
    route_classes = RouteClasses.load(settings.routes_file)

    app = FastAPI(title="Veridian engine")
    app.state.settings = settings
    app.state.route_classes = route_classes
    write_lock = threading.Lock()  # single process: serialize writes that read-then-write

    @app.exception_handler(Unauthorized)
    async def unauthorized_handler(request: Request, exc: Unauthorized) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    @app.post("/v1/events", status_code=202)
    def ingest_events(
        batch: EventBatch, background_tasks: BackgroundTasks, client_id: str = Depends(client_id_from_request)
    ) -> dict:
        deviations = []
        with write_lock, db.connect(settings.db_path) as conn:
            inserted = insert_events(conn, client_id, batch.events)
            for event in sorted(inserted, key=lambda e: e.ts):
                deviation = evaluate_event(conn, settings, route_classes, client_id, event)
                if deviation is not None:
                    deviations.append(deviation)
        if settings.judge is not None and deviations:
            # Runs after the 202 is sent: ingestion never waits for the AI.
            background_tasks.add_task(judge_deviations, settings, write_lock, latest_per_session(deviations))
        return {"accepted": len(inserted)}

    @app.get("/v1/users/{user_id}/profile")
    def get_profile(user_id: str, client_id: str = Depends(client_id_from_request)) -> dict:
        with db.connect(settings.db_path) as conn:
            profile = build_profile(conn, client_id, user_id, settings.clock(), settings.learn_delay_min)
            return profile.to_dict(blocked=is_blocked(conn, client_id, user_id))

    @app.get("/v1/decisions")
    def get_decisions(
        limit: int = Query(50, ge=1, le=500), client_id: str = Depends(client_id_from_request)
    ) -> dict:
        with db.connect(settings.db_path) as conn:
            return {"decisions": list_decisions(conn, client_id, limit)}

    @app.post("/v1/decisions/{decision_id}/normal")
    def post_mark_normal(decision_id: str, client_id: str = Depends(client_id_from_request)):
        with write_lock, db.connect(settings.db_path) as conn:
            result = mark_normal(conn, client_id, decision_id)
        if result is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return result

    @app.get("/v1/blocklist")
    def get_blocklist(since: int | None = None, client_id: str = Depends(client_id_from_request)):
        with db.connect(settings.db_path) as conn:
            if since is not None and since == current_version(conn, client_id):
                return Response(status_code=304)
            return blocklist(conn, client_id)

    return app
