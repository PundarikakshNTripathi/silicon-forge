# SPDX-License-Identifier: AGPL-3.0-or-later
"""Principal RTL Coder Agent: generates synthesizable SystemVerilog & performs COI-sliced repairs."""
from __future__ import annotations

import json
from dataclasses import asdict

from services.orchestrator.agents.base import BaseHardwareAgent
from services.orchestrator.debug import AttemptMemory, build_repair_context
from services.orchestrator.guardrails.rtl_slicer import RtlDependencySlicer
from services.orchestrator.state import DesignContract


class RtlCoderAgent(BaseHardwareAgent):
    prompt_file = "system_rtl_engineer.md"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.slicer = RtlDependencySlicer()

    def guidance(self, query: str, top_k: int = 2) -> str:
        """Knowledge-pack sections relevant to `query`, or an empty string (no registry, or nothing relevant enough)."""
        if self.skills is None:
            return ""
        ctx = self.skills.format_context(query, top_k=top_k)
        return f"Relevant guidance (follow it where it applies):\n{ctx}\n\n" if ctx else ""

    def generate_rtl(self, contract: DesignContract, spec_text: str) -> str:
        notes = self.guidance(f"synthesizable systemverilog {spec_text}", top_k=2)
        user_prompt = (
            f"DesignContract JSON:\n{json.dumps(asdict(contract), indent=2)}\n\n"
            f"Original Specification:\n{spec_text}\n\n"
            + notes
            + "Write complete, tapeout-ready, synthesizable SystemVerilog matching the contract."
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
        witness: object | None = None,
        memory: AttemptMemory | None = None,
        temperature: float = 0.05,
    ) -> str:
        coi_slice = (
            self.slicer.extract_coi_slice(current_rtl, failing_signal)
            if failing_signal
            else current_rtl
        )
        evidence = build_repair_context(current_rtl, diagnostic, failing_signal, witness, memory, self.slicer)
        notes = self.guidance(diagnostic, top_k=2)
        user_prompt = (
            f"REPAIR REQUEST (Attempt {retry_attempt})\n"
            f"Module: {contract.module_name}\n"
            f"Diagnostic / Counterexample:\n{diagnostic}\n\n"
            + (f"Evidence:\n{evidence}\n\n" if evidence else "")
            + notes
            + f"Localized Cone-of-Influence RTL Slice:\n{coi_slice}\n\n"
            f"Full Current RTL:\n{current_rtl}\n\n"
            "Apply a minimal, surgical fix preserving all ports and passing logic."
        )
        resp = self.router.complete(
            stage="RTL_REPAIR",
            task_type="counterexample_repair",
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            retry_attempt=retry_attempt,
            bypass_cache=True,
        )
        return self.extract_code(resp.content)
