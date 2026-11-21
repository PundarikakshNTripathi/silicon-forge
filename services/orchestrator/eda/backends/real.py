# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real EDA backend: Yosys / SymbiYosys / Verilator / LibreLane (OpenROAD) via a Runner.

The same flow logic serves three environments; only the Runner differs, and provenance is the runner name:
  - NativeRunner -> mode "native"   (tools on PATH)
  - WslRunner    -> mode "wsl"      (tools inside a WSL2 distro)
  - DockerRunner -> mode "docker"   (tools inside a container)
Missing tools raise ToolMissingError; there is no silent fallback to surrogate results.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from services.orchestrator.eda.backends.base import EDABackend
from services.orchestrator.eda.errors import ToolRunError
from services.orchestrator.eda.formal_ir import FormalIRCompiler
from services.orchestrator.eda.librelane import LibreLaneFlow
from services.orchestrator.eda.runner import Runner
from services.orchestrator.eda.vcd import parse_vcd, to_int
from services.orchestrator.state import (
    DesignContract,
    FormalReport,
    FormalWitness,
    LecReport,
    PPAMetrics,
    SimReport,
)

TOOLS = ["yosys", "sby", "verilator", "bitwuzla", "z3", "librelane"]

DEFAULT_ENGINES = {
    "bmc": "smtbmc bitwuzla",
    "prove": "abc pdr",
    "cover": "smtbmc bitwuzla",
}


