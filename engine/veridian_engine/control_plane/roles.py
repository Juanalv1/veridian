"""Role matrix (docs/DISENO-PRODUCTO.md, "Roles: owner, admin, viewer")."""

ROLES = ("owner", "admin", "viewer")

VIEWER_ACTIONS = frozenset({"view"})
ADMIN_ACTIONS = VIEWER_ACTIONS | {
    # A viewer cannot mark a decision normal: it lifts a block (decision 3).
    "mark_normal",
    "manage_keys",
    "manage_routes",
    "manage_webhook",
    "manage_members",
    "edit_settings",
    "erase_user_data",
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
