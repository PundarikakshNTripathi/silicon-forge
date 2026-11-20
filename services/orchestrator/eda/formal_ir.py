"""Typed Property IR -> SymbiYosys SVA & .sby Compiler + Counterexample Witness Distiller."""
from __future__ import annotations

import re
from typing import Any

from services.orchestrator.state import DesignContract, FormalWitness


class FormalIRCompiler:
    """Deterministically compiles TypedPropertyIR JSON into SymbiYosys-valid SVA.

    Eliminates LLM temporal syntax hallucinations and enforces f_past_valid reset framing.
    """

    def compile_sva_block(self, contract: DesignContract, properties_ir: list[dict[str, Any]]) -> str:
        clk = contract.clock_port
        rst = contract.reset_port
        rst_cond = f"!{rst}" if contract.reset_active_low else rst
        not_rst = rst if contract.reset_active_low else f"!{rst}"

        lines = [
            "`ifdef FORMAL",
            "    reg f_past_valid;",
            "    initial f_past_valid = 1'b0;",
            f"    always @(posedge {clk}) f_past_valid <= 1'b1;",
            "",
            "    // Assume reset is active on cycle 0 so k-induction starts from a valid state",
            f"    always @(*) if (!f_past_valid) assume({rst_cond});",
            "",
            f"    always @(posedge {clk}) begin",
        ]

        for prop in properties_ir:
            pid = re.sub(r"[^a-zA-Z0-9_]", "_", prop.get("id", "PROP"))
            kind = prop.get("kind", "")
            desc = prop.get("description", "")
            lines.append(f"        // {pid}: {desc}")
            if kind == "RESET_VALUE":
                sig = prop["signal"]
                exp = prop["expected"]
                lines.append(f"        if (f_past_valid && $past({rst_cond})) begin")
                lines.append(f"            {pid}: assert({sig} == {exp});")
                lines.append("        end")
            elif kind == "INVARIANT":
                cond = prop.get("condition", "1'b1")
                expr = prop["expression"]
                lines.append(f"        if (f_past_valid && {not_rst} && ({cond})) begin")
                lines.append(f"            {pid}: assert({expr});")
                lines.append("        end")
            elif kind == "HANDSHAKE_STABILITY":
                valid_sig = prop["valid_signal"]
                ready_sig = prop["ready_signal"]
                data_sig = prop["data_signal"]
                lines.append(f"        if (f_past_valid && {not_rst} && $past({valid_sig} && !{ready_sig})) begin")
                lines.append(f"            {pid}_v: assert({valid_sig});")
                lines.append(f"            {pid}_d: assert($stable({data_sig}));")
                lines.append("        end")
            elif kind == "COVER_REACHABILITY":
                expr = prop["expression"]
                lines.append(f"        if (f_past_valid && {not_rst}) begin")
                lines.append(f"            {pid}: cover({expr});")
                lines.append("        end")
            else:
                raise ValueError(f"Unsupported TypedPropertyIR kind: {kind}")

        lines.extend([
            "    end",
            "`endif",
        ])
        return "\n".join(lines)

    def inject_formal_block(self, rtl_source: str, sva_block: str) -> str:
        idx = rtl_source.rfind("endmodule")
        if idx == -1:
            raise ValueError("Cannot inject formal SVA block: 'endmodule' not found in RTL.")
        return rtl_source[:idx] + "\n" + sva_block + "\n" + rtl_source[idx:]

    def render_sby_config(self, module_name: str, rtl_filename: str, depth: int = 20) -> str:
        return (
            "[tasks]\n"
            "bmc\n"
            "prove\n"
            "cover\n\n"
            "[options]\n"
            f"bmc: mode bmc\nbmc: depth {depth}\n"
            f"prove: mode prove\nprove: depth {depth}\n"
            f"cover: mode cover\ncover: depth {depth}\n\n"
            "[engines]\n"
            "smtbmc z3\n\n"
            "[script]\n"
            f"read -formal -sv {rtl_filename}\n"
            f"prep -top {module_name}\n\n"
            "[files]\n"
            f"{rtl_filename}\n"
        )

    def parse_sby_witness(self, sby_log: str) -> list[FormalWitness]:
        witnesses: list[FormalWitness] = []
        # Match SymbiYosys assertion failure lines
        fail_re = re.compile(
            r"Assert failed in\s+([a-zA-Z0-9_]+):.*?([A-Z0-9_]+).*?(?:step|cycle)\s+(\d+)",
            re.IGNORECASE,
        )
        for m in fail_re.finditer(sby_log):
            witnesses.append(
                FormalWitness(
                    property_id=m.group(2),
                    task="bmc",
                    cycle=int(m.group(3)),
                    failing_signal=m.group(2).lower(),
                    summary=m.group(0),
                )
            )
        return witnesses
