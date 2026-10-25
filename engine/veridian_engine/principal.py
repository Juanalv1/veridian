"""Who may call what (docs/DISENO-PRODUCTO.md, "Cómo se autentica el panel contra el motor").

- `sdk`: a project's API key. Only the SDK endpoints (events, check) plus blocklist and routes,
  always of the key's own project.
- `panel`: the panel's service credential plus `X-Veridian-Actor` (a person id) and, on data
  reads, `X-Veridian-Project`. The engine reads the actor's role in the organization of that
  project from the database on every request; there are no project roles. A non-member, an
  unknown project and a deleted one all get the same 403.

Order of checks, as in the fake engine: credential (401), actor (403), project header (400),
role (403).
"""

from dataclasses import dataclass

from fastapi import Depends, Request

from . import db
from .auth import Caller, caller_from_request
from .control_plane.repository import get_person, project_org, get_role
from .control_plane.roles import can


class Forbidden(Exception):
    """Rendered as 403 {"error": "forbidden"}."""


class ProjectRequired(Exception):
    """Rendered as 400 {"error": "project_required"}."""


@dataclass(frozen=True)
class PanelProject:
    person_id: str
    project_id: str
    org_id: str
    role: str


def _db_path(request: Request) -> str:
    return request.app.state.settings.db_path


def _actor(request: Request, caller: Caller) -> str:
    if caller.kind != "panel":
        raise Forbidden()
    person_id = request.headers.get("x-veridian-actor", "").strip()
    if not person_id:
        raise Forbidden()
    with db.connect(_db_path(request)) as conn:
        if get_person(conn, person_id) is None:
            raise Forbidden()
    return person_id


def _authorize_project(request: Request, person_id: str, project_id: str, action: str) -> PanelProject:
    with db.connect(_db_path(request)) as conn:
        org_id = project_org(conn, project_id)
        role = get_role(conn, org_id, person_id) if org_id else None
    if not can(role, action):
        raise Forbidden()
    return PanelProject(person_id=person_id, project_id=project_id, org_id=org_id, role=role)


def _header_project(request: Request, caller: Caller, action: str) -> PanelProject:
    person_id = _actor(request, caller)
    project_id = request.headers.get("x-veridian-project", "").strip()
    if not project_id:
        raise ProjectRequired()
    return _authorize_project(request, person_id, project_id, action)


def sdk_project(caller: Caller = Depends(caller_from_request)) -> str:
    """SDK-only endpoints: the key's project. The panel credential is refused."""
    if caller.kind != "sdk":
        raise Forbidden()
    return caller.project_id


def panel_actor(request: Request, caller: Caller = Depends(caller_from_request)) -> str:
    """Panel-only endpoints that name no project in a header: the acting person's id."""
    return _actor(request, caller)


def panel_project(action: str):
    """Panel-only data endpoints: the project in `X-Veridian-Project`, if the actor's role in its
    organization allows `action`."""

    def dependency(request: Request, caller: Caller = Depends(caller_from_request)) -> PanelProject:
        return _header_project(request, caller, action)

    return dependency


def shared_project(request: Request, caller: Caller = Depends(caller_from_request)) -> str:
    """Blocklist and routes: an SDK key's own project, or the panel's header project for any member."""
    if caller.kind == "sdk":
        return caller.project_id
    return _header_project(request, caller, "view").project_id


def path_project(action: str):
    """Panel-only administration of the project named in the path (`/v1/projects/{project_id}/...`)."""

    def dependency(project_id: str, request: Request, caller: Caller = Depends(caller_from_request)) -> PanelProject:
        return _authorize_project(request, _actor(request, caller), project_id, action)

    return dependency
