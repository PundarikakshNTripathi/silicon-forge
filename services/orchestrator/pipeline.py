# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end deterministic DAG state machine for SiliconForge (Spec -> RTL -> verified -> GDSII).

Tool verdicts (guardrails, SymbiYosys, Yosys LEC, OpenROAD) decide every edge; LLMs only propose artifacts.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from services.orchestrator.agents.architect import ArchitectAgent
from services.orchestrator.agents.base import StructuredOutputError
from services.orchestrator.agents.formal_verifier import FormalVerifierAgent
from services.orchestrator.agents.physical_designer import PhysicalDesignerAgent
from services.orchestrator.agents.rtl_coder import RtlCoderAgent
from services.orchestrator.config import FlowConfig
from services.orchestrator.eda.bridge import EDABridge
from services.orchestrator.eda.errors import EDAError
from services.orchestrator.guardrails.hallucination import HallucinationGuardrail
from services.orchestrator.inference.cost_tracker import BudgetExceededError, CostTracker
from services.orchestrator.inference.providers import BaseLLMProvider, ReplayMissError
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.paths import RESOURCE_ROOT
from services.orchestrator.rl.optimizer import HybridPpaOptimizer
from services.orchestrator.rl.ppa_env import PpaOptimizationEnv
from services.orchestrator.skills.registry import SkillRegistry
from services.orchestrator.state import (
    DesignContract,
    FlowState,
    GuardrailReport,
    PipelineStage,
    PPAMetrics,
)

EventListener = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


class RunCancelled(Exception):
    """Raised between stages when the caller requested cancellation."""


