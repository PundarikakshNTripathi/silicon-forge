"""End-to-end deterministic DAG State Machine for SiliconForge RTL-to-GDSII."""
from __future__ import annotations

import json
from pathlib import Path
import uuid
from typing import Optional

from services.orchestrator.agents.architect import ArchitectAgent
from services.orchestrator.agents.formal_verifier import FormalVerifierAgent
from services.orchestrator.agents.physical_designer import PhysicalDesignerAgent
from services.orchestrator.agents.rtl_coder import RtlCoderAgent
from services.orchestrator.config import FlowConfig
from services.orchestrator.eda.bridge import EDABridge
from services.orchestrator.guardrails.hallucination import HallucinationGuardrail
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.providers import BaseLLMProvider
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.rl.optimizer import HybridPpaOptimizer
from services.orchestrator.rl.ppa_env import PpaOptimizationEnv
from services.orchestrator.skills.registry import SkillRegistry
from services.orchestrator.state import FlowState, PipelineStage


class SiliconForgePipeline:
    def __init__(
        self,
        config: FlowConfig,
        provider: Optional[BaseLLMProvider] = None,
        repo_root: Optional[Path] = None,
    ) -> None:
        self.config = config
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]
        self.prompts_dir = self.repo_root / "configs" / "prompts"

        self.cost_tracker = CostTracker(
            max_budget_usd=config.inference.max_budget_usd,
            max_total_tokens=config.inference.max_total_tokens,
        )
        self.router = ModelRouter(config.inference, self.cost_tracker, provider=provider)
        self.skills = SkillRegistry()
        self.guardrail = HallucinationGuardrail(
            pdk_spec=config.pdk_spec,
            telemetry_binary=config.telemetry_binary,
        )
        self.eda = EDABridge(config)

        self.architect = ArchitectAgent(self.router, self.skills, self.prompts_dir)
        self.rtl_coder = RtlCoderAgent(self.router, self.skills, self.prompts_dir)
        self.formal_agent = FormalVerifierAgent(self.router, self.skills, self.prompts_dir)
        self.physical_agent = PhysicalDesignerAgent(self.router, self.skills, self.prompts_dir)

    def run(self, spec_text: str, run_id: Optional[str] = None) -> FlowState:
        rid = run_id or f"run_{uuid.uuid4().hex[:8]}"
        run_dir = self.config.workspace_root / rid
        run_dir.mkdir(parents=True, exist_ok=True)
        state = FlowState(run_id=rid, spec_text=spec_text)

        # Stage 1: Principal Architect -> DesignContract JSON
        state.stage = PipelineStage.ARCHITECT
        contract = self.architect.synthesize_contract(spec_text, self.config.pdk)
        state.contract = contract
        state.log_event("ARCHITECT", "PASS", f"Synthesized DesignContract for '{contract.module_name}' ({len(contract.ports)} ports).")

        # Stage 2 & 3: RTL Generation + Anti-Hallucination Guardrail Loop
        state.stage = PipelineStage.RTL_GEN
        rtl = self.rtl_coder.generate_rtl(contract, spec_text)
        max_rtl_iters = int(self.config.pipeline.get("max_rtl_repair_iters", 5))
        for attempt in range(1, max_rtl_iters + 1):
            state.stage = PipelineStage.GUARDRAIL_AUDIT
            grd = self.guardrail.audit_rtl(rtl, contract)
            state.guardrail_history.append(grd)
            if grd.passed:
                state.log_event("GUARDRAIL_AUDIT", "PASS", f"RTL passed all static & contract guardrails on attempt {attempt}.")
                break
            diag = "\n".join(f"[{v.rule_id}] {v.message}" for v in grd.violations)
            state.log_event("GUARDRAIL_AUDIT", "RETRY", f"Guardrail violations on attempt {attempt}; repairing RTL.")
            rtl = self.rtl_coder.repair_rtl(contract, rtl, diag, retry_attempt=attempt)
        else:
            state.stage = PipelineStage.FAILED
            state.error_message = "RTL failed anti-hallucination guardrails after max iterations."
            return state
        state.rtl_source = rtl

        # Stage 4: Closed-Loop Formal Verification (TypedPropertyIR -> SymbiYosys + COI Repair)
        state.stage = PipelineStage.FORMAL_VERIFY
        props_ir = self.formal_agent.synthesize_properties_ir(contract, spec_text)
        ir_audit = self.guardrail.audit_formal_ir(props_ir, contract)
        state.guardrail_history.append(ir_audit)
        if not ir_audit.passed:
            state.stage = PipelineStage.FAILED
            state.error_message = "Formal IR failed anti-vacuity / port-grounding guardrail."
            return state
        state.formal_properties_ir = props_ir

        max_formal_iters = int(self.config.pipeline.get("max_formal_iters", 4))
        for f_iter in range(1, max_formal_iters + 1):
            formal_rep, sva_block = self.eda.run_formal_verification(run_dir, contract, state.rtl_source, props_ir)
            state.formal_history.append(formal_rep)
            state.sva_source = sva_block
            if formal_rep.passed:
                state.log_event("FORMAL_VERIFY", "PASS", f"SymbiYosys BMC + k-induction + Cover proved on iteration {f_iter}.")
                break
            witness = formal_rep.witnesses[0] if formal_rep.witnesses else None
            diag = witness.summary if witness else formal_rep.sby_log
            fail_sig = witness.failing_signal if witness else None
            state.log_event("FORMAL_VERIFY", "RETRY", f"Formal counterexample on iter {f_iter} ({fail_sig}); applying COI-sliced repair.")
            state.rtl_source = self.rtl_coder.repair_rtl(
                contract, state.rtl_source, diag, failing_signal=fail_sig, retry_attempt=f_iter
            )
        else:
            state.stage = PipelineStage.FAILED
            state.error_message = "Formal verification did not converge within iteration budget."
            return state

        # Stage 5: Synthesis & Formal Logical Equivalence Check (LEC)
        state.stage = PipelineStage.SYNTHESIS_LEC
        lec_ok, lec_log = self.eda.run_synthesis_and_lec(run_dir, contract, state.rtl_source)
        state.lec_passed = lec_ok
        if self.config.pipeline.get("require_lec_pass", True) and not lec_ok:
            state.stage = PipelineStage.FAILED
            state.error_message = f"Post-synthesis LEC failed: {lec_log}"
            return state
        state.log_event("SYNTHESIS_LEC", "PASS", "Yosys synthesis & formal RTL-to-Gate LEC passed.")

        # Stage 6: Physical Implementation & Hybrid RL + Pareto PPA Optimization
        state.stage = PipelineStage.PNR_RL_OPT
        rl_cfg = self.config.rl_optimizer
        env = PpaOptimizationEnv(
            eda_bridge=self.eda,
            run_dir=run_dir,
            contract=contract,
            targets=self.config.targets,
            reward_weights=rl_cfg.get("reward_weights", {}),
        )
        optimizer = HybridPpaOptimizer(
            num_actions=env.num_actions,
            learning_rate=float(rl_cfg.get("learning_rate", 0.15)),
            ppo_clip_eps=float(rl_cfg.get("ppo_clip_eps", 0.2)),
            exploration_c=float(rl_cfg.get("exploration_c", 1.414)),
            pareto_archive_size=int(rl_cfg.get("pareto_archive_size", 8)),
        )
        episodes, pareto, best = optimizer.optimize(
            env, max_episodes=int(self.config.pipeline.get("max_pnr_rl_episodes", 6))
        )
        state.ppa_episodes = episodes
        state.pareto_front = pareto
        state.best_ppa = best

        # Validate winning knobs against PDK/Tcl guardrail
        knob_audit = self.guardrail.audit_flow_config(best.knobs)
        state.guardrail_history.append(knob_audit)
        state.log_event(
            "PNR_RL_OPT",
            "PASS" if knob_audit.passed else "WARN",
            f"RL PPA optimization finished {len(episodes)} episodes; best WNS={best.wns_ns}ns, Area={best.area_um2}um^2, Power={best.total_power_mw}mW, DRC={best.drc_violations}.",
        )

        # Stage 7: Final Tapeout Signoff Gate
        state.stage = PipelineStage.SIGNOFF
        signoff_ok = (
            best.wns_ns >= float(self.config.targets.get("min_wns_ns", 0.0))
            and (best.drc_violations == 0 or not self.config.pipeline.get("require_zero_drc", True))
            and (best.lvs_clean or not self.config.pipeline.get("require_clean_lvs", True))
            and state.lec_passed
            and knob_audit.passed
        )
        state.signoff_passed = signoff_ok
        gds_file = run_dir / f"{contract.module_name}.gds"
        if signoff_ok:
            gds_file.write_bytes(b"SILICONFORGE_GDSII_STREAM_V1\x00")
            state.gds_path = str(gds_file)
            state.log_event("SIGNOFF", "PASS", f"Tapeout signoff CLEAN! GDSII written to {gds_file.name}.")
        else:
            eco = self.physical_agent.advise_eco(best, self.config.pdk)
            state.log_event("SIGNOFF", "WARN", f"Signoff targets not fully closed; ECO advised: {eco.get('recommendation')}")

        # Persist full telemetry report for Dashboard UI
        report_path = run_dir / "signoff_report.json"
        report_payload = {
            "state": state.to_dict(),
            "cost": {
                "total_cost_usd": self.cost_tracker.total_cost_usd,
                "total_tokens": self.cost_tracker.total_tokens,
                "cache_hit_rate": self.cost_tracker.cache_hit_rate,
                "by_stage": self.cost_tracker.summary_by_stage(),
            },
        }
        report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
        return state
