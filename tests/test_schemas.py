"""Schema validation of structured LLM output (DesignContract / TypedPropertyIR)."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from services.orchestrator.schemas import PROPERTY_LIST, ContractModel, contract_json_schema
from tests.conftest import EXAMPLES, example_names


@pytest.mark.parametrize("name", example_names())
def test_bundled_fixtures_validate(name):
    fx = EXAMPLES / name / "fixtures"
    contract = ContractModel.model_validate_json((fx / "contract.json").read_text(encoding="utf-8"))
    props = PROPERTY_LIST.validate_python(json.loads((fx / "props.json").read_text(encoding="utf-8")))
    assert contract.module_name == name
    assert any(p.kind == "COVER_REACHABILITY" for p in props)


def test_contract_rejects_missing_clock_port_and_duplicates():
    base = json.loads((EXAMPLES / "uart_tx" / "fixtures" / "contract.json").read_text(encoding="utf-8"))
    bad = {**base, "clock_port": "nope"}
    with pytest.raises(ValidationError, match="not declared"):
        ContractModel.model_validate(bad)
    dup = {**base, "ports": base["ports"] + [base["ports"][0]]}
    with pytest.raises(ValidationError, match="duplicate"):
        ContractModel.model_validate(dup)


def test_unknown_property_kind_and_bad_fields_are_rejected():
    with pytest.raises(ValidationError):
        PROPERTY_LIST.validate_python([{"id": "P", "kind": "MAGIC", "expression": "a"}])
    with pytest.raises(ValidationError):
        PROPERTY_LIST.validate_python(
            [{"id": "P", "kind": "LATENCY_BOUND", "trigger": "a", "response": "b", "bound": 0}]
        )


def test_json_schema_exports_for_constrained_decoding():
    schema = contract_json_schema()
    assert "module_name" in schema["properties"]
