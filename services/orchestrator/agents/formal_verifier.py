"""Principal Formal Verification Agent: synthesizes schema-validated TypedPropertyIR for SymbiYosys."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from services.orchestrator.agents.base import BaseHardwareAgent
from services.orchestrator.schemas import PROPERTY_LIST, property_list_json_schema
from services.orchestrator.state import DesignContract


class FormalVerifierAgent(BaseHardwareAgent):
    prompt_file = "system_formal_verifier.md"

    def synthesize_properties_ir(self, contract: DesignContract, spec_text: str) -> list[dict[str, Any]]:
        skill_ctx = self.skills.format_context("SymbiYosys formal SVA anti-vacuity cover", top_k=1)
        user_prompt = (
            f"Formal Verification Skill Context:\n{skill_ctx}\n\n"
            f"DesignContract JSON:\n{json.dumps(asdict(contract), indent=2)}\n\n"
            f"Specification:\n{spec_text}\n\n"
            "Emit a JSON array of TypedPropertyIR objects including at least one COVER_REACHABILITY property."
        )

        def parse(raw: Any) -> list[dict[str, Any]]:
            if not isinstance(raw, list):
                raise ValueError("expected a JSON array of TypedPropertyIR objects")
            return [p.model_dump() for p in PROPERTY_LIST.validate_python(raw)]

        return self.complete_structured(
            stage="FORMAL_VERIFY",
            task_type="formal_ir_synthesis",
            user_prompt=user_prompt,
            parse=parse,
            json_schema=property_list_json_schema(),
        )