class RealBackend(EDABackend):
    REQUIRED = {
        "formal": ["sby", "yosys"],
        "sim": ["verilator"],
        "synth": ["yosys"],
        "pnr": ["librelane"],
    }

    def __init__(
        self,
        runner: Runner,
        engines: dict[str, str] | None = None,
        pnr_timeout_sec: float = 3600.0,
        librelane_cmd: list[str] | None = None,
        timing_corner: str = "worst",
    ) -> None:
        self.runner = runner
        self.name = runner.name
        self.engines = {**DEFAULT_ENGINES, **(engines or {})}
        self.compiler = FormalIRCompiler()
        self.pnr_timeout_sec = pnr_timeout_sec
        self.librelane = LibreLaneFlow(runner, command=librelane_cmd, timing_corner=timing_corner)
        self._tools_cache: dict[str, bool] | None = None

    def check_tools(self) -> dict[str, bool]:
        if self._tools_cache is None:
            have = {t: self.runner.has_tool(t) for t in TOOLS if t != "librelane"}
            have["librelane"] = self.librelane.available()
            self._tools_cache = have
        return self._tools_cache

    def install_hint(self) -> str:
        return (
            "Install OSS CAD Suite (yosys, sby, verilator, bitwuzla) and LibreLane (OpenROAD flow); "
            "see docs/eda-setup.md, or run `python scripts/dev.py doctor`."
        )

    # --- formal --------------------------------------------------------------------------------

    def run_formal(
        self,
        run_dir: Path,
        contract: DesignContract,
        rtl_source: str,
        properties_ir: list[dict[str, Any]],
        depth: int,
    ) -> tuple[FormalReport, str]:
        self.require("formal")
        sva = self.compiler.compile_sva_block(contract, properties_ir)
        formal_dir = run_dir / "formal"
        formal_dir.mkdir(parents=True, exist_ok=True)
        rtl_name = f"{contract.module_name}_formal.sv"
        sby_name = f"{contract.module_name}.sby"
        (formal_dir / rtl_name).write_text(self.compiler.inject_formal_block(rtl_source, sva), encoding="utf-8")
        (formal_dir / sby_name).write_text(
            self.compiler.render_sby_config(contract.module_name, rtl_name, depth=depth, engines=self.engines),
            encoding="utf-8",
        )
        res = self.runner.run(["sby", "-f", sby_name], formal_dir, timeout=900)
        status = self._task_status(formal_dir, contract.module_name)
        if status and "ERROR" in status.values() and not all(v == "ERROR" for v in status.values()):
            # One engine crashed while others finished (transient tool error): retry once before judging the design.
            res = self.runner.run(["sby", "-f", sby_name], formal_dir, timeout=900)
            status = self._task_status(formal_dir, contract.module_name)
        log = res.log
        (formal_dir / "sby.log").write_text(log, encoding="utf-8")

        if not status:
            # sby failed before running any task (front-end/syntax error in generated RTL or SVA)
            tail = "\n".join(log.splitlines()[-25:])
            witness = FormalWitness(
                property_id="FRONTEND_ERROR", task="bmc", cycle=0, failing_signal="",
                summary=f"SymbiYosys/Yosys front-end error:\n{tail}",
            )
            return FormalReport(
                bmc_passed=False, prove_passed=False, cover_passed=False, vacuous_detected=False,
                witnesses=[witness], sby_log=log, mode=self.name,
                task_status={"bmc": "ERROR", "prove": "ERROR", "cover": "ERROR"}, elapsed_sec=res.elapsed_sec,
            ), sva

        witnesses = self._witnesses(formal_dir, contract, properties_ir, log)
        report = FormalReport(
            bmc_passed=status.get("bmc") == "PASS",
            prove_passed=status.get("prove") == "PASS",
            cover_passed=status.get("cover") == "PASS",
            # An unreachable cover means assumptions over-constrain the design or the cover is dead: vacuity risk
            vacuous_detected=status.get("cover") not in (None, "PASS"),
            witnesses=witnesses,
            sby_log=log,
            mode=self.name,
            task_status=status,
            elapsed_sec=res.elapsed_sec,
        )
        return report, sva

    @staticmethod
    def _task_status(formal_dir: Path, module: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for task in ("bmc", "prove", "cover"):
            f = formal_dir / f"{module}_{task}" / "status"
            if f.exists():
                words = f.read_text(encoding="utf-8").split()
                out[task] = words[0] if words else "UNKNOWN"
        return out

    def _witnesses(
        self, formal_dir: Path, contract: DesignContract, props: list[dict[str, Any]], log: str
    ) -> list[FormalWitness]:
        witnesses = self.compiler.parse_sby_witness(log)
        for w in witnesses:
            w.failing_signal = self.compiler.resolve_failing_signal(w.property_id, props, contract)
            vcd_path = self._find_trace(formal_dir, contract.module_name, w.task)
            if vcd_path is None:
                continue
            trace = parse_vcd(vcd_path.read_text(encoding="utf-8", errors="replace"))
            # Clocked immediate asserts are reported one step after the offending state (pre-edge sampling)
            t_fail = self._time_of_step(trace, max(w.cycle - 1, 0))
            inputs = {p.name for p in contract.ports if p.direction == "input"}
            outputs = {p.name for p in contract.ports if p.direction == "output"}
            snap = trace.snapshot(t_fail)
            w.stimulus = {k: to_int(v) for k, v in snap.items() if k in inputs}
            observed = {k: to_int(v) for k, v in snap.items() if k in outputs}
            w.summary += f"; inputs={w.stimulus}; outputs={observed}"
            w.trace = trace.to_wavejson()
        return witnesses

    @staticmethod
    def _find_trace(formal_dir: Path, module: str, task: str) -> Path | None:
        for name in ("trace.vcd", "trace_induct.vcd", "trace0.vcd"):
            p = formal_dir / f"{module}_{task}" / "engine_0" / name
            if p.exists():
                return p
        return None

    @staticmethod
    def _time_of_step(trace, step: int) -> int:
        for t, v in trace.changes.get("smt_step", []):
            if to_int(v) == step:
                return t
        return trace.end_time

    # --- simulation / lint ----------------------------------------------------------------------

    def run_simulation(
        self, run_dir: Path, contract: DesignContract, rtl_source: str, testbench: Path | None
    ) -> SimReport:
        self.require("sim")
        sim_dir = run_dir / "sim"
        sim_dir.mkdir(parents=True, exist_ok=True)
        rtl_file = f"{contract.module_name}.sv"
        (sim_dir / rtl_file).write_text(rtl_source, encoding="utf-8")
        res = self.runner.run(
            ["verilator", "--lint-only", "-Wall", "-Wno-fatal", "--top-module", contract.module_name, rtl_file],
            sim_dir, timeout=120,
        )
        errors = len(re.findall(r"^%Error", res.log, re.MULTILINE))
        warns = len(re.findall(r"^%Warning", res.log, re.MULTILINE))
        return SimReport(
            passed=res.ok and errors == 0,
            mode=self.name,
            summary=f"Verilator lint: {errors} error(s), {warns} warning(s)",
            log=res.log,
        )

    # --- synthesis + LEC --------------------------------------------------------------------------

    def run_synthesis_and_lec(
        self, run_dir: Path, contract: DesignContract, rtl_source: str, pdk: str
    ) -> LecReport:
        self.require("synth")
        synth_dir = run_dir / "synthesis"
        synth_dir.mkdir(parents=True, exist_ok=True)
        rtl_file = f"{contract.module_name}.sv"
        (synth_dir / rtl_file).write_text(rtl_source, encoding="utf-8")
        top = contract.module_name
        script = "\n".join([
            f"read_verilog -sv {rtl_file}",
            f"hierarchy -check -top {top}",
            "proc",
            "opt_clean",
            "async2sync",
            f"rename {top} gold",
            "design -stash gold",
            f"read_verilog -sv {rtl_file}",
            f"hierarchy -check -top {top}",
            f"synth -top {top} -nofsm",  # name-based equiv cannot see through FSM re-encoding
            "tee -o synth_stat.txt stat",
            "write_verilog -noattr gate_netlist.v",
            "async2sync",
            f"rename {top} gate",
            "design -stash gate",
            "design -copy-from gold -as gold gold",
            "design -copy-from gate -as gate gate",
            "equiv_make gold gate equiv",
            "hierarchy -top equiv",
            "equiv_simple -undef",
            "equiv_induct -undef",
            "equiv_status -assert",
        ])
        (synth_dir / "lec.ys").write_text(script + "\n", encoding="utf-8")
        res = self.runner.run(["yosys", "-q", "-l", "yosys.log", "lec.ys"], synth_dir, timeout=600)
        log = (synth_dir / "yosys.log").read_text(encoding="utf-8", errors="replace") if (synth_dir / "yosys.log").exists() else res.log
        cells = 0
        stat = synth_dir / "synth_stat.txt"
        if stat.exists():
            m = re.search(r"^\s*(\d+)\s+cells?\b", stat.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                cells = int(m.group(1))
        return LecReport(passed=res.ok, mode=self.name, log=log[-6000:], cell_count=cells, elapsed_sec=res.elapsed_sec)

    # --- physical implementation -------------------------------------------------------------------

    def run_physical_implementation(
        self,
        run_dir: Path,
        contract: DesignContract,
        rtl_source: str,
        knobs: dict[str, Any],
        episode_idx: int,
        pdk: str,
    ) -> PPAMetrics:
        self.require("pnr")
        pnr_dir = run_dir / f"pnr_ep_{episode_idx:02d}"
        pnr_dir.mkdir(parents=True, exist_ok=True)
        try:
            metrics = self.librelane.run(
                pnr_dir, contract, rtl_source, knobs, pdk, timeout=self.pnr_timeout_sec
            )
        except ToolRunError:
            raise
        metrics.mode = self.name
        return metrics
