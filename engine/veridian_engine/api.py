import hmac
import threading
from datetime import datetime, timedelta

from dotenv import find_dotenv, load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse

from . import db
from .activity import list_activity
from .auth import ApiKeyAuth, RateLimited, Unauthorized, project_id_from_request
from .check import CheckRequest, check_event
from .control_plane.legacy import seed_legacy
from .decisions import blocklist, current_version, list_decisions, mark_normal, reset_client
from .events import EventBatch, insert_events
from .judging import judge_deviations, latest_per_session
from .project_settings import get_project_flags
from .profile import build_profile, is_blocked
from .route_classes import RouteClasses
from .rules import evaluate_event
from .settings import Settings
from .summary import as_utc, build_summary, list_users


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        # Real environment variables win over the nearest .env (searched upward from the cwd).
        load_dotenv(find_dotenv(usecwd=True))
        settings = Settings.from_env()
    db.init_db(settings.db_path)
    with db.connect(settings.db_path) as conn:
        seed_legacy(conn, settings.tokens, settings.clock())
    route_classes = RouteClasses.load(settings.routes_file)

    app = FastAPI(title="Veridian engine")
    app.state.settings = settings
    app.state.route_classes = route_classes
    app.state.auth = ApiKeyAuth(settings)
    write_lock = threading.Lock()  # single process: serialize writes that read-then-write

    @app.exception_handler(Unauthorized)
    async def unauthorized_handler(request: Request, exc: Unauthorized) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    @app.exception_handler(RateLimited)
    async def rate_limited_handler(request: Request, exc: RateLimited) -> JSONResponse:
        return JSONResponse(
            status_code=429, content={"error": "rate_limited"}, headers={"Retry-After": exc.retry_after_header}
        )

    @app.get("/health")
    def health() -> dict:
        """Liveness probe for the hosting platform. No token: it reveals nothing."""
        return {"status": "ok"}

    @app.post("/v1/events", status_code=202)
    def ingest_events(
        batch: EventBatch, background_tasks: BackgroundTasks, project_id: str = Depends(project_id_from_request)
    ) -> dict:
        deviations = []
        with write_lock, db.connect(settings.db_path) as conn:
            ai_enabled = get_project_flags(conn, project_id).ai_enabled
            inserted = insert_events(conn, project_id, batch.events)
            for event in sorted(inserted, key=lambda e: e.ts):
                deviation = evaluate_event(conn, settings, route_classes, project_id, event)
                if deviation is not None:
                    deviations.append(deviation)
        if settings.judge is not None and ai_enabled and deviations:
            # Runs after the 202 is sent: ingestion never waits for the AI.
            background_tasks.add_task(judge_deviations, settings, write_lock, latest_per_session(deviations))
        return {"accepted": len(inserted)}

    @app.post("/v1/check")
    def post_check(
        body: CheckRequest, background_tasks: BackgroundTasks, project_id: str = Depends(project_id_from_request)
    ) -> dict:
        """Contract 9: decide a critical request before the app executes it."""
        result = check_event(settings, route_classes, write_lock, project_id, body.event)
        if result.explain_later is not None:
            # A rule block answers at once; the AI rewrites its rationale after the response.
            background_tasks.add_task(judge_deviations, settings, write_lock, [result.explain_later])
        return result.body()

    @app.get("/v1/summary")
    def get_summary(
        since: datetime | None = None,
        until: datetime | None = None,
        project_id: str = Depends(project_id_from_request),
    ):
        until = as_utc(until) if until is not None else settings.clock()
        since = as_utc(since) if since is not None else until - timedelta(hours=24)
        if since >= until:
            return JSONResponse(status_code=422, content={"error": "since must be before until"})
        with db.connect(settings.db_path) as conn:
            return build_summary(conn, project_id, since, until)

    @app.get("/v1/users")
    def get_users(project_id: str = Depends(project_id_from_request)) -> dict:
        with db.connect(settings.db_path) as conn:
            return {"users": list_users(conn, project_id, settings.clock(), settings.learn_delay_min)}

    @app.get("/v1/routes")
    def get_routes(project_id: str = Depends(project_id_from_request)) -> dict:
        """The loaded classification. Global to the engine: every client sees the same list."""
        return {"routes": route_classes.listing(), "default": "normal"}

    @app.get("/v1/users/{user_id}/profile")
    def get_profile(user_id: str, project_id: str = Depends(project_id_from_request)) -> dict:
        with db.connect(settings.db_path) as conn:
            profile = build_profile(conn, project_id, user_id, settings.clock(), settings.learn_delay_min)
            return profile.to_dict(blocked=is_blocked(conn, project_id, user_id))

    @app.get("/v1/decisions")
    def get_decisions(
        limit: int = Query(50, ge=1, le=500), project_id: str = Depends(project_id_from_request)
    ) -> dict:
        with db.connect(settings.db_path) as conn:
            return {"decisions": list_decisions(conn, project_id, limit)}

    @app.get("/v1/activity")
    def get_activity(
        limit: int = Query(50, ge=1, le=200), project_id: str = Depends(project_id_from_request)
    ) -> dict:
        with db.connect(settings.db_path) as conn:
            return {"activity": list_activity(conn, project_id, limit)}

    @app.post("/v1/decisions/{decision_id}/normal")
    def post_mark_normal(decision_id: str, project_id: str = Depends(project_id_from_request)):
        with write_lock, db.connect(settings.db_path) as conn:
            result = mark_normal(conn, project_id, decision_id)
        if result is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return result

    @app.post("/v1/admin/reset")
    def post_reset(request: Request):
        """Rehearsal-only wipe of the caller's data. Hidden (404 before auth) unless a reset secret
        is configured and sent in X-Veridian-Reset-Token."""
        sent = request.headers.get("x-veridian-reset-token", "")
        if not settings.reset_token or not hmac.compare_digest(sent.encode(), settings.reset_token.encode()):
            return JSONResponse(status_code=404, content={"error": "not_found"})
        project_id = project_id_from_request(request)
        with write_lock, db.connect(settings.db_path) as conn:
            version = reset_client(conn, project_id)
        return {"reset": True, "version": version}

    @app.get("/v1/blocklist")
    def get_blocklist(since: int | None = None, project_id: str = Depends(project_id_from_request)):
        with db.connect(settings.db_path) as conn:
            if since is not None and since == current_version(conn, project_id):
                return Response(status_code=304)
            if not get_project_flags(conn, project_id).enforce:
                # observe (decision 6): decisions are recorded, nothing is published to enforce.
                return {"version": current_version(conn, project_id), "users": [], "sessions": [], "ips": []}
            return blocklist(conn, project_id)

    return app
