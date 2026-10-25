"""The "copy prompt for your agent" button: same prompt in the panel and the landing, never with a key in it."""

import re
from pathlib import Path

from tests.test_agent_prompt_support import prompt_in, reveal_page
from tests.test_panel_snippet import render

REPO = Path(__file__).resolve().parents[2]
LANDING = (REPO / "landing" / "index.html").read_text()
GUIDE = "https://veridian-platanus.onrender.com/install.md"


def test_the_panel_snippet_has_the_agent_prompt_button():
    page = render()
    assert "Copiar prompt para tu agente" in page
    assert GUIDE in prompt_in(page)


def test_the_prompt_tells_the_agent_not_to_ask_for_the_key():
    prompt = prompt_in(render())
    assert "VERIDIAN_API_KEY" in prompt and "do not ask me to paste" in prompt


def test_the_prompt_has_no_api_key_even_when_the_snippet_shows_a_new_one():
    key = "vrd_abc12345_secretsecretsecretsecretsecretsecret0"
    assert key in render(key)  # the environment line does show it...
    assert key not in prompt_in(render(key))  # ...the prompt does not


def test_the_one_time_reveal_page_has_the_button_and_its_prompt_has_no_key():
    page, key = reveal_page()
    assert "Copiar prompt para tu agente" in page
    assert key in page  # sanity: this is the page that reveals the key
    assert key not in prompt_in(page)
    assert not re.search(r"vrd_[a-z0-9]{8}_", prompt_in(page))


def test_the_landing_prompt_equals_the_panel_prompt():
    assert "Copiar prompt para tu agente" in LANDING
    assert prompt_in(LANDING) == prompt_in(render())


def test_the_landing_prompt_button_reuses_the_copy_class():
    m = re.search(r'<button[^>]*data-agent-prompt[^>]*>', LANDING)
    assert m and 'class="copy' in m.group(0)
