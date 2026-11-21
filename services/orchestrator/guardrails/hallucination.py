# SPDX-License-Identifier: AGPL-3.0-or-later
"""Six-gate anti-hallucination guardrail engine for SiliconForge."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from services.orchestrator.config import PdkConfig
from services.orchestrator.eda.formal_ir import property_signals
from services.orchestrator.state import (
    DesignContract,
    FormalReport,
    GuardrailReport,
    GuardrailViolation,
    LecReport,
)

# Commercial EDA commands frequently hallucinated by LLMs into OpenROAD/Yosys Tcl
FORBIDDEN_COMMERCIAL_TCL = {
    "compile_ultra",
    "place_opt_design",
    "opt_design",
    "route_opt",
    "clock_opt",
    "set_dont_touch_network",
    "create_generated_clock_synopsys",
}


class HallucinationGuardrail:
    def __init__(self, pdk_spec: PdkConfig, telemetry_binary: Path | None = None) -> None:
        self.pdk_spec = pdk_spec
        self.telemetry_binary = telemetry_binary

    def audit_rtl(self, rtl_source: str, contract: DesignContract) -> GuardrailReport:
        violations: list[GuardrailViolation] = []

        # Gate 1: Exact module & port contract verification
        violations.extend(self._check_port_contract(rtl_source, contract))

        # Gate 2: Native C++20 static RTL linter (falls back to Python checks if binary not built)
        violations.extend(self._run_cpp_static_lint(rtl_source))

        # Gate 3: PDK stdcell instantiation allowlist
        violations.extend(self._check_pdk_cells(rtl_source))

        passed = not any(v.severity == "ERROR" for v in violations)
        return GuardrailReport(passed=passed, violations=violations)

    def audit_flow_config(self, flow_vars: dict[str, Any], tcl_snippet: str = "") -> GuardrailReport:
        violations: list[GuardrailViolation] = []

        # Gate 4a: LibreLane/OpenROAD variable allowlist
        allowed = set(self.pdk_spec.allowed_flow_vars)
        for key in flow_vars:
            if allowed and key not in allowed:
                violations.append(
                    GuardrailViolation(
                        gate="TCL_FLOW_ALLOWLIST",
                        rule_id="SF-GRD-401",
                        severity="ERROR",
                        message=f"Hallucinated or unsupported flow variable '{key}' for PDK {self.pdk_spec.pdk_name}.",
                    )
                )

        # Gate 4b: Block commercial Cadence/Synopsys Tcl commands hallucinated into OpenROAD
        for cmd in FORBIDDEN_COMMERCIAL_TCL:
            if re.search(rf"\b{re.escape(cmd)}\b", tcl_snippet):
                violations.append(
                    GuardrailViolation(
                        gate="TCL_FLOW_ALLOWLIST",
                        rule_id="SF-GRD-402",
                        severity="ERROR",
                        message=f"Commercial EDA command '{cmd}' is invalid in OpenROAD/Yosys Tcl.",
                    )
                )

        # Gate 4c: PDK physical realism check (e.g. clock period below PDK floor)
        if "CLOCK_PERIOD" in flow_vars:
            clk_ns = float(flow_vars["CLOCK_PERIOD"])
            if clk_ns < self.pdk_spec.min_clock_period_ns:
                violations.append(
                    GuardrailViolation(
                        gate="PDK_PHYSICS",
                        rule_id="SF-GRD-403",
                        severity="ERROR",
                        message=(
                            f"CLOCK_PERIOD={clk_ns}ns violates {self.pdk_spec.pdk_name} "
                            f"physical floor ({self.pdk_spec.min_clock_period_ns}ns)."
                        ),
                    )
                )

        passed = not any(v.severity == "ERROR" for v in violations)
        return GuardrailReport(passed=passed, violations=violations)

    def audit_formal_ir(self, properties_ir: list[dict[str, Any]], contract: DesignContract) -> GuardrailReport:
        violations: list[GuardrailViolation] = []
        valid_ports = contract.port_names()
        has_cover = False
        has_assert = False

        for prop in properties_ir:
            kind = prop.get("kind", "")
            pid = prop.get("id", "UNNAMED")
            if kind == "COVER_REACHABILITY":
                has_cover = True
            else:
                has_assert = True

            # Verify every referenced design signal exists in the DesignContract (incl. inside expressions)
            for sig in sorted(property_signals(prop) - valid_ports):
                violations.append(
                    GuardrailViolation(
                        gate="FORMAL_GROUNDING",
                        rule_id="SF-GRD-501",
                        severity="ERROR",
                        message=f"Property '{pid}' references non-existent port '{sig}' not in DesignContract.",
                    )
                )

        # Gate 5: Anti-vacuity requirement (must include at least one cover reachability witness)
        if has_assert and not has_cover:
            violations.append(
                GuardrailViolation(
                    gate="FORMAL_ANTI_VACUITY",
                    rule_id="SF-GRD-502",
                    severity="ERROR",
                    message="Formal IR has safety assertions but zero COVER_REACHABILITY properties (vacuity risk).",
                )
            )

        passed = not any(v.severity == "ERROR" for v in violations)
        return GuardrailReport(passed=passed, violations=violations)

    def audit_formal_result(self, report: FormalReport) -> GuardrailReport:
        """Gate 5: a proof only counts if it is non-vacuous (cover statements reached, no front-end error)."""
        violations: list[GuardrailViolation] = []
        if report.task_status.get("cover") == "ERROR":
            violations.append(GuardrailViolation(
                "FORMAL_ANTI_VACUITY", "SF-GRD-504", "ERROR",
                "Formal front-end error: properties could not be elaborated, so nothing was proven."))
        elif report.vacuous_detected:
            violations.append(GuardrailViolation(
                "FORMAL_ANTI_VACUITY", "SF-GRD-503", "ERROR",
                "Cover reachability failed: assumptions over-constrain the design or the trigger state is "
                "unreachable, so the assertions may hold vacuously."))
        return GuardrailReport(passed=not violations, violations=violations)

    def audit_lec(self, lec: LecReport) -> GuardrailReport:
        """Gate 6: post-synthesis logical equivalence between RTL and the synthesized netlist."""
        if lec.passed:
            return GuardrailReport(passed=True)
        return GuardrailReport(passed=False, violations=[GuardrailViolation(
            "LEC", "SF-GRD-601", "ERROR",
            "Logical equivalence check failed: the synthesized netlist is not equivalent to the RTL.")])

    def _check_port_contract(self, rtl_source: str, contract: DesignContract) -> list[GuardrailViolation]:
        violations: list[GuardrailViolation] = []
        mod_match = re.search(rf"\bmodule\s+{re.escape(contract.module_name)}\b", rtl_source)
        if not mod_match:
            violations.append(
                GuardrailViolation(
                    gate="PORT_CONTRACT",
                    rule_id="SF-GRD-101",
                    severity="ERROR",
                    message=f"Expected module '{contract.module_name}' not found in generated RTL.",
                )
            )
            return violations

        for port in contract.ports:
            # Check port direction + name presence in module header
            pattern = rf"\b{port.direction}\b[^;\n,]*\b{re.escape(port.name)}\b"
            if not re.search(pattern, rtl_source):
                violations.append(
                    GuardrailViolation(
                        gate="PORT_CONTRACT",
                        rule_id="SF-GRD-102",
                        severity="ERROR",
                        message=f"Contract port '{port.direction} {port.name}' (width={port.width}) missing or mismatched.",
                    )
                )
                continue
            # Width check for numeric ranges, e.g. `input logic [31:0] a_i`
            decl = re.search(
                rf"\b{port.direction}\b\s+(?:(?:logic|wire|reg)\s+)?(?:signed\s+)?"
                rf"(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?{re.escape(port.name)}\b",
                rtl_source,
            )
            if decl:
                hi, lo = decl.group(1), decl.group(2)
                actual = abs(int(hi) - int(lo)) + 1 if hi is not None else 1
                if actual != port.width:
                    violations.append(
                        GuardrailViolation(
                            gate="PORT_CONTRACT",
                            rule_id="SF-GRD-103",
                            severity="ERROR",
                            message=(
                                f"Port '{port.name}' is {actual} bit(s) wide in the RTL but the "
                                f"DesignContract requires {port.width}."
                            ),
                        )
                    )
        return violations

    def _run_cpp_static_lint(self, rtl_source: str) -> list[GuardrailViolation]:
        if self.telemetry_binary and self.telemetry_binary.exists():
            proc = subprocess.run(
                [str(self.telemetry_binary), "--mode", "lint-rtl", "--input", "-"],
                input=rtl_source,
                text=True,
                capture_output=True,
                check=False,
            )
            if proc.stdout.strip():
                data = json.loads(proc.stdout)
                return [
                    GuardrailViolation(
                        gate="CPP_STATIC_LINT",
                        rule_id=f["rule_id"],
                        severity=f["severity"],
                        line_number=int(f.get("line_number", 0)),
                        message=f["message"],
                    )
                    for f in data.get("findings", [])
                ]

        # Lightweight Python fallback if C++ binary is unavailable
        violations: list[GuardrailViolation] = []
        if re.search(r"#[0-9]+", rtl_source):
            violations.append(
                GuardrailViolation("STATIC_LINT", "SF-RTL-001", "ERROR", "Non-synthesizable #delay construct detected.")
            )
        return violations

    def _check_pdk_cells(self, rtl_source: str) -> list[GuardrailViolation]:
        violations: list[GuardrailViolation] = []
        # Detect explicit stdcell instantiations like sky130_... or gf180mcu_... or asap7...
        cell_refs = re.findall(r"\b((?:sky130|gf180mcu|tsmc|nangate|asap7)[a-zA-Z0-9_]+)\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(", rtl_source)
        for cell in cell_refs:
            if not any(cell.startswith(pref) for pref in self.pdk_spec.allowed_cell_prefixes):
                violations.append(
                    GuardrailViolation(
                        gate="PDK_CELL_ALLOWLIST",
                        rule_id="SF-GRD-301",
                        severity="ERROR",
                        message=f"Instantiated cell '{cell}' is not in {self.pdk_spec.pdk_name} allowlist.",
                    )
                )
        return violations
