from pathlib import Path

import yaml

from veridian_engine.openapi import Operation, extract_operations, load_spec

FIXTURE = Path(__file__).parent / "fixtures" / "demo_app.yaml"


def demo_spec() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def test_extracts_every_operation_with_upper_case_method_and_template():
    keys = [op.key for op in extract_operations(demo_spec())]
    assert keys == [
        "POST /login",
        "GET /invoices",
        "GET /invoices/{id}",
        "GET /invoices/export",
        "PUT /payouts/bank-account",
        "POST /payouts/bank-account",
        "POST /users",
    ]


def test_ignores_non_operation_keys_in_path_items():
    ops = extract_operations(demo_spec())
    methods = {op.method for op in ops}
    assert methods <= {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}
    # `parameters` and `summary` at path-item level are not operations.
    assert len(ops) == 7


def test_keeps_operation_metadata():
    ops = {op.key: op for op in extract_operations(demo_spec())}
    put = ops["PUT /payouts/bank-account"]
    assert isinstance(put, Operation)
    assert put.operation_id == "updateBankAccount"
    assert put.summary == "Change the bank account that receives payouts"
    assert ops["POST /login"].tags == ["auth"]
    assert ops["GET /invoices/{id}"].description.startswith("Returns invoice details")
    assert ops["GET /invoices/export"].tags == []


def test_empty_or_malformed_spec_yields_no_operations():
    assert extract_operations({}) == []
    assert extract_operations({"paths": None}) == []
    assert extract_operations({"paths": {"/x": "not-a-dict"}}) == []
    assert extract_operations({"paths": {"/x": {"get": "not-a-dict"}}}) == []


def test_load_spec_reads_yaml_and_json(tmp_path):
    assert load_spec(FIXTURE)["openapi"] == "3.0.3"
    json_file = tmp_path / "spec.json"
    json_file.write_text('{"openapi": "3.1.0", "paths": {}}', encoding="utf-8")
    assert load_spec(json_file)["openapi"] == "3.1.0"
