# SPDX-License-Identifier: AGPL-3.0-or-later
"""Formal property IR: schema enums over the contract's ports, and grounding inside the agent's re-ask loop."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.orchestrator.agents.base import StructuredOutputError
from services.orchestrator.agents.formal_verifier import FormalVerifierAgent
from services.orchestrator.schemas import ContractModel, property_list_json_schema
from services.orchestrator.skills.registry import SkillRegistry
from tests.conftest import EXAMPLES

PROMPTS = Path(__file__).resolve().parents[1] / "configs" / "prompts"
PORTS = ["clk", "rst_n", "en_i", "count_o"]


def _contract():
    raw = json.loads((EXAMPLES / "up_down_counter" / "fixtures" / "contract.json").read_text(encoding="utf-8"))
    return ContractModel(**raw).to_state()


def _enum_fields(schema):
    found = {}

    def walk(n):
        if isinstance(n, dict):
            for name, spec in (n.get("properties") or {}).items():
                if name in ("signal", "valid_signal", "ready_signal", "data_signal"):
                    found.setdefault(name, []).append(spec)
                if name == "signals":
                    found.setdefault("signals", []).append(spec.get("items"))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(schema)
    return found


def test_signal_fields_become_enums_of_the_contract_ports():
    found = _enum_fields(property_list_json_schema(PORTS))
    assert {"signal", "valid_signal", "ready_signal", "data_signal", "signals"} <= set(found)
    for specs in found.values():
        assert all(s == {"type": "string", "enum": sorted(PORTS)} for s in specs)


def test_without_ports_the_schema_is_unchanged_and_not_mutated():
    plain = property_list_json_schema()
    constrained = property_list_json_schema(PORTS)
    assert plain != constrained and "enum" not in json.dumps(_enum_fields(plain))
    assert property_list_json_schema() == plain  # building a constrained schema did not touch the shared one


class _Router:
    def __init__(self, replies):
        self.replies, self.prompts, self.schemas = list(replies), [], []

    def complete(self, **kw):
        self.prompts.append(kw["user_prompt"])
        self.schemas.append(kw.get("json_schema"))
        return SimpleNamespace(content=json.dumps(self.replies.pop(0)))


def _agent(replies):
    return FormalVerifierAgent(_Router(replies), SkillRegistry(), PROMPTS)


GOOD = [
    {"kind": "RESET_VALUE", "id": "P_RESET", "signal": "count_o", "expected": "8'd0"},
    {"kind": "COVER_REACHABILITY", "id": "C_RUN", "expression": "en_i && count_o == 8'd3"},
]
SVA_LEAK = [
    {"kind": "RESET_VALUE", "id": "P_RESET", "signal": "count_o", "expected": "8'd0"},
    {"kind": "COVER_REACHABILITY", "id": "C_RUN", "expression": "cover property (@(posedge clk) f_past_valid && en_i)"},
]


def test_a_reply_that_leaks_sva_is_rejected_with_the_allowed_signals_and_then_corrected():
    contract = _contract()
    agent = _agent([SVA_LEAK, GOOD])
    props = agent.synthesize_properties_ir(contract, "spec")
    assert [p["id"] for p in props] == ["P_RESET", "C_RUN"]
    second = agent.router.prompts[1]
    assert "previous reply was rejected" in second and "unknown signals" in second
    assert "f_past_valid" in second and "posedge" in second
    assert all(p in second for p in sorted(contract.port_names()))  # lists what is allowed
    assert agent.router.schemas[0]["$defs"]  # the schema went out with the call


def test_the_first_prompt_already_names_the_allowed_signals_and_the_expression_rules():
    contract = _contract()
    agent = _agent([GOOD])
    agent.synthesize_properties_ir(contract, "spec")
    first = agent.router.prompts[0]
    assert "Signals you may use, and no others" in first and "no SVA" in first
    assert all(p in first for p in contract.port_names())


def test_persistent_leakage_ends_in_a_structured_output_error_not_a_silent_pass():
    agent = _agent([SVA_LEAK, SVA_LEAK, SVA_LEAK])
    with pytest.raises(StructuredOutputError, match="unknown signals"):
        agent.synthesize_properties_ir(_contract(), "spec")
