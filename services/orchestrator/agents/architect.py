# SPDX-License-Identifier: AGPL-3.0-or-later
"""Principal Architect Agent: converts a natural-language spec into a schema-validated DesignContract."""
from __future__ import annotations

from services.orchestrator.agents.base import BaseHardwareAgent
from services.orchestrator.schemas import ContractModel, contract_json_schema
from services.orchestrator.state import DesignContract


class ArchitectAgent(BaseHardwareAgent):
    prompt_file = "system_principal_architect.md"

    def synthesize_contract(self, spec_text: str, pdk_name: str) -> DesignContract:
        skill_ctx = self.skills.format_context(f"{pdk_name} physical clock budget {spec_text}", top_k=1)
        # Stable content first (PDK + skill context), volatile spec last: maximizes prompt-prefix cache hits.
        user_prompt = (
            f"Target PDK: {pdk_name}\n\n"
            f"Retrieved PDK Skill Context:\n{skill_ctx}\n\n"
            f"Hardware Specification:\n{spec_text}\n\n"
            "Emit the exact DesignContract JSON."
        )
        model = self.complete_structured(
            stage="ARCHITECT",
            task_type="architecture_contract",
            user_prompt=user_prompt,
            parse=ContractModel.model_validate,
            json_schema=contract_json_schema(),
        )
        return model.to_state()
