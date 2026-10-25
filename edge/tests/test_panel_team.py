"""Panel: members and invitations (T5), over the real fake engine (contracts 11 and 12 and 15).

The org is always the one of the session project; no form field names an org. The engine re-checks
every role, and the panel shows what it answers.
"""

import re

import httpx
import pytest

from tests.panel_support import EMAIL, make_panel, session_of, stub_client
from tests.test_panel_accounts import (OTHER, google_transport, invited, link, member_panel, new_engine, org_a, org_b,
                                       panel_of, project_a, project_b, text)

PAST = "2000-01-01T00:00:00Z"


def block(html, attr, value):
    m = re.search(r'<li[^>]*data-%s="%s"[^>]*>(.*?)</li>' % (attr, re.escape(value)), html, re.S)
    return m.group(1) if m else None


def member(html, email):
    return block(html, "member", email)


def invitation(html, email):
    return block(html, "invite", email)


def roles(engine, org_id):
    org = engine.state.orgs[org_id]
    return {engine.state.people[p].email: r for p, r in org.members.items()}


def two_people(role="admin"):
    """Engine with the owner (EMAIL) and `OTHER` as `role`, both logged in once. Returns (engine, owner_panel, other_panel)."""
    engine = new_engine()
    owner = panel_of(engine)
    other = member_panel(engine, org_a(engine), role)
    return engine, owner, other


def pid_of(engine, email):
    return next(p.id for p in engine.state.people.values() if p.email == email)


# --- the page -----------------------------------------------------------------------------------------------


def test_the_members_page_names_the_org_and_marks_you():
    engine, owner, _ = two_people("viewer")
    html = owner.get("/miembros").text
    assert "Miembros de Demo 1" in html
    assert "El rol de cada persona vale para todos los proyectos de Demo 1. Siempre queda al menos un owner." in text(html)
    mine = member(html, EMAIL)
    assert "(tú)" in text(mine) and "Owner" in text(mine) and "<select" not in mine and "Quitar" not in mine
    assert "Personas · 2" in text(html)


def test_the_page_follows_the_org_of_the_open_project():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/proyectos/abrir", data={"project_id": project_b(engine)})
    assert "Miembros de Demo 2" in panel.get("/miembros").text


def test_an_owner_can_invite_owners_and_edit_everyone_but_themselves():
    engine, owner, _ = two_people("admin")
    html = owner.get("/miembros").text
    assert "Invitar a una persona" in html
    assert re.search(r'<select[^>]*id="inv-role".*?<option[^>]*value="owner"', html, re.S)
    other = member(html, OTHER)
    assert "<select" in other and 'value="owner"' in other and "Quitar" in other
    assert f"/miembros/{pid_of(engine, OTHER)}/rol" in other and f"/miembros/{pid_of(engine, OTHER)}/quitar" in other


def test_an_admin_cannot_invite_owners_or_touch_owners():
    engine, _, admin = two_people("admin")
    html = admin.get("/miembros").text
    assert "Invitar a una persona" in html
    invite_form = re.search(r'<select[^>]*id="inv-role".*?</select>', html, re.S).group(0)
    assert "Viewer" in invite_form and "Admin" in invite_form and "owner" not in invite_form
    owner_row = member(html, EMAIL)
    assert "<select" not in owner_row and "Quitar" not in owner_row and "Owner" in text(owner_row)
    assert "(tú)" in text(member(html, OTHER)) and "<select" not in member(html, OTHER)


def test_an_admin_edits_viewers_with_only_viewer_and_admin_as_options():
    engine = new_engine()
    panel_of(engine)
    invited(engine, org_a(engine), "adm@example.com", "admin")
    invited(engine, org_a(engine), "vw@example.com", "viewer")
    admin = panel_of(engine, email="adm@example.com", sub="g-adm")
    panel_of(engine, email="vw@example.com", sub="g-vw")
    row = member(admin.get("/miembros").text, "vw@example.com")
    assert "Quitar" in row and 'value="viewer"' in row and 'value="admin"' in row and 'value="owner"' not in row


