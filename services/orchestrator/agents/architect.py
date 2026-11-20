"""Principal Architect Agent: converts natural-language spec into strict DesignContract JSON."""
from __future__ import annotations

from services.orchestrator.agents.base import BaseHardwareAgent
from services.orchestrator.state import DesignContract, PortSpec


class ArchitectAgent(BaseHardwareAgent):
    prompt_file = "system_principal_architect.md"

    def synthesize_contract(self, spec_text: str, pdk_name: str) -> DesignContract:
        skill_ctx = self.skills.format_context(f"{pdk_name} physical clock budget {spec_text}", top_k=1)
        user_prompt = (
            f"Target PDK: {pdk_name}\n\n"
            f"Retrieved PDK Skill Context:\n{skill_ctx}\n\n"
            f"Hardware Specification:\n{spec_text}\n\n"
            "Emit the exact DesignContract JSON."
        )
        resp = self.router.complete(
            stage="ARCHITECT",
            task_type="architecture_contract",
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            temperature=0.05,
        )
        data = self.extract_json(resp.content)
        ports = [
            PortSpec(
                name=p["name"],
                direction=p["direction"],
                width=int(p["width"]),
                description=p.get("description", ""),
            )
            for p in data["ports"]
        ]
        return DesignContract(
            module_name=data["module_name"],
            clock_port=data["clock_port"],
            reset_port=data["reset_port"],
            reset_active_low=bool(data["reset_active_low"]),
            reset_sync=bool(data["reset_sync"]),
            ports=ports,
            functional_requirements=list(data.get("functional_requirements", [])),
            pipeline_stages=int(data.get("pipeline_stages", 1)),
            target_clock_ns=float(data.get("target_clock_ns", 10.0)),
            max_area_um2=float(data.get("max_area_um2", 25000.0)),
            max_power_mw=float(data.get("max_power_mw", 5.0)),
        )
