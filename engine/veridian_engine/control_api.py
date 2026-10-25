"""Panel-only control endpoints: contract 10 (people, organizations, projects), contract 12
(API keys) and contract 13 (webhook). Wire shapes match the fake engine (edge/fake_engine/app.py)
so the panel works against either. Roles are the actor's role in the organization (docs/DISENO-PRODUCTO.md).
"""

import threading
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import db, webhook_store
from .auth import Caller, caller_from_request
from .control_plane import repository
from .control_plane.repository import (
    AlreadyMember,
    InvitationUnavailable,
    LastOwnerError,
    NotFound,
    PermissionDenied,
    SelfChangeError,
)
from .control_plane.roles import can
from .notifications.outbox import list_notifications, new_notification_id
from .notifications.secret_box import SecretEncryptionError, decrypt_secret
from .notifications.webhook import (
    TEST_EVENT,
    HttpxTransport,
    UnsafeUrlError,
    build_test_payload,
    default_resolver,
    deliver,
)
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


class WebhookIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class WebhookPatch(BaseModel):
    enabled: bool


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    retention_days: int | None = Field(default=None, ge=1)
    enforcement: Literal["observe", "enforce"] | None = None
    ai_enabled: bool | None = None


Role = Literal["owner", "admin", "viewer"]


class RoleIn(BaseModel):
    role: Role


class InvitationIn(BaseModel):
    email: str = Field(max_length=254, pattern=r"^\s*[^@\s]+@[^@\s]+\.[^@\s]+\s*$")
    role: Role