def test_a_viewer_sees_the_list_and_nothing_to_change():
    engine, _, viewer = two_people("viewer")
    html = viewer.get("/miembros").text
    assert "Invitar a una persona" not in html and "<select" not in html and "Quitar" not in html
    assert "Tienes rol viewer" in text(html) and "solo un admin u owner invita, cambia roles o quita personas" in text(html)
    assert EMAIL in html and OTHER in html


def test_the_only_owner_is_called_so():
    engine, owner, _ = two_people("viewer")
    assert "único owner" in text(member(owner.get("/miembros").text, EMAIL))


def test_names_and_emails_are_escaped():
    engine = new_engine()
    owner = panel_of(engine)
    invited(engine, org_a(engine), "x@example.com", "viewer")
    make_panel(link(engine), transport=google_transport(sub="g-x", email="x@example.com", name="<script>alert(1)</script>"))
    html = owner.get("/miembros").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_pending_invitations_show_role_and_expiry():
    engine, owner, _ = two_people("viewer")
    owner.post("/miembros/invitar", data={"email": "marta@example.com", "role": "admin"})
    engine.state.orgs[org_a(engine)].invite("pedro@example.com", "viewer", PAST)
    html = owner.get("/miembros").text
    assert "Invitaciones pendientes · 2" in text(html)
    pending = text(invitation(html, "marta@example.com"))
    assert "Admin" in pending and "vence en 14 días" in pending and "Cancelar" in pending and "Reenviar" not in pending
    expired = text(invitation(html, "pedro@example.com"))
    assert "Viewer" in expired and "vencida" in expired and "Reenviar" in expired and "Cancelar" in expired


def test_a_viewer_sees_pending_invitations_without_actions():
    engine, owner, viewer = two_people("viewer")
    owner.post("/miembros/invitar", data={"email": "marta@example.com", "role": "admin"})
    row = invitation(viewer.get("/miembros").text, "marta@example.com")
    assert "marta@example.com" in row and "<button" not in row


def test_an_admin_gets_no_buttons_on_an_invitation_to_owner():
    engine, owner, admin = two_people("admin")
    owner.post("/miembros/invitar", data={"email": "boss@example.com", "role": "owner"})
    assert "<button" not in invitation(admin.get("/miembros").text, "boss@example.com")


# --- inviting ---------------------------------------------------------------------------------------------------


def test_inviting_creates_the_invitation_and_the_person_gets_access_on_login():
    engine, owner, _ = two_people("viewer")
    r = owner.post("/miembros/invitar", data={"email": "Marta@Example.com", "role": "admin"})
    assert r.status_code == 303 and r.headers["location"] == "/miembros"
    page = owner.get("/miembros").text
    assert "marta@example.com" in page and "Invitación creada para marta@example.com" in text(page)
    marta = panel_of(engine, email="marta@example.com", sub="g-marta")
    assert "Miembros de Demo 1" in marta.get("/miembros").text
    assert roles(engine, org_a(engine))["marta@example.com"] == "admin"


def test_inviting_a_member_shows_the_error_under_the_form_and_keeps_the_email():
    engine, owner, _ = two_people("viewer")
    r = owner.post("/miembros/invitar", data={"email": OTHER, "role": "viewer"})
    assert r.status_code == 409
    assert re.search(r'role="alert"[^>]*>\s*Esa persona ya es miembro de esta organización\.', r.text)
    assert f'value="{OTHER}"' in r.text
    assert 'aria-invalid="true"' in r.text


def test_a_bad_email_or_role_is_rejected_with_a_message():
    engine, owner, _ = two_people("viewer")
    bad_email = owner.post("/miembros/invitar", data={"email": "nope", "role": "viewer"})
    assert bad_email.status_code == 400 and "Escribe un correo válido" in bad_email.text
    bad_role = owner.post("/miembros/invitar", data={"email": "a@example.com", "role": "emperor"})
    assert bad_role.status_code == 400 and "Elige un rol" in bad_role.text
    assert engine.state.orgs[org_a(engine)].pending() == []


