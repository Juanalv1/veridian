import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from dotenv import find_dotenv, load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse

from . import db
from .activity import list_activity
from .auth import ApiKeyAuth, Caller, RateLimited, Unauthorized, caller_from_request
from .check import CheckRequest, check_event
from .control_api import build_router, install_error_handlers
from .control_plane.legacy import seed_legacy
from .control_plane.repository import record_audit
from .decisions import blocklist, current_version, list_decisions, mark_normal
from .events import EventBatch, insert_events, valid_events
from .judging import judge_deviations, latest_per_session
from .notifications.email_sender import build_email_sender
from .notifications.sender import build_webhook_sender, webhook_lifespan
from .principal import Forbidden, PanelProject, panel_project, sdk_project, shared_project
from .project_settings import get_project_flags
from .profile import build_profile, is_blocked
from .retention_worker import build_retention_worker
from .route_catalog import RouteCatalogIn, build_route_classifier, record_report
from .route_classes import RouteClasses, seed_unseeded
from .rules import evaluate_event
from .self_unlock import self_unlock_applies
from .self_unlock_page import VALID, read_token, unlock
from .settings import Settings
from .summary import as_utc, build_summary, list_users


@asynccontextmanager
async def background_lifespan(app):
    """The webhook and email senders, the retention worker and the route classifier run while
    the app does."""
    workers = [w for w in (getattr(app.state, "retention_worker", None), getattr(app.state, "route_classifier", None)) if w]
    for worker in workers:
        worker.start()
    try:
        async with webhook_lifespan(app):
            yield
    finally:
        for worker in workers:
            worker.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        # Real environment variables win over the nearest .env (searched upward from the cwd).
        load_dotenv(find_dotenv(usecwd=True))
        settings = Settings.from_env()
    db.init_db(settings.db_path)
    # routes.yaml is the template every project starts with (U7); each project then owns its rules.
    route_template = RouteClasses.load(settings.routes_file)
    with db.connect(settings.db_path) as conn:
        seed_legacy(conn, settings.tokens, settings.clock())
        seed_unseeded(conn, route_template)

    app = FastAPI(title="Veridian engine", lifespan=background_lifespan)
    app.state.settings = settings
    app.state.route_template = route_template
    app.state.auth = ApiKeyAuth(settings)
    write_lock = threading.Lock()  # single process: serialize writes that read-then-write
    # Signed block webhooks (U12): started and stopped with the app; None when off.
    app.state.webhook_sender = build_webhook_sender(settings, write_lock)
    # Retention (U13): purges by retention_days and wipes deleted projects; None when off.
    app.state.retention_worker = build_retention_worker(settings, write_lock)
    # Block alert email to owners/admins (U17), same switch and lifespan.
    app.state.email_sender = build_email_sender(settings, write_lock)
    # Automatic route classification (contract 16); None when off.
    app.state.route_classifier = build_route_classifier(settings, write_lock)

    @app.exception_handler(Unauthorized)
    async def unauthorized_handler(request: Request, exc: Unauthorized) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    @app.exception_handler(RateLimited)
    async def rate_limited_handler(request: Request, exc: RateLimited) -> JSONResponse:
        return JSONResponse(
            status_code=429, content={"error": "rate_limited"}, headers={"Retry-After": exc.retry_after_header}
        )

    install_error_handlers(app)
    view = panel_project("view")  # panel only, any member of the project's organization

    @app.get("/health")
    def health() -> dict:
        """Liveness probe for the hosting platform. No token: it reveals nothing."""
        return {"status": "ok"}

    @app.post("/v1/events", status_code=202)
    def ingest_events(
        batch: EventBatch, background_tasks: BackgroundTasks, project_id: str = Depends(sdk_project)
    ) -> dict:
        events, rejected = valid_events(batch.events)
        deviations = []
        with write_lock, db.connect(settings.db_path) as conn:
            ai_enabled = get_project_flags(conn, project_id).ai_enabled
            route_classes = RouteClasses.for_project(conn, project_id)
            inserted = insert_events(conn, project_id, events)
            for event in sorted(inserted, key=lambda e: e.ts):
                deviation = evaluate_event(conn, settings, route_classes, project_id, event)
                if deviation is not None:
                    deviations.append(deviation)
        if settings.judge is not None and ai_enabled and deviations:
            # Runs after the 202 is sent: ingestion never waits for the AI.
            background_tasks.add_task(judge_deviations, settings, write_lock, latest_per_session(deviations))
        answer = {"accepted": len(inserted)}
        if rejected:
            answer["rejected"] = rejected  # only when some were, so the usual answer keeps its shape
        return answer

    @app.post("/v1/check")
    def post_check(
        body: CheckRequest, background_tasks: BackgroundTasks, project_id: str = Depends(sdk_project)
    ) -> dict:
        """Contract 9: decide a critical request before the app executes it."""
        with db.connect(settings.db_path) as conn:
            route_classes = RouteClasses.for_project(conn, project_id)
        result = check_event(settings, route_classes, write_lock, project_id, body.event)
        if result.verdict == "block":
            with db.connect(settings.db_path) as conn:
                result.self_unlock = self_unlock_applies(conn, project_id, body.event.user_id)
        if result.explain_later is not None:
            # A rule block answers at once; the AI rewrites its rationale after the response.
            background_tasks.add_task(judge_deviations, settings, write_lock, [result.explain_later])
        return result.body()

    @app.get("/v1/summary")
    def get_summary(
        since: datetime | None = None,
        until: datetime | None = None,
        project: PanelProject = Depends(view),
    ):
        project_id = project.project_id
        until = as_utc(until) if until is not None else settings.clock()
        since = as_utc(since) if since is not None else until - timedelta(hours=24)
        if since >= until:
            return JSONResponse(status_code=422, content={"error": "since must be before until"})
        with db.connect(settings.db_path) as conn:
            return build_summary(conn, project_id, since, until)

    @app.get("/v1/users")
    def get_users(project: PanelProject = Depends(view)) -> dict:
        project_id = project.project_id
        with db.connect(settings.db_path) as conn:
            return {"users": list_users(conn, project_id, settings.clock(), settings.learn_delay_min)}

    @app.get("/v1/routes")
    def get_routes(project_id: str = Depends(shared_project)) -> dict:
        """The caller's project classification (U7): the SDK key's project, or the panel's header."""
        with db.connect(settings.db_path) as conn:
            return {"routes": RouteClasses.for_project(conn, project_id).listing(), "default": "normal"}

    @app.put("/v1/routes/catalog")
    def put_route_catalog(body: RouteCatalogIn, project_id: str = Depends(sdk_project)) -> dict:
        """Contract 16: the SDK reports the app's routes at startup; new or changed ones are
        classified in the background and land in GET /v1/routes."""
        keys = {(r.method, r.route) for r in body.routes}
        if len(keys) != len(body.routes):
            return JSONResponse(status_code=422, content={"error": "duplicate_route"})
        with write_lock, db.connect(settings.db_path) as conn:
            pending = record_report(conn, project_id, [r.model_dump() for r in body.routes], settings.clock())
        return {"received": len(body.routes), "to_classify": pending}

    @app.get("/v1/users/{user_id}/profile")
    def get_profile(user_id: str, project: PanelProject = Depends(view)) -> dict:
        project_id = project.project_id
        with db.connect(settings.db_path) as conn:
            profile = build_profile(conn, project_id, user_id, settings.clock(), settings.learn_delay_min)
            return profile.to_dict(blocked=is_blocked(conn, project_id, user_id))

    @app.get("/v1/decisions")
    def get_decisions(
        limit: int = Query(50, ge=1, le=500), project: PanelProject = Depends(view)
    ) -> dict:
        project_id = project.project_id
        with db.connect(settings.db_path) as conn:
            return {"decisions": list_decisions(conn, project_id, limit)}

    @app.get("/v1/activity")
    def get_activity(
        limit: int = Query(50, ge=1, le=200), project: PanelProject = Depends(view)
    ) -> dict:
        project_id = project.project_id
        with db.connect(settings.db_path) as conn:
            return {"activity": list_activity(conn, project_id, limit)}

    @app.post("/v1/decisions/{decision_id}/normal")
    def post_mark_normal(decision_id: str, project: PanelProject = Depends(panel_project("mark_normal"))):
        """Lifts a block: needs the mark_normal role (decision 3, control_plane/roles.py) and is audited
        in the same transaction."""
        with write_lock, db.connect(settings.db_path) as conn:
            result = mark_normal(conn, project.project_id, decision_id)
            if result is not None:
                record_audit(
                    conn,
                    org_id=project.org_id,
                    project_id=project.project_id,
                    actor_person_id=project.person_id,
                    actor_kind="person",
                    action="decision.marked_normal",
                    target_type="decision",
                    target_id=decision_id,
                    now=settings.clock(),
                )
        if result is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return result

    # --- self-unlock by email: the panel's public page calls these with its service credential ---

    def unlock_token(request: Request, caller: Caller = Depends(caller_from_request)) -> str:
        """The user's token, from a header (a URL would put it in the access logs). Only the panel."""
        if caller.kind != "panel":
            raise Forbidden()
        return request.headers.get("x-veridian-unlock-token", "").strip()

    def token_body(view) -> dict:
        return {"state": view.state, "brand": view.brand, "access": view.access, "expires_at": view.expires_at}

    @app.get("/v1/self-unlock")
    def get_self_unlock(token: str = Depends(unlock_token)):
        """State and page data of the link. Never changes anything."""
        with db.connect(settings.db_path) as conn:
            view = read_token(conn, token, settings.clock())
        if view is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return token_body(view)

    @app.post("/v1/self-unlock")
    def post_self_unlock(token: str = Depends(unlock_token)):
        """The user confirms: marks the decision normal (as the admin's mark-normal does) and audits
        `decision.self_unlocked`. An expired, used or closed token is a 409 and unlocks nothing."""
        with write_lock, db.connect(settings.db_path) as conn:
            view = unlock(conn, token, settings.clock())
        if view is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        if view.state != VALID:
            return JSONResponse(status_code=409, content={"error": view.state})
        return {"unlocked": True, "brand": view.brand}

    @app.get("/v1/blocklist")
    def get_blocklist(since: int | None = None, project_id: str = Depends(shared_project)):
        with db.connect(settings.db_path) as conn:
            if since is not None and since == current_version(conn, project_id):
                return Response(status_code=304)
            if not get_project_flags(conn, project_id).enforce:
                # observe (decision 6): decisions are recorded, nothing is published to enforce.
                return {"version": current_version(conn, project_id), "users": [], "sessions": [], "ips": []}
            return blocklist(conn, project_id)

    app.include_router(build_router(settings, write_lock))
    return app
