# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed Property IR -> SymbiYosys SVA & .sby compiler + counterexample witness parser.

The LLM never writes raw SVA. It emits structured `TypedPropertyIR` objects (see `schemas.py`) and this
deterministic compiler lowers them to yosys-compatible immediate assertions framed by `f_past_valid`.
"""
from __future__ import annotations

import re
from typing import Any

from services.orchestrator.state import DesignContract, FormalWitness

IDENT_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")
SYSFUNC_RE = re.compile(r"\$[a-zA-Z_]\w*")  # $past, $stable, ...
SV_LITERAL_RE = re.compile(r"\b\d*'[sS]?[bBdDhHoO][0-9a-fA-FxXzZ_]+\b")

SUPPORTED_KINDS = (
    "RESET_VALUE",
    "INVARIANT",
    "HANDSHAKE_STABILITY",
    "MUTUAL_EXCLUSION",
    "COMBINATIONAL_EQUIVALENCE",
    "LATENCY_BOUND",
    "COVER_REACHABILITY",
)


def referenced_signals(expression: str) -> set[str]:
    """Identifiers referenced by an SV expression (sized literals and operators removed)."""
    cleaned = SYSFUNC_RE.sub(" ", SV_LITERAL_RE.sub(" ", expression))
    return set(IDENT_RE.findall(cleaned))


def property_signals(prop: dict[str, Any]) -> set[str]:
    """All design signals a typed property refers to."""
    sigs: set[str] = set()
    for key in ("signal", "valid_signal", "ready_signal", "data_signal", "trigger", "response"):
        if prop.get(key):
            sigs |= referenced_signals(str(prop[key]))
    for key in ("expression", "condition", "expr_a", "expr_b"):
        if prop.get(key):
            sigs |= referenced_signals(str(prop[key]))
    for s in prop.get("signals", []) or []:
        sigs |= referenced_signals(str(s))
    return sigs


class FormalIRCompiler:
    """Deterministically compiles TypedPropertyIR JSON into SymbiYosys-valid SVA."""

    def compile_sva_block(self, contract: DesignContract, properties_ir: list[dict[str, Any]]) -> str:
        clk = contract.clock_port
        rst = contract.reset_port
        rst_cond = f"!{rst}" if contract.reset_active_low else rst
        not_rst = rst if contract.reset_active_low else f"!{rst}"

        decls: list[str] = []
        body: list[str] = []

        for prop in properties_ir:
            pid = re.sub(r"[^a-zA-Z0-9_]", "_", prop.get("id", "PROP"))
            kind = prop.get("kind", "")
            desc = prop.get("description", "")
            body.append(f"        // {pid}: {desc}")
            if kind == "RESET_VALUE":
                sig, exp = prop["signal"], prop["expected"]
                body.append(f"        if (f_past_valid && $past({rst_cond})) begin")
                body.append(f"            {pid}: assert({sig} == {exp});")
                body.append("        end")
            elif kind == "INVARIANT":
                cond = prop.get("condition", "1'b1")
                body.append(f"        if (f_past_valid && {not_rst} && ({cond})) begin")
                body.append(f"            {pid}: assert({prop['expression']});")
                body.append("        end")
            elif kind == "HANDSHAKE_STABILITY":
                v, r, d = prop["valid_signal"], prop["ready_signal"], prop["data_signal"]
                body.append(f"        if (f_past_valid && {not_rst} && $past({v} && !{r})) begin")
                body.append(f"            {pid}_v: assert({v});")
                body.append(f"            {pid}_d: assert($stable({d}));")
                body.append("        end")
            elif kind == "MUTUAL_EXCLUSION":
                sigs = [str(s) for s in prop["signals"]]
                pairs = [f"!(({a}) && ({b}))" for i, a in enumerate(sigs) for b in sigs[i + 1:]]
                body.append(f"        if (f_past_valid && {not_rst}) begin")
                body.append(f"            {pid}: assert({' && '.join(pairs)});")
                body.append("        end")
            elif kind == "COMBINATIONAL_EQUIVALENCE":
                cond = prop.get("condition", "1'b1")
                body.append(f"        if (f_past_valid && {not_rst} && ({cond})) begin")
                body.append(f"            {pid}: assert(({prop['expr_a']}) == ({prop['expr_b']}));")
                body.append("        end")
            elif kind == "LATENCY_BOUND":
                trig, resp, bound = prop["trigger"], prop["response"], int(prop["bound"])
                width = max(2, bound.bit_length() + 1)
                cnt = f"f_wait_{pid}"
                decls.append(f"    reg [{width - 1}:0] {cnt};")
                decls.append(f"    initial {cnt} = {width}'d0;")
                body.append(f"        if ({rst_cond} || ({resp})) {cnt} <= {width}'d0;")
                body.append(f"        else if (({trig}) || {cnt} != {width}'d0) {cnt} <= {cnt} + {width}'d1;")
                body.append(f"        if (f_past_valid && {not_rst}) begin")
                body.append(f"            {pid}: assert({cnt} <= {width}'d{bound});")
                body.append("        end")
            elif kind == "COVER_REACHABILITY":
                body.append(f"        if (f_past_valid && {not_rst}) begin")
                body.append(f"            {pid}: cover({prop['expression']});")
                body.append("        end")
            else:
                raise ValueError(f"Unsupported TypedPropertyIR kind: {kind}")

        lines = [
            "`ifdef FORMAL",
            "    reg f_past_valid;",
            "    initial f_past_valid = 1'b0;",
            f"    always @(posedge {clk}) f_past_valid <= 1'b1;",
            "",
            "    // Reset is asserted on cycle 0 so k-induction starts from a valid state",
            f"    always @(*) if (!f_past_valid) assume({rst_cond});",
            *decls,
            "",
            f"    always @(posedge {clk}) begin",
            *body,
            "    end",
            "`endif",
        ]
        return "\n".join(lines)

    def inject_formal_block(self, rtl_source: str, sva_block: str) -> str:
        idx = rtl_source.rfind("endmodule")
        if idx == -1:
            raise ValueError("Cannot inject formal SVA block: 'endmodule' not found in RTL.")
        return rtl_source[:idx] + "\n" + sva_block + "\n" + rtl_source[idx:]

    def render_sby_config(
        self,
        module_name: str,
        rtl_filename: str,
        depth: int = 20,
        engines: dict[str, str] | None = None,
    ) -> str:
        """Render a .sby with bmc / prove / cover tasks. `engines` maps task -> engine line."""
        eng = {"bmc": "smtbmc z3", "prove": "smtbmc z3", "cover": "smtbmc z3"}
        eng.update(engines or {})
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
            f"bmc: {eng['bmc']}\n"
            f"prove: {eng['prove']}\n"
            f"cover: {eng['cover']}\n\n"
            "[script]\n"
            f"read -formal -sv {rtl_filename}\n"
            f"prep -top {module_name}\n\n"
            "[files]\n"
            f"{rtl_filename}\n"
        )

    # --- Witness parsing -------------------------------------------------------------------

    _REAL_FAIL_RE = re.compile(
        r"summary:\s+failed assertion\s+(?P<mod>[\w$.\\]+?)\.(?P<prop>\w+)\s+at\s+(?P<loc>\S+)"
        r"(?:\s+step\s+(?P<step>\d+))?"
    )
    _LEGACY_FAIL_RE = re.compile(
        r"Assert failed in\s+(?P<mod>[a-zA-Z0-9_]+):.*?(?P<prop>[A-Z][A-Z0-9_]+).*?(?:step|cycle)\s+(?P<step>\d+)",
        re.IGNORECASE,
    )

    def parse_sby_witness(self, sby_log: str) -> list[FormalWitness]:
        """Extract failing properties from a real (or legacy-format) SymbiYosys log."""
        witnesses: list[FormalWitness] = []
        seen: set[tuple[str, int]] = set()
        # Real format: "[axi_lite_alu_prove] summary: failed assertion axi_lite_alu.P_X at f.sv:62.13 step 3"
        for line in sby_log.splitlines():
            task = "bmc"
            m_task = re.search(r"\[\w+?_(bmc|prove|cover)\]", line)
            if m_task:
                task = m_task.group(1)
            m = self._REAL_FAIL_RE.search(line)
            if m:
                prop, step = m.group("prop"), int(m.group("step") or 0)
                if (prop, step) not in seen:
                    seen.add((prop, step))
                    witnesses.append(FormalWitness(
                        property_id=prop, task=task, cycle=step, failing_signal="",
                        summary=f"{task.upper()} counterexample: assertion {prop} failed at step {step} ({m.group('loc')})",
                    ))
        if witnesses:
            return witnesses
        for m in self._LEGACY_FAIL_RE.finditer(sby_log):
            prop = m.group("prop")
            witnesses.append(FormalWitness(
                property_id=prop, task="bmc", cycle=int(m.group("step")),
                failing_signal=prop.lower(), summary=m.group(0),
            ))
        return witnesses

    def resolve_failing_signal(
        self, prop_id: str, properties_ir: list[dict[str, Any]], contract: DesignContract
    ) -> str:
        """Pick the RTL signal to slice on for a failed property (prefer an output port it mentions)."""
        for prop in properties_ir:
            if re.sub(r"[^a-zA-Z0-9_]", "_", prop.get("id", "")) != prop_id:
                continue
            if prop.get("signal") in contract.port_names():
                return str(prop["signal"])
            # Ordered by appearance: the assertion body first, then guard conditions.
            ordered: list[str] = []
            for key in ("expression", "expr_a", "expr_b", "response", "data_signal", "valid_signal"):
                text = SV_LITERAL_RE.sub(" ", str(prop.get(key, "")))
                ordered += [s for s in IDENT_RE.findall(text) if s not in ordered]
            ordered += sorted(property_signals(prop) - set(ordered))
            outputs = {p.name for p in contract.ports if p.direction == "output"}
            for s in ordered:
                if s in outputs:
                    return s
            for s in ordered:
                if s in contract.port_names():
                    return s
        return ""