def test_a_forged_owner_invitation_by_an_admin_is_refused_by_the_engine():
    engine, _, admin = two_people("admin")
    r = admin.post("/miembros/invitar", data={"email": "boss@example.com", "role": "owner"})
    assert r.status_code == 403 and "Solo un owner puede invitar a otro owner" in r.text
    assert engine.state.orgs[org_a(engine)].pending() == []


def test_a_viewer_forging_an_invitation_is_refused_by_the_engine():
    engine, _, viewer = two_people("viewer")
    r = viewer.post("/miembros/invitar", data={"email": "a@example.com", "role": "viewer"})
    assert r.status_code == 403 and "No tienes permiso" in r.text
    assert engine.state.orgs[org_a(engine)].pending() == []


# --- roles, removal, cancel and resend ------------------------------------------------------------------------------


def flash_of(panel, path="/miembros"):
    return text(re.search(r'<div class="note flash[^"]*"[^>]*>(.*?)</div>', panel.get(path).text, re.S).group(1))


def test_an_owner_changes_a_role():
    engine, owner, _ = two_people("viewer")
    r = owner.post(f"/miembros/{pid_of(engine, OTHER)}/rol", data={"role": "admin"})
    assert r.status_code == 303 and r.headers["location"] == "/miembros"
    assert roles(engine, org_a(engine))[OTHER] == "admin"
    assert "other@example.com ahora es Admin" in flash_of(owner)


def test_changing_your_own_role_is_refused_with_the_reason():
    engine, owner, _ = two_people("viewer")
    owner.post(f"/miembros/{pid_of(engine, EMAIL)}/rol", data={"role": "viewer"})
    assert "No puedes cambiar tu propio rol ni quitarte a ti mismo" in flash_of(owner)
    assert roles(engine, org_a(engine))[EMAIL] == "owner"


def test_an_admin_forging_a_change_on_an_owner_is_refused():
    engine, _, admin = two_people("admin")
    admin.post(f"/miembros/{pid_of(engine, EMAIL)}/rol", data={"role": "viewer"})
    assert "No tienes permiso para hacer ese cambio" in flash_of(admin)
    assert roles(engine, org_a(engine))[EMAIL] == "owner"


def test_a_bad_role_in_a_change_is_not_sent_to_the_engine():
    engine, owner, _ = two_people("viewer")
    owner.post(f"/miembros/{pid_of(engine, OTHER)}/rol", data={"role": "emperor"})
    assert "Elige un rol" in flash_of(owner)
    assert roles(engine, org_a(engine))[OTHER] == "viewer"


def test_an_owner_removes_a_member():
    engine, owner, _ = two_people("viewer")
    r = owner.post(f"/miembros/{pid_of(engine, OTHER)}/quitar")
    assert r.status_code == 303 and OTHER not in roles(engine, org_a(engine))
    assert "Persona quitada de Demo 1" in flash_of(owner)
    assert member(owner.get("/miembros").text, OTHER) is None


def test_removing_yourself_is_refused_and_an_admin_cannot_remove_an_owner():
    engine, owner, admin = two_people("admin")
    owner.post(f"/miembros/{pid_of(engine, EMAIL)}/quitar")
    assert "No puedes cambiar tu propio rol ni quitarte a ti mismo" in flash_of(owner)
    admin.post(f"/miembros/{pid_of(engine, EMAIL)}/quitar")
    assert "No tienes permiso para hacer ese cambio" in flash_of(admin)
    assert set(roles(engine, org_a(engine))) == {EMAIL, OTHER}


def test_acting_on_someone_who_is_gone_says_so():
    engine, owner, _ = two_people("viewer")
    owner.post("/miembros/per_nobody/quitar")
    assert "Esa persona ya no está en la organización" in flash_of(owner)


def test_cancelling_an_invitation():
    engine, owner, _ = two_people("viewer")
    owner.post("/miembros/invitar", data={"email": "marta@example.com", "role": "viewer"})
    inv = engine.state.orgs[org_a(engine)].pending()[0]
    r = owner.post(f"/invitaciones/{inv['id']}/cancelar")
    assert r.status_code == 303 and engine.state.orgs[org_a(engine)].pending() == []
    assert "Invitación cancelada" in flash_of(owner)
    assert invitation(owner.get("/miembros").text, "marta@example.com") is None
    assert panel_of(engine, email="marta@example.com", sub="g-m").get("/", follow_redirects=False).status_code == 303