class SiliconForgePipeline:
    def __init__(
        self,
        config: FlowConfig,
        provider: BaseLLMProvider | None = None,
        repo_root: Path | None = None,
        eda: EDABridge | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self.config = config
        self.repo_root = repo_root or RESOURCE_ROOT
        self.prompts_dir = self.repo_root / "configs" / "prompts"

        self.cost_tracker = cost_tracker or CostTracker(
            max_budget_usd=config.inference.max_budget_usd,
            max_total_tokens=config.inference.max_total_tokens,
        )
        self.router = ModelRouter(config.inference, self.cost_tracker, provider=provider)
        self.skills = SkillRegistry()
        self.guardrail = HallucinationGuardrail(
            pdk_spec=config.pdk_spec,
            telemetry_binary=config.telemetry_binary,
        )
        self.eda = eda or EDABridge(config)

        self.architect = ArchitectAgent(self.router, self.skills, self.prompts_dir)
        self.rtl_coder = RtlCoderAgent(self.router, self.skills, self.prompts_dir)
        self.formal_agent = FormalVerifierAgent(self.router, self.skills, self.prompts_dir)
        self.physical_agent = PhysicalDesignerAgent(self.router, self.skills, self.prompts_dir)

    # ------------------------------------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------------------------------------

    def run(
        self,
        spec_text: str,
        run_id: str | None = None,
        on_event: EventListener | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> FlowState:
        rid = run_id or f"run_{uuid.uuid4().hex[:8]}"
        run_dir = self.config.workspace_root / rid
        run_dir.mkdir(parents=True, exist_ok=True)
        state = FlowState(run_id=rid, spec_text=spec_text, eda_mode=self.eda.mode)
        state.set_listener(on_event)
        self._cancel_check = cancel_check
        self.router.on_call = lambda c: state.log_event(
            "LLM", "CALL",
            f"{c['stage']} -> {c['provider']}/{c['model']} ({c['prompt_tokens']}+{c['completion_tokens']} tok"
            f"{', cache hit' if c['cache_hit'] else ''})",
            c,
        )
        t0 = time.perf_counter()
        state.log_event("INIT", "INFO", f"Run {rid} started (EDA backend: {self.eda.mode}).",
                        {"eda_mode": self.eda.mode, "pdk": self.config.pdk})
        try:
            self._execute(state, run_dir)
        except RunCancelled:
            state.stage = PipelineStage.CANCELLED
            state.error_message = "Run cancelled."
            state.log_event("CANCELLED", "FAIL", "Run cancelled by user.")
        except BudgetExceededError as exc:
            self._fail(state, f"Inference budget exceeded: {exc}")
        except (EDAError, StructuredOutputError, ReplayMissError) as exc:
            self._fail(state, str(exc))
        finally:
            self._persist(state, run_dir, time.perf_counter() - t0)
        return state

    # ------------------------------------------------------------------------------------------------
    # orchestration
    # ------------------------------------------------------------------------------------------------

    def _execute(self, state: FlowState, run_dir: Path) -> None:
        # Stage 1: Principal Architect -> DesignContract
        self._enter(state, PipelineStage.ARCHITECT)
        contract = self.architect.synthesize_contract(state.spec_text, self.config.pdk)
        state.contract = contract
        state.log_event("ARCHITECT", "PASS",
                        f"Synthesized DesignContract for '{contract.module_name}' ({len(contract.ports)} ports).",
                        {"contract": asdict(contract)})

        # Stage 2/3: RTL generation + guardrail + simulation-lint repair loop
        self._enter(state, PipelineStage.RTL_GEN)
        rtl = self.rtl_coder.generate_rtl(contract, state.spec_text)
        state.add_rtl_version("initial", rtl)
        rtl = self._rtl_quality_loop(state, run_dir, contract, rtl)
        if rtl is None:
            return
        state.rtl_source = rtl

        # Stage 4: formal verification (TypedPropertyIR -> SymbiYosys + COI repair)
        if not self._formal_loop(state, run_dir, contract):
            return
        stop_after = str(self.config.pipeline.get("stop_after") or "")
        if stop_after == "formal":
            state.log_event("FORMAL_VERIFY", "INFO", "Stopped after formal verification (verify-only run).")
            return

        # Stage 5: synthesis + LEC (gate 6)
        self._enter(state, PipelineStage.SYNTHESIS_LEC)
        lec = self.eda.run_synthesis_and_lec(run_dir, contract, state.rtl_source)
        state.lec_report = lec
        state.lec_passed = lec.passed
        lec_audit = self.guardrail.audit_lec(lec)
        state.guardrail_history.append(lec_audit)
        if self.config.pipeline.get("require_lec_pass", True) and not lec.passed:
            state.log_event("SYNTHESIS_LEC", "FAIL", "Post-synthesis LEC failed.",
                            {"mode": lec.mode, "log_tail": lec.log[-1500:]})
            self._fail(state, "Post-synthesis LEC failed: the netlist is not equivalent to the RTL.")
            return
        state.log_event("SYNTHESIS_LEC", "PASS",
                        f"Yosys synthesis + RTL-to-gate LEC passed ({lec.cell_count} cells, mode={lec.mode}).",
                        {"mode": lec.mode, "cells": lec.cell_count, "elapsed_sec": lec.elapsed_sec})

        if stop_after == "lec":
            state.log_event("SYNTHESIS_LEC", "INFO", "Stopped after LEC (verify-only run).")
            return

        # Stage 6: physical implementation + hybrid RL / Pareto PPA optimization
        self._enter(state, PipelineStage.PNR_RL_OPT)
        best = self._ppa_optimization(state, run_dir, contract)

        # Stage 7: signoff
        self._enter(state, PipelineStage.SIGNOFF)
        self._signoff(state, run_dir, contract, best)

    # ------------------------------------------------------------------------------------------------
    # stage helpers
    # ------------------------------------------------------------------------------------------------

    def _rtl_quality_loop(
        self, state: FlowState, run_dir: Path, contract: DesignContract, rtl: str
    ) -> str | None:
        max_iters = int(self.config.pipeline.get("max_rtl_repair_iters", 5))
        for attempt in range(1, max_iters + 1):
            self._check_cancel()
            state.stage = PipelineStage.GUARDRAIL_AUDIT
            grd = self.guardrail.audit_rtl(rtl, contract)
            state.guardrail_history.append(grd)
            if grd.passed:
                state.log_event("GUARDRAIL_AUDIT", "PASS",
                                f"RTL passed contract + lint + PDK guardrails on attempt {attempt}.",
                                {"attempt": attempt, "warnings": self._violations(grd)})
                sim = self.eda.run_simulation(run_dir, contract, rtl, None)
                state.sim_report = sim
                if sim.passed:
                    state.stage = PipelineStage.SIMULATION
                    state.log_event("SIMULATION", "PASS", sim.summary, {"mode": sim.mode})
                    return rtl
                state.log_event("SIMULATION", "RETRY", f"Lint failed: {sim.summary}; repairing RTL.",
                                {"mode": sim.mode, "log_tail": sim.log[-1500:]})
                diagnostic = f"Verilator lint errors:\n{sim.log[-2500:]}"
            else:
                diagnostic = "\n".join(f"[{v.rule_id}] {v.message}" for v in grd.violations)
                state.log_event("GUARDRAIL_AUDIT", "RETRY",
                                f"Guardrail violations on attempt {attempt}; repairing RTL.",
                                {"attempt": attempt, "violations": self._violations(grd)})
            rtl = self.rtl_coder.repair_rtl(contract, rtl, diagnostic, retry_attempt=attempt)
            state.add_rtl_version(f"guardrail-repair-{attempt}", rtl)
        self._fail(state, "RTL failed guardrails / lint after the maximum number of repair iterations.")
        return None

    def _formal_loop(self, state: FlowState, run_dir: Path, contract: DesignContract) -> bool:
        self._enter(state, PipelineStage.FORMAL_VERIFY)
        props_ir = self.formal_agent.synthesize_properties_ir(contract, state.spec_text)
        for ir_attempt in range(2):
            ir_audit = self.guardrail.audit_formal_ir(props_ir, contract)
            state.guardrail_history.append(ir_audit)
            if ir_audit.passed:
                break
            state.log_event("FORMAL_VERIFY", "RETRY", "Property IR failed grounding/vacuity guardrail.",
                            {"violations": self._violations(ir_audit)})
            if ir_attempt == 1:
                self._fail(state, "Formal IR failed anti-vacuity / port-grounding guardrail.")
                return False
            props_ir = self.formal_agent.synthesize_properties_ir(
                contract,
                state.spec_text + "\n\nPrevious property set was rejected: "
                + "; ".join(v.message for v in ir_audit.violations),
            )
        state.formal_properties_ir = props_ir

        max_iters = int(self.config.pipeline.get("max_formal_iters", 4))
        for f_iter in range(1, max_iters + 1):
            self._check_cancel()
            report, sva = self.eda.run_formal_verification(run_dir, contract, state.rtl_source, props_ir)
            state.formal_history.append(report)
            state.sva_source = sva
            vac = self.guardrail.audit_formal_result(report)
            state.guardrail_history.append(vac)
            payload = {
                "iteration": f_iter, "mode": report.mode, "task_status": report.task_status,
                "elapsed_sec": report.elapsed_sec,
                "witnesses": [asdict(w) for w in report.witnesses],
            }
            if report.passed and vac.passed:
                state.log_event("FORMAL_VERIFY", "PASS",
                                f"BMC + k-induction/PDR + cover proved on iteration {f_iter} (mode={report.mode}).",
                                payload)
                return True

            witness = report.witnesses[0] if report.witnesses else None
            if witness is None and report.vacuous_detected:
                diag = "; ".join(v.message for v in vac.violations)
                fail_sig = None
            else:
                diag = witness.summary if witness else report.sby_log[-2000:]
                fail_sig = witness.failing_signal if witness else None
            state.log_event("FORMAL_VERIFY", "RETRY",
                            f"Formal check failed on iteration {f_iter} "
                            f"({witness.property_id if witness else 'vacuity'}); applying COI-sliced repair.",
                            payload)
            if f_iter == max_iters:
                break
            repaired = self.rtl_coder.repair_rtl(
                contract, state.rtl_source, diag, failing_signal=fail_sig, retry_attempt=f_iter
            )
            state.add_rtl_version(f"formal-repair-{f_iter}", repaired)
            # Re-audit every repair: a fix must not reintroduce guardrail violations
            grd = self.guardrail.audit_rtl(repaired, contract)
            state.guardrail_history.append(grd)
            if not grd.passed:
                state.log_event("GUARDRAIL_AUDIT", "RETRY", "Formal repair violated RTL guardrails; re-repairing.",
                                {"violations": self._violations(grd)})
                repaired = self.rtl_coder.repair_rtl(
                    contract, repaired, "\n".join(f"[{v.rule_id}] {v.message}" for v in grd.violations),
                    retry_attempt=f_iter + 1,
                )
                state.add_rtl_version(f"formal-repair-{f_iter}-guardrail-fix", repaired)
                if not self.guardrail.audit_rtl(repaired, contract).passed:
                    self._fail(state, "RTL repair kept violating guardrails after formal feedback.")
                    return False
            state.rtl_source = repaired
        self._fail(state, "Formal verification did not converge within the iteration budget.")
        return False

    def _ppa_optimization(self, state: FlowState, run_dir: Path, contract: DesignContract) -> PPAMetrics:
        rl_cfg = self.config.rl_optimizer
        targets = dict(self.config.targets)
        env = PpaOptimizationEnv(
            eda_bridge=self.eda,
            run_dir=run_dir,
            contract=contract,
            targets=targets,
            reward_weights=rl_cfg.get("reward_weights", {}),
            rtl_source=state.rtl_source,
            allow_clock_relax=bool(rl_cfg.get("allow_clock_relax", False)),
            cache_dir=self.config.workspace_root / "_cache" / "ppa",
        )
        optimizer = HybridPpaOptimizer(
            num_actions=env.num_actions,
            learning_rate=float(rl_cfg.get("learning_rate", 0.15)),
            ppo_clip_eps=float(rl_cfg.get("ppo_clip_eps", 0.2)),
            exploration_c=float(rl_cfg.get("exploration_c", 1.414)),
            pareto_archive_size=int(rl_cfg.get("pareto_archive_size", 8)),
        )
        min_wns = float(targets.get("min_wns_ns", 0.0))

        def is_clean(m: PPAMetrics) -> bool:
            return m.wns_ns >= min_wns and m.drc_violations == 0 and m.lvs_clean

        def on_episode(n: int, m: PPAMetrics) -> None:
            state.log_event("PNR_RL_OPT", "EPISODE",
                            f"Episode {n}: WNS={m.wns_ns:.3f}ns area={m.area_um2:.0f}um² DRC={m.drc_violations} "
                            f"reward={m.reward:.2f}{' (cached)' if m.cached else ''} [{m.mode}]",
                            {"episode": n, "metrics": asdict(m)})

        episodes, pareto, best = optimizer.optimize(
            env,
            max_episodes=int(self.config.pipeline.get("max_pnr_rl_episodes", 6)),
            batch_size=int(rl_cfg.get("parallel_episodes", 1)),
            is_clean=is_clean if rl_cfg.get("early_stop_on_clean", True) else None,
            on_episode=on_episode,
            cancel_check=self._cancel_check,
        )
        self._check_cancel()
        state.ppa_episodes = episodes
        state.pareto_front = pareto
        state.best_ppa = best

        knob_audit = self.guardrail.audit_flow_config(
            {k: v for k, v in best.knobs.items()}
        )
        state.guardrail_history.append(knob_audit)
        state.log_event(
            "PNR_RL_OPT", "PASS" if knob_audit.passed else "WARN",
            f"PPA optimization finished {len(episodes)} episode(s) (cache hits: {env.cache_hits}); "
            f"best WNS={best.wns_ns:.3f}ns, area={best.area_um2:.0f}um², power={best.total_power_mw:.3f}mW, "
            f"DRC={best.drc_violations}.",
            {"mode": best.mode, "episodes": len(episodes), "cache_hits": env.cache_hits},
        )
        return best

    def _signoff(self, state: FlowState, run_dir: Path, contract: DesignContract, best: PPAMetrics) -> None:
        targets = self.config.targets
        min_wns = float(targets.get("min_wns_ns", 0.0))
        target_clk = float(targets.get("clock_period_ns", contract.target_clock_ns))
        achieved_clk = float(best.knobs.get("CLOCK_PERIOD", target_clk))
        flow_audit_ok = state.guardrail_history[-1].passed if state.guardrail_history else True
        checks = [
            (f"Timing (WNS >= {min_wns:.2f} ns @ {target_clk:.2f} ns clock)",
             best.wns_ns >= min_wns and achieved_clk <= target_clk + 1e-9,
             f"WNS {best.wns_ns:.3f} ns @ {achieved_clk:.2f} ns clock"),
            ("DRC clean", best.drc_violations == 0 or not self.config.pipeline.get("require_zero_drc", True),
             f"{best.drc_violations} violation(s)"),
            ("LVS clean", best.lvs_clean or not self.config.pipeline.get("require_clean_lvs", True),
             "clean" if best.lvs_clean else "mismatch"),
            ("RTL == netlist (LEC)", state.lec_passed, "passed" if state.lec_passed else "failed"),
            ("Formal proofs non-vacuous", bool(state.formal_history) and state.formal_history[-1].passed,
             "BMC + prove + cover"),
            ("Flow variables on PDK allowlist", flow_audit_ok, "ok" if flow_audit_ok else "violations"),
            ("Area budget", best.area_um2 <= float(targets.get("max_area_um2", 1e12)),
             f"{best.area_um2:.0f} um² (max {targets.get('max_area_um2', '-')})"),
            ("Power budget", best.total_power_mw <= float(targets.get("max_power_mw", 1e12)),
             f"{best.total_power_mw:.3f} mW (max {targets.get('max_power_mw', '-')})"),
        ]
        state.signoff_checks = [{"name": n, "passed": bool(ok), "detail": d} for n, ok, d in checks]
        state.signoff_passed = all(ok for _, ok, _ in checks)

        if best.gds_path and Path(best.gds_path).exists():
            state.gds_path = best.gds_path
        if state.signoff_passed:
            note = "" if best.mode != "simulated" else " (SIMULATED flow: surrogate PPA, no real GDSII)"
            state.log_event("SIGNOFF", "PASS",
                            f"Signoff checks passed{note}." + (f" GDSII: {Path(state.gds_path).name}" if state.gds_path else ""),
                            {"mode": best.mode, "checks": state.signoff_checks})
        else:
            failed = [n for n, ok, _ in checks if not ok]
            eco = self.physical_agent.advise_eco(best, self.config.pdk)
            state.stage = PipelineStage.FAILED
            state.error_message = "Signoff not closed: " + "; ".join(failed)
            state.log_event("SIGNOFF", "WARN",
                            f"Signoff not closed ({', '.join(failed)}). ECO advice: {eco.get('recommendation')}",
                            {"mode": best.mode, "checks": state.signoff_checks, "eco": eco})

    # ------------------------------------------------------------------------------------------------
    # utilities
    # ------------------------------------------------------------------------------------------------

    _cancel_check: CancelCheck | None = None

    def _check_cancel(self) -> None:
        if self._cancel_check is not None and self._cancel_check():
            raise RunCancelled()

    def _enter(self, state: FlowState, stage: PipelineStage) -> None:
        self._check_cancel()
        state.stage = stage

    @staticmethod
    def _violations(report: GuardrailReport) -> list[dict[str, Any]]:
        return [asdict(v) for v in report.violations]

    def _fail(self, state: FlowState, message: str) -> None:
        state.stage = PipelineStage.FAILED
        state.error_message = message
        state.log_event("FAILED", "FAIL", message)

    def _persist(self, state: FlowState, run_dir: Path, elapsed: float) -> None:
        payload = {
            "state": state.to_dict(),
            "cost": {
                "total_cost_usd": self.cost_tracker.total_cost_usd,
                "total_tokens": self.cost_tracker.total_tokens,
                "cache_hit_rate": self.cost_tracker.cache_hit_rate,
                "by_stage": self.cost_tracker.summary_by_stage(),
            },
            "elapsed_sec": round(elapsed, 2),
            "eda_mode": self.eda.mode,
        }
        (run_dir / "signoff_report.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
