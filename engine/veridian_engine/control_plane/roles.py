"""Role matrix (docs/DISENO-PRODUCTO.md, "Roles: owner, admin, viewer").

Roles live only in the organization: a person's role in an org applies to every project of it.
"""

ROLES = ("owner", "admin", "viewer")

# Open decision 3 of docs/DISENO-PRODUCTO.md: may a viewer mark a decision normal? Marking normal
# lifts a block, so the design recommends no. This is the only place that decides it.
VIEWER_CAN_MARK_NORMAL = False

VIEWER_ACTIONS = frozenset({"view"} | ({"mark_normal"} if VIEWER_CAN_MARK_NORMAL else set()))
ADMIN_ACTIONS = VIEWER_ACTIONS | {
    "mark_normal",
    "manage_keys",
    "manage_routes",
    "manage_webhook",
    "manage_members",
    "edit_settings",
    "erase_user_data",
    "create_project",
    "edit_org",
}
OWNER_ACTIONS = ADMIN_ACTIONS | {"manage_owners", "erase_project_data", "delete_project"}

ACTIONS = (
    "view",
    "mark_normal",
    "manage_keys",
    "manage_routes",
    "manage_webhook",
    "manage_members",
    "edit_settings",
    "erase_user_data",
    "create_project",
    "edit_org",
    "manage_owners",
    "erase_project_data",
    "delete_project",
)

_ALLOWED = {"owner": OWNER_ACTIONS, "admin": ADMIN_ACTIONS, "viewer": VIEWER_ACTIONS}


def can(role: str | None, action: str) -> bool:
    return action in _ALLOWED.get(role, frozenset())


def can_manage_role(actor_role: str | None, target_role: str) -> bool:
    """Whether actor_role may invite, remove, or assign members holding target_role."""
    if target_role == "owner":
        return can(actor_role, "manage_owners")
    return target_role in ROLES and can(actor_role, "manage_members")
