"""Simulated EDA backend: deterministic surrogate models for demos, CI and offline development.

Everything returned here is labelled mode="simulated". It does NOT run Yosys/SymbiYosys/OpenROAD and its
PPA numbers come from an analytic surrogate, not a real flow. It exists so the full pipeline, UI and tests
can run without the EDA toolchain, never to stand in for real results.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from services.orchestrator.eda.backends.base import EDABackend
from services.orchestrator.eda.formal_ir import FormalIRCompiler
from services.orchestrator.state import (
    DesignContract,
    FormalReport,
    FormalWitness,
    LecReport,
    PPAMetrics,
    SimReport,
)


class SimulatedBackend(EDABackend):
    name = "simulated"

    def __init__(self, telemetry_binary: Path | None = None) -> None:
        self.telemetry_binary = telemetry_binary
        self.formal_compiler = FormalIRCompiler()

    def check_tools(self) -> dict[str, bool]:
        return {}

    def run_formal(self, run_dir, contract, rtl_source, properties_ir, depth):
        sva = self.formal_compiler.compile_sva_block(contract, properties_ir)
        formal_dir = run_dir / "formal"
        formal_dir.mkdir(parents=True, exist_ok=True)
        (formal_dir / f"{contract.module_name}_formal.sv").write_text(
            self.formal_compiler.inject_formal_block(rtl_source, sva), encoding="utf-8"
        )
        t0 = time.perf_counter()
        report = self._formal_surrogate(rtl_source, properties_ir)
        report.elapsed_sec = round(time.perf_counter() - t0, 4)
        return report, sva

    def run_simulation(self, run_dir, contract, rtl_source, testbench):
        ok = f"module {contract.module_name}" in rtl_source
        return SimReport(passed=ok, mode=self.name, summary="simulated lint (module present)", skipped=False)

    def run_synthesis_and_lec(self, run_dir, contract, rtl_source, pdk):
        synth_dir = run_dir / "synthesis"
        synth_dir.mkdir(parents=True, exist_ok=True)
        (synth_dir / f"{contract.module_name}.sv").write_text(rtl_source, encoding="utf-8")
        ok = f"module {contract.module_name}" in rtl_source
        log = f"[simulated] no Yosys run. Surrogate LEC verdict for {contract.module_name}: {'pass' if ok else 'fail'}"
        return LecReport(passed=ok, mode=self.name, log=log, cell_count=1380)

    def run_physical_implementation(self, run_dir, contract, rtl_source, knobs, episode_idx, pdk):
        t0 = time.perf_counter()
        pnr_dir = run_dir / f"pnr_ep_{episode_idx:02d}"
        pnr_dir.mkdir(parents=True, exist_ok=True)
        raw_log = self._surrogate_logs(contract, knobs)
        (pnr_dir / "surrogate_signoff.log").write_text(raw_log, encoding="utf-8")
        parsed = self.parse_logs(raw_log)
        return PPAMetrics(
            wns_ns=float(parsed["wns_ns"]),
            tns_ns=float(parsed["tns_ns"]),
            area_um2=float(parsed["area_um2"]),
            total_power_mw=float(parsed["total_power_mw"]),
            cell_count=int(parsed["cell_count"]),
            drc_violations=int(parsed["drc_violations"]),
            lvs_clean=bool(parsed["lvs_clean"]),
            runtime_sec=round(time.perf_counter() - t0, 4),
            knobs=dict(knobs),
            mode=self.name,
            run_dir=str(pnr_dir),
        )

    # --- log parsing (C++ sf-telemetry when built, Python fallback otherwise) -----------------

    def parse_logs(self, log_text: str) -> dict[str, Any]:
        binary = self.telemetry_binary
        if binary is not None and Path(binary).exists():
            proc = subprocess.run(
                [str(binary), "--mode", "parse-logs", "--input", "-"],
                input=log_text, text=True, capture_output=True, check=True,
            )
            return json.loads(proc.stdout)

        def _find(pattern: str, default: float) -> float:
            m = re.search(pattern, log_text, re.IGNORECASE)
            return float(m.group(1)) if m else default

        return {
            "wns_ns": _find(r"wns\s+(-?[0-9.]+)", 0.0),
            "tns_ns": _find(r"tns\s+(-?[0-9.]+)", 0.0),
            "area_um2": _find(r"Design area\s+([0-9.]+)", 18000.0),
            "total_power_mw": _find(r"Total Power:\s*([0-9.]+)\s*mW", 2.0),
            "cell_count": int(_find(r"Number of cells:\s*([0-9]+)", 1200)),
            "drc_violations": int(_find(r"Total DRC errors:\s*([0-9]+)", 0)),
            "lvs_clean": "Circuits match uniquely" in log_text,
        }

    # --- surrogate models ---------------------------------------------------------------------

    def _formal_surrogate(self, rtl_source: str, properties_ir: list[dict[str, Any]]) -> FormalReport:
        # Fixture-based: detects the demo's deliberately inverted zero_o polarity. Not a real proof.
        if "(alu_comb != 32'd0)" in rtl_source:
            witness = FormalWitness(
                property_id="P_ZERO_FLAG_CONSISTENT",
                task="bmc",
                cycle=2,
                failing_signal="zero_o",
                stimulus={"rst_n": 1, "valid_i": 1, "op_i": 0, "a_i": 5, "b_i": 3},
                summary="[simulated] BMC counterexample at cycle 2: result_o=8, zero_o=1 (expected 0).",
            )
            return FormalReport(
                bmc_passed=False, prove_passed=False, cover_passed=True, vacuous_detected=False,
                witnesses=[witness], mode=self.name,
                sby_log=f"[simulated] Assert failed in axi_lite_alu: P_ZERO_FLAG_CONSISTENT at step 2\n{witness.summary}",
                task_status={"bmc": "FAIL", "prove": "FAIL", "cover": "PASS"},
            )
        has_cover = any(p.get("kind") == "COVER_REACHABILITY" for p in properties_ir)
        return FormalReport(
            bmc_passed=True, prove_passed=True, cover_passed=has_cover, vacuous_detected=not has_cover,
            mode=self.name,
            sby_log="[simulated] no SymbiYosys run (surrogate verdict: PASS)",
            task_status={"bmc": "PASS", "prove": "PASS", "cover": "PASS" if has_cover else "FAIL"},
        )

    def _surrogate_logs(self, contract: DesignContract, knobs: dict[str, Any]) -> str:
        strat = str(knobs.get("SYNTH_STRATEGY", "AREA 0"))
        util = float(knobs.get("FP_CORE_UTIL", 45))
        density = float(knobs.get("PL_TARGET_DENSITY_PCT", 60))
        grt_adj = float(knobs.get("GRT_ADJUSTMENT", 0.30))
        clk = float(knobs.get("CLOCK_PERIOD", contract.target_clock_ns))

        is_delay = strat.startswith("DELAY")
        base_delay_ns = 8.4 if is_delay else 10.3
        congestion_delay = max(0.0, (util - 55.0) * 0.045) + max(0.0, (density - 65.0) * 0.03)
        wns = round(clk - (base_delay_ns + congestion_delay), 3)
        tns = round(min(0.0, wns * 6.5), 3)
        area_scale = 1.12 if is_delay else 0.94
        area = round((17800.0 * area_scale) * (50.0 / max(30.0, util)) ** 0.35, 2)
        cells = int(round(area / 12.8))
        power_mw = round((2.45 if is_delay else 1.95) * (10.0 / max(4.0, clk)) ** 0.7, 3)
        drc = 0
        if util > 60 or (density > 72 and grt_adj < 0.25):
            drc = int(math.ceil(max(0.0, util - 58) * 1.5 + max(0.0, density - 70)))
        return (
            f"Number of cells: {cells}\n"
            f"Design area {area} u^2 {util:.1f}% utilization.\n"
            f"wns {wns}\ntns {tns}\n"
            f"Total Power: {power_mw} mW\n"
            f"Total DRC errors: {drc}\n"
            "Circuits match uniquely.\n"
        )
