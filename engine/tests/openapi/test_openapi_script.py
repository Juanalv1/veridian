import importlib.util
from pathlib import Path

import yaml

from veridian_engine.openapi import RouteClass
from veridian_engine.route_classes import RouteClasses

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "classify_openapi.py"


def load_script():
    spec = importlib.util.spec_from_file_location("classify_openapi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_is_a_routes_file_the_engine_loads(tmp_path):
    script = load_script()
    text = script.render({
        "PUT /payouts/bank-account": RouteClass("critical", "redirects payouts\n# tricky"),
        "GET /invoices/{id}": RouteClass("normal", ""),
    })
    assert yaml.safe_load(text) == {
        "routes": {
            "PUT /payouts/bank-account": "critical",
            "GET /invoices/{id}": "normal",
        }
    }
    assert "# redirects payouts # tricky" in text
    assert text.startswith("# Generated")
    routes_file = tmp_path / "routes.yaml"
    routes_file.write_text(text, encoding="utf-8")
    assert RouteClasses.load(str(routes_file)).classify("PUT", "/payouts/bank-account") == "critical"
