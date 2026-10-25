"""The SDK snippet component: one macro shared by the keys page and the signup flow, with
Python / Node tabs, highlighting, copy buttons and the new key already in the environment line."""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "edge" / "panel" / "templates"


def render(key=None):
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
    macro = env.from_string('{% from "partials/snippet.html" import sdk_snippet %}{{ sdk_snippet(key) }}')
    return macro.render(key=key)


def tag(html, element_id):
    m = re.search(r'<[^>]*\bid="%s"[^>]*>' % re.escape(element_id), html)
    assert m, element_id
    return m.group(0)


def test_the_new_key_is_in_the_environment_line_and_its_copy_button():
    html = render("vrd_abc12345_secret")
    assert '<code id="snippet-env">VERIDIAN_API_KEY=vrd_abc12345_secret</code>' in html
    assert 'data-copy-from="snippet-env"' in html


def test_without_a_key_the_environment_line_shows_a_placeholder():
    assert '<code id="snippet-env">VERIDIAN_API_KEY=vrd_...</code>' in render()


def test_the_key_is_escaped():
    html = render("<i>x</i>")
    assert "<i>x" not in html and "&lt;i&gt;x" in html


def test_python_and_node_tabs_follow_the_aria_tabs_pattern():
    html = render("k")
    py, node = tag(html, "snippet-tab-python"), tag(html, "snippet-tab-node")
    assert 'role="tablist"' in html
    assert 'role="tab"' in py and 'aria-selected="true"' in py and 'aria-controls="snippet-panel-python"' in py
    assert 'role="tab"' in node and 'aria-selected="false"' in node and 'tabindex="-1"' in node
    assert 'role="tabpanel"' in tag(html, "snippet-panel-python")
    assert "hidden" in tag(html, "snippet-panel-node")
    assert "hidden" not in tag(html, "snippet-panel-python")


def test_the_python_tab_has_install_and_highlighted_code_with_copy_buttons():
    html = render("k")
    assert 'data-copy="pip install veridian-sdk"' in html
    assert 'data-copy-from="snippet-python"' in html
    assert '<span class="k">from</span>' in html


def test_the_node_tab_has_install_and_highlighted_code_with_copy_buttons():
    html = render("k")
    assert 'data-copy="npm install veridian-sdk"' in html
    assert 'data-copy-from="snippet-node"' in html
    assert "en preparación" not in html
    assert not re.search(r'id="snippet-node"[^>]*>[^<]*vrd_', html)
