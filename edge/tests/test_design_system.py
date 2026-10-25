"""Design system wiring: tokens copies, stylesheet order, button markup."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "design" / "tokens.css"
COPIES = [ROOT / "edge" / "panel" / "static" / "tokens.css", ROOT / "landing" / "tokens.css"]


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: str(p.relative_to(ROOT)))
def test_tokens_copy_is_identical_to_design_source(copy):
    assert copy.exists(), f"{copy} missing: copy design/tokens.css"
    assert copy.read_bytes() == SOURCE.read_bytes()


def test_landing_loads_tokens_before_styles():
    html = (ROOT / "landing" / "index.html").read_text()
    assert 'href="tokens.css?v=' in html
    assert html.index("tokens.css") < html.index("styles.css")
    assert "family=Archivo" in html and "Urbanist" not in html


def test_landing_buttons_use_design_system_classes():
    html = (ROOT / "landing" / "index.html").read_text()
    assert "btn btn-primary" in html and "btn btn-secondary" in html
