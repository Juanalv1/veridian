"""Helpers for test_agent_prompt: pull the prompt out of a page, and get the one-time key reveal page."""

import html
import re

from tests.test_panel_accounts import new_engine, panel_of
from tests.test_panel_keys import KEY, create


def prompt_in(page):
    m = re.search(r'<button[^>]*data-agent-prompt[^>]*>', page)
    assert m, "no agent prompt button"
    attr = re.search(r'data-copy="([^"]*)"', m.group(0))
    assert attr, "the button has no data-copy"
    return html.unescape(attr.group(1))


def reveal_page():
    r, key = create(panel_of(new_engine()))
    assert KEY.fullmatch(key)
    return r.text, key
