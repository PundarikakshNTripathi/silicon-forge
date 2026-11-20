"""Principal Physical Design Agent: OpenROAD/LibreLane timing closure & DRC/LVS advisor."""
from __future__ import annotations

import json
from typing import Any

from services.orchestrator.agents.base import BaseHardwareAgent
from services.orchestrator.schemas import EcoAdvice
from services.orchestrator.state import PPAMetrics


class PhysicalDesignerAgent(BaseHardwareAgent):
    prompt_file = "system_physical_design.md"

    def advise_eco(self, best_ppa: PPAMetrics, pdk_name: str) -> dict[str, Any]:
        skill_ctx = self.skills.format_context(f"OpenROAD Tcl {pdk_name} timing closure DRC", top_k=2)
        corners = ", ".join(f"{c}={v:.2f}" for c, v in sorted(best_ppa.corner_wns.items())) or "n/a"
        user_prompt = (
            f"PDK: {pdk_name}\n\n"
            f"Skill Context:\n{skill_ctx}\n\n"
            f"Current Best PPA ({best_ppa.mode} flow):\n"
            f"WNS={best_ppa.wns_ns}ns, TNS={best_ppa.tns_ns}ns, Area={best_ppa.area_um2}um^2, "
            f"Power={best_ppa.total_power_mw}mW, DRC={best_ppa.drc_violations}, LVS={best_ppa.lvs_clean}\n"
            f"Per-corner setup slack: {corners}\n"
            f"Knobs={json.dumps(best_ppa.knobs)}\n\n"
            'Provide a grounded OpenROAD/LibreLane ECO recommendation as JSON: '
            '{"recommendation": str, "knobs": {name: value}} using only allowlisted flow variables.'
        )
        advice = self.complete_structured(
            stage="PNR_RL_OPT",
            task_type="physical_eco",
            user_prompt=user_prompt,
            parse=EcoAdvice.model_validate,
            temperature=0.1,
        )
        return advice.model_dump()
