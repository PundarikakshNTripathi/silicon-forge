"""CLI and optional FastAPI HTTP server entrypoint for SiliconForge."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from services.orchestrator.config import load_flow_config
from services.orchestrator.inference.providers import MockChipDesignLLM
from services.orchestrator.pipeline import SiliconForgePipeline


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SiliconForge Autonomous RTL-to-GDSII Chip Design Agent")
    parser.add_argument("--spec", type=str, required=True, help="Path to hardware specification markdown/text file")
    parser.add_argument("--config", type=str, default="configs/flow_default.yaml", help="Path to flow YAML config")
    parser.add_argument("--inject-bug", action="store_true", help="Demo mode: inject an initial RTL bug to exercise formal counterexample repair")
    parser.add_argument("--run-id", type=str, default=None, help="Optional custom run identifier")
    args = parser.parse_args(argv)

    cfg = load_flow_config(args.config)
    spec_text = Path(args.spec).read_text(encoding="utf-8")
    provider = MockChipDesignLLM(inject_initial_bug=args.inject_bug) if cfg.inference.provider == "mock" else None
    pipeline = SiliconForgePipeline(cfg, provider=provider)
    state = pipeline.run(spec_text, run_id=args.run_id)

    summary = {
        "run_id": state.run_id,
        "module": state.contract.module_name if state.contract else None,
        "stage": state.stage.value,
        "signoff_passed": state.signoff_passed,
        "lec_passed": state.lec_passed,
        "formal_iterations": len(state.formal_history),
        "best_ppa": {
            "wns_ns": state.best_ppa.wns_ns,
            "tns_ns": state.best_ppa.tns_ns,
            "area_um2": state.best_ppa.area_um2,
            "total_power_mw": state.best_ppa.total_power_mw,
            "drc_violations": state.best_ppa.drc_violations,
            "lvs_clean": state.best_ppa.lvs_clean,
            "reward": state.best_ppa.reward,
        } if state.best_ppa else None,
        "cost_usd": pipeline.cost_tracker.total_cost_usd,
        "total_tokens": pipeline.cost_tracker.total_tokens,
        "gds_path": state.gds_path,
    }
    print(json.dumps(summary, indent=2))
    return 0 if state.signoff_passed else 1


def create_app():
    """Creates a FastAPI application for the React Cockpit dashboard (requires optional `api` extras)."""
    from fastapi import FastAPI
    from pydantic import BaseModel

    app = FastAPI(title="SiliconForge RTL-to-GDSII Agent API", version="1.0.0")

    class RunRequest(BaseModel):
        spec_text: str
        config_path: str = "configs/flow_default.yaml"
        inject_bug: bool = False

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "siliconforge-orchestrator"}

    @app.post("/api/v1/runs")
    def start_run(req: RunRequest):
        cfg = load_flow_config(req.config_path)
        provider = MockChipDesignLLM(inject_initial_bug=req.inject_bug) if cfg.inference.provider == "mock" else None
        pipeline = SiliconForgePipeline(cfg, provider=provider)
        state = pipeline.run(req.spec_text)
        return {
            "state": state.to_dict(),
            "cost": {
                "total_cost_usd": pipeline.cost_tracker.total_cost_usd,
                "total_tokens": pipeline.cost_tracker.total_tokens,
                "cache_hit_rate": pipeline.cost_tracker.cache_hit_rate,
                "by_stage": pipeline.cost_tracker.summary_by_stage(),
            },
        }

    return app


if __name__ == "__main__":
    sys.exit(cli_main())
