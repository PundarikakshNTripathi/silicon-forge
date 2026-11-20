"""Principal Formal Verification Agent: synthesizes TypedPropertyIR for SymbiYosys."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from services.orchestrator.agents.base import BaseHardwareAgent
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
        resp = self.router.complete(
            stage="FORMAL_VERIFY",
            task_type="formal_ir_synthesis",
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            temperature=0.05,
        )
        props = self.extract_json(resp.content)
        if not isinstance(props, list):
            raise ValueError("FormalVerifierAgent expected a JSON list of TypedPropertyIR objects.")
        return props
