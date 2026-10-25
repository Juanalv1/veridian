import pytest

from veridian_engine.control_plane.roles import ACTIONS, can, can_manage_role


def test_viewer_can_only_view():
    assert can("viewer", "view")
    assert [action for action in ACTIONS if can("viewer", action)] == ["view"]


def test_viewer_cannot_mark_normal_but_admin_and_owner_can():
    assert not can("viewer", "mark_normal")
    assert can("admin", "mark_normal")
    assert can("owner", "mark_normal")


@pytest.mark.parametrize("action", ["manage_owners", "erase_project_data", "delete_project"])
def test_irreversible_actions_are_owner_only(action):
    assert can("owner", action)
    assert not can("admin", action)
    assert not can("viewer", action)


@pytest.mark.parametrize(
    "action",
    ["manage_keys", "manage_routes", "manage_webhook", "manage_members", "edit_settings", "erase_user_data"],
)
def test_admin_actions(action):
    assert can("admin", action)
    assert can("owner", action)
    assert not can("viewer", action)


def test_unknown_role_or_action_is_denied():
    assert not can("superuser", "view")
    assert not can("owner", "launch_rockets")


def test_admin_manages_viewers_and_admins_but_not_owners():
    assert can_manage_role("admin", "viewer")
    assert can_manage_role("admin", "admin")
    assert not can_manage_role("admin", "owner")
    assert can_manage_role("owner", "owner")
    assert not can_manage_role("viewer", "viewer")


def test_decision_3_lives_in_one_switch_and_defaults_to_viewer_cannot_mark_normal():
    from veridian_engine.control_plane import roles

    assert roles.VIEWER_CAN_MARK_NORMAL is False
    assert not can("viewer", "mark_normal")


@pytest.mark.parametrize("action", ["create_project", "edit_org"])
def test_org_admin_actions(action):
    assert can("admin", action)
    assert can("owner", action)
    assert not can("viewer", action)