NOT_FOUND = {"error": "not_found"}
SECRET_KEY_MISSING = {"error": "secret_key_missing"}


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

    @router.delete("/v1/projects/{project_id}", status_code=204)
    def delete_project(project: PanelProject = Depends(path_project("delete_project"))) -> Response:
        with write_lock, db.connect(settings.db_path) as conn:
            repository.delete_project(conn, actor_id=project.person_id, project_id=project.project_id,
                                      now=settings.clock())
        return Response(status_code=204)

    # --- contract 15: audit log (read only; no endpoint changes or deletes it) ---

    @router.get("/v1/orgs/{org_id}/audit")
    def get_audit(org_id: str, person_id: str = Depends(panel_actor), project: str | None = None,
                  limit: int = Query(50, ge=1, le=200), before: int | None = Query(None, ge=1)) -> dict:
        with db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "read_audit")
            entries, next_before = repository.list_audit(conn, org_id, project_id=project, limit=limit, before=before)
        return {"entries": entries, "next_before": next_before}

    # --- contract 11: members and invitations of the organization ---
    # The router only checks membership and the coarse action; which roles an actor may touch,
    # self-changes and the last owner are domain rules in the repository.

    @router.get("/v1/orgs/{org_id}/members")
    def get_members(org_id: str, person_id: str = Depends(panel_actor)) -> dict:
        with db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "view")
            return {"members": repository.list_members(conn, org_id)}

    @router.patch("/v1/orgs/{org_id}/members/{member_id}")
    def patch_member(org_id: str, member_id: str, body: RoleIn, person_id: str = Depends(panel_actor)) -> dict:
        with write_lock, db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "manage_members")
            repository.set_member_role(conn, actor_id=person_id, org_id=org_id, person_id=member_id,
                                       role=body.role, now=settings.clock())
            return repository.get_member(conn, org_id, member_id)

    @router.delete("/v1/orgs/{org_id}/members/{member_id}", status_code=204)
    def delete_member(org_id: str, member_id: str, person_id: str = Depends(panel_actor)) -> Response:
        with write_lock, db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "manage_members")
            repository.remove_member(conn, actor_id=person_id, org_id=org_id, person_id=member_id,
                                     now=settings.clock())
        return Response(status_code=204)

    @router.get("/v1/orgs/{org_id}/invitations")
    def get_invitations(org_id: str, person_id: str = Depends(panel_actor)) -> dict:
        with db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "view")
            return {"invitations": repository.list_pending_invitations(conn, org_id, settings.clock())}

    @router.post("/v1/orgs/{org_id}/invitations", status_code=201)
    def post_invitation(org_id: str, body: InvitationIn, person_id: str = Depends(panel_actor)) -> dict:
        now = settings.clock()
        with write_lock, db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "manage_members")
            invitation_id = repository.create_invitation(
                conn, actor_id=person_id, org_id=org_id, email=body.email, role=body.role, now=now,
                ttl=timedelta(days=settings.invitation_days),
            )
            return repository.get_invitation(conn, org_id, invitation_id, now)

    @router.delete("/v1/orgs/{org_id}/invitations/{invitation_id}", status_code=204)
    def delete_invitation(org_id: str, invitation_id: str, person_id: str = Depends(panel_actor)) -> Response:
        with write_lock, db.connect(settings.db_path) as conn:
            authorize_org(conn, org_id, person_id, "manage_members")
            repository.revoke_invitation(conn, actor_id=person_id, org_id=org_id, invitation_id=invitation_id,
                                         now=settings.clock())
        return Response(status_code=204)

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

    # --- contract 13: webhook ---

    resolver = settings.webhook_resolver or default_resolver

    def audit_webhook(conn, project: PanelProject, action: str, meta: dict | None = None) -> None:
        repository.record_audit(conn, actor_person_id=project.person_id, actor_kind="person", action=action,
                                target_type="webhook", target_id=project.project_id, now=settings.clock(),
                                meta=meta, project_id=project.project_id, org_id=project.org_id)

    @router.get("/v1/projects/{project_id}/webhook")
    def get_webhook(project: PanelProject = Depends(path_project("manage_webhook"))) -> dict:
        with db.connect(settings.db_path) as conn:
            return {"webhook": webhook_store.get_webhook_endpoint(conn, project.project_id)}

    @router.put("/v1/projects/{project_id}/webhook")
    def put_webhook(body: WebhookIn, project: PanelProject = Depends(path_project("manage_webhook"))):
        url = body.url.strip()
        try:
            with write_lock, db.connect(settings.db_path) as conn:
                secret = webhook_store.set_webhook_endpoint(conn, project.project_id, url, now=settings.clock(),
                                                            resolver=resolver)
                audit_webhook(conn, project, "webhook.set", {"url": url})
                endpoint = webhook_store.get_webhook_endpoint(conn, project.project_id)
        except UnsafeUrlError as exc:
            return JSONResponse(status_code=422, content={"error": "unsafe_url", "detail": str(exc)})
        except SecretEncryptionError:
            return JSONResponse(status_code=503, content=SECRET_KEY_MISSING)
        # The only time the signing secret leaves the engine.
        return {**endpoint, "secret": secret}

    @router.patch("/v1/projects/{project_id}/webhook")
    def patch_webhook(body: WebhookPatch, project: PanelProject = Depends(path_project("manage_webhook"))):
        with write_lock, db.connect(settings.db_path) as conn:
            if not webhook_store.set_webhook_enabled(conn, project.project_id, body.enabled):
                return JSONResponse(status_code=404, content=NOT_FOUND)
            audit_webhook(conn, project, "webhook.enable", {"enabled": body.enabled})
            return {"webhook": webhook_store.get_webhook_endpoint(conn, project.project_id)}

    @router.delete("/v1/projects/{project_id}/webhook", status_code=204)
    def delete_webhook(project: PanelProject = Depends(path_project("manage_webhook"))):
        with write_lock, db.connect(settings.db_path) as conn:
            if not webhook_store.clear_webhook_endpoint(conn, project.project_id):
                return JSONResponse(status_code=404, content=NOT_FOUND)
            audit_webhook(conn, project, "webhook.delete")
        return Response(status_code=204)

    @router.get("/v1/projects/{project_id}/webhook/deliveries")
    def get_webhook_deliveries(limit: int = Query(default=50, ge=1, le=200),
                               project: PanelProject = Depends(path_project("manage_webhook"))) -> dict:
        with db.connect(settings.db_path) as conn:
            return {"deliveries": list_notifications(conn, project.project_id, limit)}

    @router.post("/v1/projects/{project_id}/webhook/test")
    def post_webhook_test(project: PanelProject = Depends(path_project("manage_webhook"))):
        """Send one signed `webhook.test` now and report the outcome. Not queued, not retried."""
        now = settings.clock()
        with db.connect(settings.db_path) as conn:
            endpoint = conn.execute("SELECT url, secret_enc FROM webhook_endpoint WHERE project_id = ?",
                                    (project.project_id,)).fetchone()
            name = conn.execute("SELECT name FROM project WHERE id = ?", (project.project_id,)).fetchone()
        if endpoint is None:
            return JSONResponse(status_code=404, content=NOT_FOUND)
        try:
            secret = decrypt_secret(endpoint["secret_enc"])
        except SecretEncryptionError:
            return JSONResponse(status_code=503, content=SECRET_KEY_MISSING)
        delivery_id = new_notification_id()
        payload = build_test_payload(notification_id=delivery_id, created_at=now, project_id=project.project_id,
                                     project_name=name["name"] if name else project.project_id)
        # No connection or lock is held while the receiver answers (up to the 5 s timeout).
        result = deliver(endpoint["url"], secret, payload, delivery_id=delivery_id,
                         transport=settings.webhook_transport or HttpxTransport(), clock=settings.clock,
                         resolver=resolver, event=TEST_EVENT)
        with write_lock, db.connect(settings.db_path) as conn:
            audit_webhook(conn, project, "webhook.test", {"delivered": result.delivered,
                                                          "status_code": result.status_code})
        return {"delivered": result.delivered, "status_code": result.status_code, "error": result.error}

    return router


def install_error_handlers(app) -> None:
    @app.exception_handler(Forbidden)
    async def forbidden_handler(request: Request, exc: Forbidden) -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    @app.exception_handler(ProjectRequired)
    async def project_required_handler(request: Request, exc: ProjectRequired) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "project_required"})

    @app.exception_handler(NotFound)
    @app.exception_handler(InvitationUnavailable)
    async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content=NOT_FOUND)

    @app.exception_handler(SelfChangeError)
    async def self_change_handler(request: Request, exc: SelfChangeError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "cannot_change_self"})

    @app.exception_handler(LastOwnerError)
    async def last_owner_handler(request: Request, exc: LastOwnerError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "last_owner"})

    @app.exception_handler(AlreadyMember)
    async def already_member_handler(request: Request, exc: AlreadyMember) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "already_member"})

    @app.exception_handler(PermissionDenied)
    async def permission_denied_handler(request: Request, exc: PermissionDenied) -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": "forbidden"})
