"""Panel-only control endpoints: contract 10 (people, organizations, projects) and contract 12
(API keys). Wire shapes match the fake engine (edge/fake_engine/app.py) so the panel works
against either. Roles are the actor's role in the organization (docs/DISENO-PRODUCTO.md).
"""

import threading
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import db
from .auth import Caller, caller_from_request
from .control_plane import repository
from .control_plane.repository import NotFound, PermissionDenied
from .control_plane.roles import can
from .principal import Forbidden, PanelProject, ProjectRequired, panel_actor, path_project
from .project_settings import set_project_flags
from .settings import Settings


class LoginIn(BaseModel):
    google_sub: str = Field(min_length=1)
    email: str = Field(min_length=3)
    name: str | None = None


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    retention_days: int | None = Field(default=None, ge=1)
    enforcement: Literal["observe", "enforce"] | None = None
    ai_enabled: bool | None = None


NOT_FOUND = {"error": "not_found"}


def build_router(settings: Settings, write_lock: threading.Lock) -> APIRouter:
    router = APIRouter()

    def org_view(conn, org_id: str, person_id: str, with_created: bool = False) -> dict:
        org = repository.get_organization(conn, org_id)
        view = {"id": org["id"], "name": org["name"], "role": repository.get_role(conn, org_id, person_id),
                "projects": [{"id": p["id"], "name": p["name"]} for p in repository.list_org_projects(conn, org_id)]}
        if with_created:
            view["created_at"] = org["created_at"]
        return view

    def project_view(conn, project_id: str, person_id: str) -> dict:
        project = repository.get_project(conn, project_id)
        return {**project, "role": repository.role_in_project(conn, project_id, person_id)}

    def authorize_org(conn, org_id: str, person_id: str, action: str) -> None:
        if not can(repository.get_role(conn, org_id, person_id), action):
            raise Forbidden()

    # --- contract 10 ---

    @router.post("/v1/people/login")
    def post_login(body: LoginIn, caller: Caller = Depends(caller_from_request)) -> dict:
        """Upsert by google_sub, take the bootstrap and the pending invitations, answer with the orgs."""
        if caller.kind != "panel":
            raise Forbidden()
        email = body.email.strip().lower()
        now = settings.clock()
        with write_lock, db.connect(settings.db_path) as conn:
            name = body.name or repository.person_name_by_sub(conn, body.google_sub) or email
            person_id = repository.upsert_person(conn, google_sub=body.google_sub, email=email, name=name, now=now)
            repository.bootstrap_owner(conn, person_id=person_id, email=email, owners=settings.bootstrap_owners, now=now)
            repository.accept_pending_invitations(conn, person_id=person_id, verified_email=email, now=now)
            return {"person": repository.get_person(conn, person_id), "orgs": repository.list_orgs_for(conn, person_id)}

    @router.get("/v1/me/orgs")
    def get_my_orgs(person_id: str = Depends(panel_actor)) -> dict:
        with db.connect(settings.db_path) as conn:
            return {"orgs": repository.list_orgs_for(conn, person_id)}

    @router.post("/v1/orgs", status_code=201)
    def post_org(body: NameIn, person_id: str = Depends(panel_actor)) -> dict:
        with write_lock, db.connect(settings.db_path) as conn:
            person = repository.get_person(conn, person_id)
            if person["email"].strip().lower() not in settings.org_creators:
                raise Forbidden()
            org_id = repository.create_organization(conn, creator_id=person_id, name=body.name.strip(), now=settings.clock())
            return org_view(conn, org_id, person_id)

    @router.get("/v1/orgs/{org_id}")
    def get_org(org_id: str, person_id: str = Depends(panel_actor)) -> dict:
        with db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "view")
            return org_view(conn, org_id, person_id, with_created=True)

    @router.patch("/v1/orgs/{org_id}")
    def patch_org(org_id: str, body: OrgPatch, person_id: str = Depends(panel_actor)) -> dict:
        with write_lock, db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "edit_org")
            if body.name is not None:
                repository.rename_organization(conn, actor_id=person_id, org_id=org_id, name=body.name.strip(),
                                               now=settings.clock())
            return org_view(conn, org_id, person_id, with_created=True)

    @router.get("/v1/orgs/{org_id}/projects")
    def get_org_projects(org_id: str, person_id: str = Depends(panel_actor)) -> dict:
        with db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "view")
            role = repository.get_role(conn, org_id, person_id)
            return {"projects": [{**p, "role": role} for p in repository.list_org_projects(conn, org_id)]}

    @router.post("/v1/orgs/{org_id}/projects", status_code=201)
    def post_org_project(org_id: str, body: NameIn, person_id: str = Depends(panel_actor)) -> dict:
        with write_lock, db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "create_project")
            # Decision 6: a new project observes until someone turns enforcement on.
            project_id = repository.create_project(conn, actor_id=person_id, org_id=org_id, name=body.name.strip(),
                                                   enforcement="observe", now=settings.clock())
            return project_view(conn, project_id, person_id)

    @router.get("/v1/projects/{project_id}")
    def get_project(project: PanelProject = Depends(path_project("view"))) -> dict:
        with db.connect(settings.db_path) as conn:
            return project_view(conn, project.project_id, project.person_id)

    @router.patch("/v1/projects/{project_id}")
    def patch_project(body: ProjectPatch, project: PanelProject = Depends(path_project("edit_settings"))) -> dict:
        changes = body.model_dump(exclude_none=True)
        if "name" in changes:
            changes["name"] = changes["name"].strip()
        with write_lock, db.connect(settings.db_path) as conn:
            if changes:
                repository.update_project(conn, actor_id=project.person_id, project_id=project.project_id,
                                          changes=changes, now=settings.clock())
                if "enforcement" in changes or "ai_enabled" in changes:
                    set_project_flags(conn, project.project_id, enforcement=changes.get("enforcement"),
                                      ai_enabled=changes.get("ai_enabled"))
            return project_view(conn, project.project_id, project.person_id)

    # --- contract 12: API keys ---

    @router.get("/v1/projects/{project_id}/api-keys")
    def get_api_keys(project: PanelProject = Depends(path_project("manage_keys"))) -> dict:
        with db.connect(settings.db_path) as conn:
            return {"api_keys": repository.list_api_keys(conn, project.project_id)}

    @router.post("/v1/projects/{project_id}/api-keys", status_code=201)
    def post_api_key(body: NameIn, project: PanelProject = Depends(path_project("manage_keys"))) -> dict:
        with write_lock, db.connect(settings.db_path) as conn:
            created = repository.create_api_key(conn, actor_id=project.person_id, project_id=project.project_id,
                                                name=body.name.strip(), now=settings.clock())
        # The only time the full key leaves the engine.
        return {"id": created.id, "name": created.name, "prefix": created.prefix, "key": created.key}

    @router.post("/v1/projects/{project_id}/api-keys/{key_id}/revoke")
    def post_revoke_api_key(key_id: str, project: PanelProject = Depends(path_project("manage_keys"))):
        try:
            with write_lock, db.connect(settings.db_path) as conn:
                repository.revoke_api_key(conn, actor_id=project.person_id, project_id=project.project_id,
                                          key_id=key_id, now=settings.clock())
                return repository.get_api_key(conn, project.project_id, key_id)
        except NotFound:
            return JSONResponse(status_code=404, content=NOT_FOUND)

    return router


def install_error_handlers(app) -> None:
    @app.exception_handler(Forbidden)
    async def forbidden_handler(request: Request, exc: Forbidden) -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    @app.exception_handler(ProjectRequired)
    async def project_required_handler(request: Request, exc: ProjectRequired) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "project_required"})

    @app.exception_handler(PermissionDenied)
    async def permission_denied_handler(request: Request, exc: PermissionDenied) -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": "forbidden"})