def test_resending_renews_an_expired_invitation_with_the_same_email_and_role():
    engine, owner, _ = two_people("viewer")
    engine.state.orgs[org_a(engine)].invite("pedro@example.com", "viewer", PAST)
    old = engine.state.orgs[org_a(engine)].pending()[0]
    r = owner.post(f"/invitaciones/{old['id']}/reenviar")
    assert r.status_code == 303
    [now] = engine.state.orgs[org_a(engine)].pending()
    assert now["id"] != old["id"] and now["email"] == "pedro@example.com" and now["role"] == "viewer"
    assert "Invitación reenviada a pedro@example.com" in flash_of(owner)
    assert "vence en 14 días" in text(invitation(owner.get("/miembros").text, "pedro@example.com"))


def test_resending_or_cancelling_an_unknown_invitation_says_so():
    engine, owner, _ = two_people("viewer")
    owner.post("/invitaciones/inv_nope/cancelar")
    assert "Esa invitación ya no existe" in flash_of(owner)
    owner.post("/invitaciones/inv_nope/reenviar")
    assert "Esa invitación ya no existe" in flash_of(owner)


# --- security -----------------------------------------------------------------------------------------------------


WRITES = [("/miembros/invitar", {"email": "a@example.com", "role": "viewer"}), ("/miembros/per_x/rol", {"role": "admin"}),
          ("/miembros/per_x/quitar", {}), ("/invitaciones/inv_x/cancelar", {}), ("/invitaciones/inv_x/reenviar", {})]


@pytest.mark.parametrize("path,data", WRITES)
def test_every_write_needs_the_csrf_token(path, data):
    engine, owner, _ = two_people("viewer")
    assert owner.post(path, data=data, csrf=False).status_code == 403
    assert owner.post(path, data=data, headers={"Origin": "http://evil.example"}).status_code == 403
    assert len(engine.state.orgs[org_a(engine)].pending()) == 0


@pytest.mark.parametrize("path,data", WRITES)
def test_writes_need_a_session(path, data):
    engine = new_engine()
    anonymous = panel_of(engine, login=False)
    assert anonymous.post(path, data=data, csrf=False).status_code == 401
    assert anonymous.get("/miembros").headers["location"] == "/login"


def test_the_org_comes_from_the_session_not_from_the_form():
    engine = new_engine()
    panel = panel_of(engine)  # owner of both orgs; the open project is in org A
    panel.post("/miembros/invitar", data={"email": "z@example.com", "role": "viewer", "org": org_b(engine),
                                          "org_id": org_b(engine)})
    assert [i["email"] for i in engine.state.orgs[org_a(engine)].pending()] == ["z@example.com"]
    assert engine.state.orgs[org_b(engine)].pending() == []


def test_the_session_never_holds_a_role_or_the_members():
    engine, owner, _ = two_people("viewer")
    owner.get("/miembros")
    owner.post("/miembros/invitar", data={"email": "a@example.com", "role": "viewer"})
    assert set(session_of(owner)) <= {"person", "email", "name", "csrf", "org", "project", "flash", "oauth_state"}


def test_a_page_without_an_open_project_goes_to_the_selector():
    engine = new_engine(owners=())
    panel = panel_of(engine)
    assert panel.get("/miembros").headers["location"] in ("/sin-proyectos", "/proyectos")


def test_an_unreachable_engine_gets_a_message_not_a_500():
    def down(request):
        raise httpx.ConnectError("down")

    panel = make_panel(stub_client(down))
    r = panel.get("/miembros")
    assert r.status_code in (200, 502) and "No se pudo conectar con el motor" in r.text
    assert panel.post("/miembros/invitar", data={"email": "a@example.com", "role": "viewer"}).status_code in (200, 303, 502)
