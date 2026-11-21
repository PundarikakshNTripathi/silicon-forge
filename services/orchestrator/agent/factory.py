"""Builds an agent session for a workspace: config, router with policy and ledger, EDA runner, pipeline hook."""
from __future__ import annotations

from services.orchestrator.agent.session import AgentSession, Approver, Mode
from services.orchestrator.agent.tools import ToolBox
from services.orchestrator.config import load_flow_config
from services.orchestrator.eda.bridge import EDABridge
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.workspace import Workspace


def create_session(ws: Workspace, *, profile: str | None = None, mode: Mode = "ask", approver: Approver | None = None,
                   on_event=None, max_steps: int = 14, router: ModelRouter | None = None,
                   config_path: str = "configs/flow_default.yaml") -> AgentSession:
    cfg = load_flow_config(config_path)
    ws.apply(cfg)
    if profile:
        cfg.inference.profile = profile
    if router is None:
        tracker = CostTracker(max_budget_usd=cfg.inference.max_budget_usd, max_total_tokens=cfg.inference.max_total_tokens)
        router = ModelRouter(cfg.inference, tracker)

    def runner_factory():
        return EDABridge(cfg).backend.runner if cfg.eda_mode != "simulated" else _Simulated()

    def pipeline_factory(spec_text: str, verify_only: bool) -> dict:
        from services.orchestrator.pipeline import SiliconForgePipeline

        run_cfg = load_flow_config(config_path)
        ws.apply(run_cfg)
        run_cfg.inference.profile = cfg.inference.profile
        if verify_only:
            run_cfg.pipeline["stop_after"] = "lec"
        pipe = SiliconForgePipeline(run_cfg, eda=EDABridge(run_cfg))
        state = pipe.run(spec_text)
        best = state.best_ppa
        return {
            "provenance": state.eda_mode, "stage": state.stage.value, "error": state.error_message,
            "formal_iterations": len(state.formal_history), "lec_passed": state.lec_passed,
            "signoff_passed": state.signoff_passed,
            "ppa": best and {"wns_ns": best.wns_ns, "area_um2": best.area_um2, "power_mw": best.total_power_mw},
        }

    tools = ToolBox(ws, runner_factory=runner_factory, pipeline_factory=pipeline_factory)
    return AgentSession(router, ws, tools, mode=mode, approver=approver, on_event=on_event, max_steps=max_steps)


class _Simulated:
    """Stand-in runner for the simulated backend: it exists only so the EDA tool can say 'simulated' honestly."""

    name = "simulated"
