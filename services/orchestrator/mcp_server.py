"""MCP server: lets Claude Code, Codex, opencode and other MCP clients call SiliconForge's verification tools.

    siliconforge mcp                      # stdio transport
    claude mcp add siliconforge -- python -m services.orchestrator.mcp_server

Tools take and return plain JSON. Real-tool tools (formal, equivalence) raise a clear error when the selected backend
lacks the EDA toolchain; nothing is silently simulated unless backend="simulated" is requested.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from services.orchestrator.config import load_flow_config
from services.orchestrator.eda.bridge import EDABridge, normalize_mode
from services.orchestrator.guardrails.hallucination import HallucinationGuardrail
from services.orchestrator.inference.providers.replay import ReplayProvider
from services.orchestrator.pipeline import SiliconForgePipeline
from services.orchestrator.schemas import PROPERTY_LIST, ContractModel
from services.orchestrator.skills.registry import SkillRegistry

server = MCPServer(
    "siliconforge",
    instructions=(
        "Tools for verifying SystemVerilog with real EDA tools: lint, formal verification (SymbiYosys BMC/PDR/cover), "
        "logical equivalence checking, and an end-to-end spec-to-GDSII run on bundled examples. "
        "Results report their provenance (simulated, native, wsl, docker); only non-simulated results are real."
    ),
)


def _bridge(backend: str) -> EDABridge:
    cfg = load_flow_config()
    cfg.eda_mode = normalize_mode(backend)
    return EDABridge(cfg)


@server.tool()
def lint_rtl(rtl: str, module_name: str = "") -> dict[str, Any]:
    """Statically lint SystemVerilog for synthesizability problems (delays, initial blocks, latch risk, blocking misuse)."""
    cfg = load_flow_config()
    guard = HallucinationGuardrail(cfg.pdk_spec, cfg.telemetry_binary)
    findings = guard._run_cpp_static_lint(rtl)
    return {
        "passed": not any(f.severity == "ERROR" for f in findings),
        "findings": [{"rule": f.rule_id, "severity": f.severity, "line": f.line_number, "message": f.message} for f in findings],
        "module": module_name or None,
    }


@server.tool()
def verify_rtl(rtl: str, contract_json: str, properties_json: str, backend: str = "wsl") -> dict[str, Any]:
    """Formally verify RTL against typed properties with SymbiYosys (bounded check, unbounded proof, cover).

    `contract_json` is a DesignContract; `properties_json` is a JSON array of typed properties (RESET_VALUE, INVARIANT,
    HANDSHAKE_STABILITY, MUTUAL_EXCLUSION, COMBINATIONAL_EQUIVALENCE, LATENCY_BOUND, COVER_REACHABILITY).
    """
    contract = ContractModel.model_validate_json(contract_json).to_state()
    props = [p.model_dump() for p in PROPERTY_LIST.validate_python(json.loads(properties_json))]
    bridge = _bridge(backend)
    with tempfile.TemporaryDirectory(prefix="sf_mcp_") as tmp:
        report, _ = bridge.run_formal_verification(Path(tmp), contract, rtl, props)
    return {
        "provenance": report.mode,
        "passed": report.passed,
        "task_status": report.task_status,
        "vacuous": report.vacuous_detected,
        "counterexamples": [
            {"property": w.property_id, "cycle": w.cycle, "failing_signal": w.failing_signal,
             "stimulus": w.stimulus, "summary": w.summary} for w in report.witnesses
        ],
    }


@server.tool()
def check_equivalence(rtl: str, contract_json: str, backend: str = "wsl") -> dict[str, Any]:
    """Synthesize the RTL with Yosys and prove the gate-level netlist is equivalent to it (LEC)."""
    contract = ContractModel.model_validate_json(contract_json).to_state()
    bridge = _bridge(backend)
    with tempfile.TemporaryDirectory(prefix="sf_mcp_") as tmp:
        lec = bridge.run_synthesis_and_lec(Path(tmp), contract, rtl)
    return {"provenance": lec.mode, "equivalent": lec.passed, "cells": lec.cell_count, "log_tail": lec.log[-1500:]}


@server.tool()
def retrieve_skill(query: str, top_k: int = 2) -> list[dict[str, str]]:
    """Retrieve the most relevant verified knowledge packs (PDK rules, OpenROAD recipes, SymbiYosys patterns)."""
    return [{"name": p.name, "content": p.content} for p in SkillRegistry().retrieve(query, top_k)]


@server.tool()
def list_examples() -> list[str]:
    """Names of the bundled example designs that can be run end to end."""
    return ReplayProvider().designs()


@server.tool()
def run_example(name: str, backend: str = "simulated", inject_bug: bool = False, verify_only: bool = True) -> dict[str, Any]:
    """Run a bundled example through the whole pipeline (contract, RTL, guardrails, formal, LEC, and layout unless verify_only)."""
    if name not in ReplayProvider().designs():
        raise ValueError(f"Unknown example '{name}'. Available: {ReplayProvider().designs()}")
    root = Path(__file__).resolve().parents[2]
    cfg = load_flow_config()
    cfg.eda_mode = normalize_mode(backend)
    if verify_only:
        cfg.pipeline["stop_after"] = "lec"
    with tempfile.TemporaryDirectory(prefix="sf_mcp_run_") as tmp:
        cfg.workspace_root = Path(tmp)
        pipeline = SiliconForgePipeline(cfg, provider=ReplayProvider(inject_initial_bug=inject_bug), eda=EDABridge(cfg))
        state = pipeline.run((root / "hardware" / "examples" / name / "spec.md").read_text(encoding="utf-8"))
    best = state.best_ppa
    return {
        "provenance": state.eda_mode, "stage": state.stage.value, "error": state.error_message,
        "formal_iterations": len(state.formal_history), "lec_passed": state.lec_passed,
        "signoff_passed": state.signoff_passed,
        "ppa": best and {"wns_ns": best.wns_ns, "area_um2": best.area_um2, "power_mw": best.total_power_mw,
                         "drc": best.drc_violations, "lvs_clean": best.lvs_clean},
    }


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
