# SPDX-License-Identifier: AGPL-3.0-or-later
"""Principal RTL Coder Agent: generates synthesizable SystemVerilog & performs COI-sliced repairs."""
from __future__ import annotations

import json
from dataclasses import asdict

from services.orchestrator.agents.base import BaseHardwareAgent
from services.orchestrator.guardrails.rtl_slicer import RtlDependencySlicer
from services.orchestrator.state import DesignContract


class RtlCoderAgent(BaseHardwareAgent):
    prompt_file = "system_rtl_engineer.md"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.slicer = RtlDependencySlicer()

    def generate_rtl(self, contract: DesignContract, spec_text: str) -> str:
        user_prompt = (
            f"DesignContract JSON:\n{json.dumps(asdict(contract), indent=2)}\n\n"
            f"Original Specification:\n{spec_text}\n\n"
            "Write complete, tapeout-ready, synthesizable SystemVerilog matching the contract."
        )
        resp = self.router.complete(
            stage="RTL_GEN",
            task_type="rtl_generation",
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
        )
        return self.extract_code(resp.content)

    def repair_rtl(
        self,
        contract: DesignContract,
        current_rtl: str,
        diagnostic: str,
        failing_signal: str | None = None,
        retry_attempt: int = 1,
    ) -> str:
        coi_slice = (
            self.slicer.extract_coi_slice(current_rtl, failing_signal)
            if failing_signal
            else current_rtl
        )
        user_prompt = (
            f"REPAIR REQUEST (Attempt {retry_attempt})\n"
            f"Module: {contract.module_name}\n"
            f"Diagnostic / Counterexample:\n{diagnostic}\n\n"
            f"Localized Cone-of-Influence RTL Slice:\n{coi_slice}\n\n"
            f"Full Current RTL:\n{current_rtl}\n\n"
            "Apply a minimal, surgical fix preserving all ports and passing logic."
        )
        resp = self.router.complete(
            stage="RTL_REPAIR",
            task_type="counterexample_repair",
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            temperature=0.05,
            retry_attempt=retry_attempt,
            bypass_cache=True,
        )
        return self.extract_code(resp.content)
