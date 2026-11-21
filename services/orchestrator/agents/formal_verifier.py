# SPDX-License-Identifier: AGPL-3.0-or-later
"""Principal Formal Verification Agent: synthesizes schema-validated TypedPropertyIR for SymbiYosys."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from services.orchestrator.agents.base import BaseHardwareAgent
from services.orchestrator.eda.formal_ir import property_signals
from services.orchestrator.schemas import PROPERTY_LIST, property_list_json_schema
from services.orchestrator.state import DesignContract

EXPRESSION_RULES = (
    "Expressions (expression, condition, expr_a, expr_b, trigger, response) are plain Verilog expressions over the "
    "contract ports only: no SVA ('cover', 'assert', 'assume', 'property', 'posedge', '|->', '##', '$past', '$rose'), "
    "no helper or history signals ('f_past_valid' and the like). The compiler adds clocking, reset handling and "
    "history itself. To refer to a previous value use a LATENCY_BOUND property instead of writing $past."
)


class FormalVerifierAgent(BaseHardwareAgent):
    prompt_file = "system_formal_verifier.md"

    def synthesize_properties_ir(self, contract: DesignContract, spec_text: str) -> list[dict[str, Any]]:
        skill_ctx = self.skills.format_context("SymbiYosys formal SVA anti-vacuity cover", top_k=1)
        ports = sorted(contract.port_names())
        user_prompt = (
            f"Formal Verification Skill Context:\n{skill_ctx}\n\n"
            f"DesignContract JSON:\n{json.dumps(asdict(contract), indent=2)}\n\n"
            f"Specification:\n{spec_text}\n\n"
            f"Signals you may use, and no others: {', '.join(ports)}.\n{EXPRESSION_RULES}\n\n"
            "Emit a JSON array of TypedPropertyIR objects including at least one COVER_REACHABILITY property."
        )

        def parse(raw: Any) -> list[dict[str, Any]]:
            if not isinstance(raw, list):
                raise ValueError("expected a JSON array of TypedPropertyIR objects")
            props = [p.model_dump() for p in PROPERTY_LIST.validate_python(raw)]
            # Grounding is checked here, inside the re-ask loop, so the reply to a bad property set names the offending
            # identifiers and the allowed ones, and the retry escalates the model tier like any other invalid reply.
            known = set(ports)
            bad: dict[str, list[str]] = {}
            for p in props:
                for sig in sorted(property_signals(p) - known):
                    bad.setdefault(p.get("id", "?"), []).append(sig)
            if bad:
                detail = "; ".join(f"property {pid} uses {', '.join(sorted(sigs))}" for pid, sigs in bad.items())
                raise ValueError(f"unknown signals: {detail}. Allowed signals: {', '.join(ports)}. {EXPRESSION_RULES}")
            return props

        return self.complete_structured(
            stage="FORMAL_VERIFY",
            task_type="formal_ir_synthesis",
            user_prompt=user_prompt,
            parse=parse,
            json_schema=property_list_json_schema(ports),
        )
