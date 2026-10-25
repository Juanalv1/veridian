import importlib.util
from pathlib import Path

import yaml

from veridian_engine.openapi import RouteClass

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "classify_openapi.py"


def load_script():
    spec = importlib.util.spec_from_file_location("classify_openapi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_is_a_plain_yaml_mapping_with_reason_comments():
    script = load_script()
    text = script.render({
        "PUT /payouts/bank-account": RouteClass("critical", "redirects payouts\n# tricky"),
        "GET /invoices/{id}": RouteClass("normal", ""),
    })
    assert yaml.safe_load(text) == {
        "PUT /payouts/bank-account": "critical",
        "GET /invoices/{id}": "normal",
    }
    assert "# redirects payouts # tricky" in text
