"""Panel: the project brand and "Desbloqueo por correo" settings (docs/DESBLOQUEO-POR-CORREO.md).

Admins and owners edit; a viewer reads. Over the real fake engine, so what the panel sends is what it accepts."""

import re

from tests.test_panel_accounts import member_panel, new_engine, org_a, panel_of, project_a, side, text
from tests.test_panel_audit import nav_links

FIELDS = {"brand_name": "Acme", "brand_logo_url": "https://cdn.acme.com/logo.png", "brand_color": "#1A2B3C",
          "brand_support_url": "https://acme.com/help", "brand_support_email": "help@acme.com",
          "self_unlock_enabled": "on"}


def project(engine):
    return engine.state.projects[project_a(engine)]


def flash_of(panel, path="/marca"):
    return text(re.search(r'<div class="note flash[^"]*"[^>]*>(.*?)</div>', panel.get(path).text, re.S).group(1))


def test_the_sidebar_offers_the_page_to_every_role():
    engine = new_engine()
    assert "/marca" in nav_links(panel_of(engine).get("/").text)
    assert "/marca" in nav_links(member_panel(engine, org_a(engine), "viewer").get("/").text)


def test_an_owner_sees_the_empty_form_with_the_feature_off():
    r = panel_of(new_engine()).get("/marca")
    assert r.status_code == 200
    page = text(r.text)
    assert "Marca y desbloqueo por correo" in page and "Desbloqueo por correo" in page
    for name in FIELDS:
        assert f'name="{name}"' in r.text
    assert re.search(r'<input[^>]*type="checkbox"[^>]*name="self_unlock_enabled"(?![^>]*checked)', r.text)
    assert "Guardar" in page and r.text.count("btn-primary") == 1


def test_saving_round_trips_every_field_and_the_toggle():
    engine = new_engine()
    panel = panel_of(engine)
    r = panel.post("/marca", data=FIELDS)
    assert r.status_code == 303 and r.headers["location"] == "/marca"
    p = project(engine)
    assert (p.brand_name, p.brand_logo_url, p.brand_color) == ("Acme", FIELDS["brand_logo_url"], "#1A2B3C")
    assert (p.brand_support_url, p.brand_support_email, p.self_unlock_enabled) == (FIELDS["brand_support_url"],
                                                                                    "help@acme.com", True)
    assert "Marca guardada" in flash_of(panel)
    html = panel.get("/marca").text
    for value in ("Acme", FIELDS["brand_logo_url"], "#1A2B3C", "help@acme.com"):
        assert f'value="{value}"' in html
    assert re.search(r'<input[^>]*name="self_unlock_enabled"[^>]*checked', html)


def test_blank_fields_clear_the_brand_and_an_unchecked_toggle_turns_it_off():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/marca", data=FIELDS)
    panel.post("/marca", data={k: "" for k in FIELDS if k != "self_unlock_enabled"})
    p = project(engine)
    assert p.brand_name is None and p.brand_color is None and p.brand_logo_url is None and p.self_unlock_enabled is False


def test_an_unsafe_logo_url_is_explained_and_nothing_is_saved():
    engine = new_engine()
    panel = panel_of(engine)
    for url in ("http://cdn.acme.com/l.png", "https://localhost/l.png", "https://10.0.0.5/l.png"):
        r = panel.post("/marca", data={**FIELDS, "brand_logo_url": url})
        assert r.status_code == 422, url
        assert "La URL del logo tiene que ser https y pública." in text(r.text)
        assert f'value="{url}"' in r.text  # what they typed stays in the form
    assert project(engine).brand_name is None


def test_a_bad_color_name_or_email_is_refused_with_its_own_message():
    engine = new_engine()
    panel = panel_of(engine)
    r = panel.post("/marca", data={**FIELDS, "brand_color": "azul"})
    assert r.status_code == 422 and "El color tiene que ser un código como #2563EB." in text(r.text)
    r = panel.post("/marca", data={**FIELDS, "brand_name": "x" * 81})
    assert r.status_code == 422 and "El nombre puede tener hasta 80 caracteres." in text(r.text)
    r = panel.post("/marca", data={**FIELDS, "brand_support_email": "nope"})
    assert r.status_code == 422 and "Escribe un correo de soporte válido." in text(r.text)
    r = panel.post("/marca", data={**FIELDS, "brand_support_url": "http://acme.com"})
    assert r.status_code == 422 and "La URL de soporte tiene que ser https." in text(r.text)
    assert project(engine).brand_name is None


def test_values_are_escaped_in_the_form():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/marca", data={**FIELDS, "brand_name": '"><script>alert(1)</script>'})
    html = panel.get("/marca").text
    assert "<script>alert(1)</script>" not in html


def test_a_viewer_reads_the_brand_but_cannot_edit_it():
    engine = new_engine()
    owner = panel_of(engine)
    owner.post("/marca", data=FIELDS)
    viewer = member_panel(engine, org_a(engine), "viewer")
    r = viewer.get("/marca")
    assert r.status_code == 200
    page = text(r.text)
    assert "Acme" in page and "help@acme.com" in page and "Solo un admin u owner puede cambiar esto." in page
    assert "<form" not in re.search(r'<main id="live".*</main>', r.text, re.S).group(0)
    assert 'name="brand_name"' not in r.text
    r = viewer.post("/marca", data={**FIELDS, "brand_name": "Hacked"})
    assert r.status_code == 303
    assert project(engine).brand_name == "Acme"
    assert "Tu rol no permite" in flash_of(viewer)


def test_the_form_needs_the_csrf_token():
    engine = new_engine()
    panel = panel_of(engine)
    assert panel.post("/marca", data=FIELDS, csrf=False).status_code == 403
    assert project(engine).brand_name is None


def test_a_brand_change_shows_up_in_the_audit_page():
    engine = new_engine()
    panel = panel_of(engine)
    panel.post("/marca", data=FIELDS)
    assert "Ana cambió la marca o el desbloqueo por correo" in text(panel.get("/auditoria").text)
