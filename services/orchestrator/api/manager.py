"""Run manager: executes pipelines on a worker pool, persists events, supports cancellation."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from services.orchestrator.api.store import Store
from services.orchestrator.config import load_flow_config
from services.orchestrator.eda.bridge import MODES, EDABridge, normalize_mode
from services.orchestrator.inference.providers.replay import ReplayProvider
from services.orchestrator.pipeline import SiliconForgePipeline
from services.orchestrator.state import FlowState, PipelineStage


class RunRequest(BaseModel):
    spec_text: str = Field(min_length=10)
    profile: str = "replay"
    backend: str = "simulated"
    pdk: str | None = None
    inject_bug: bool = False
    clock_period_ns: float | None = Field(default=None, gt=0)
    max_area_um2: float | None = Field(default=None, gt=0)
    max_power_mw: float | None = Field(default=None, gt=0)
    max_budget_usd: float | None = Field(default=None, gt=0)
    stop_after: str | None = Field(default=None, pattern="^(formal|lec)$")
    parallel_episodes: int | None = Field(default=None, ge=1, le=8)
    max_pnr_episodes: int | None = Field(default=None, ge=1, le=40)
    allow_clock_relax: bool | None = None
    config_path: str = "configs/flow_default.yaml"


class RunManager:
    def __init__(self, store: Store, repo_root: Path, workers: int = 2, runs_root: Path | None = None) -> None:
        self.store = store
        self.repo_root = repo_root
        self.runs_root = runs_root or (repo_root / "runs")
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sf-run")
        self._cancel: dict[str, threading.Event] = {}
        self.store.mark_interrupted()

    def build_pipeline(self, req: RunRequest, settings: dict[str, Any] | None = None) -> SiliconForgePipeline:
        cfg = load_flow_config(req.config_path)
        cfg.eda_mode = normalize_mode(req.backend)
        cfg.inference.profile = req.profile
        cfg.workspace_root = self.runs_root
        if cfg.inference.cache_path:
            cfg.inference.cache_path = str(self.runs_root / "_cache" / "llm.sqlite")
        if req.pdk:
            cfg.pdk = req.pdk
        if req.clock_period_ns:
            cfg.targets["clock_period_ns"] = req.clock_period_ns
        if req.max_area_um2:
            cfg.targets["max_area_um2"] = req.max_area_um2
        if req.max_power_mw:
            cfg.targets["max_power_mw"] = req.max_power_mw
        if req.max_budget_usd:
            cfg.inference.max_budget_usd = req.max_budget_usd
        if req.stop_after:
            cfg.pipeline["stop_after"] = req.stop_after
        if req.parallel_episodes:
            cfg.rl_optimizer["parallel_episodes"] = req.parallel_episodes
        if req.max_pnr_episodes:
            cfg.pipeline["max_pnr_rl_episodes"] = req.max_pnr_episodes
        if req.allow_clock_relax is not None:
            cfg.rl_optimizer["allow_clock_relax"] = req.allow_clock_relax
        settings = settings or {}
        cfg.inference.cli_bridge_enabled = bool(settings.get("cli_bridge_enabled", False))
        if "timing_corner" in settings:
            cfg.pipeline["timing_corner"] = settings["timing_corner"]
        provider = ReplayProvider(inject_initial_bug=req.inject_bug) if req.profile == "replay" else None
        return SiliconForgePipeline(cfg, provider=provider, eda=EDABridge(cfg))

    def start(self, req: RunRequest) -> str:
        if normalize_mode(req.backend) not in MODES:
            raise ValueError(f"backend must be one of {MODES}")
        run_id = f"run_{time.strftime('%m%d')}_{uuid.uuid4().hex[:6]}"
        self.store.create_run(run_id, req.spec_text, req.profile, normalize_mode(req.backend), req.model_dump())
        self._cancel[run_id] = threading.Event()
        self.pool.submit(self._execute, run_id, req)
        return run_id

    def cancel(self, run_id: str) -> bool:
        ev = self._cancel.get(run_id)
        if ev is None:
            return False
        ev.set()
        return True

    # ---------------------------------------------------------------------------------------------

    def _execute(self, run_id: str, req: RunRequest) -> None:
        t0 = time.perf_counter()
        cancel = self._cancel[run_id]
        try:
            self.store.set_status(run_id, "running", "INIT")
            pipeline = self.build_pipeline(req, self.store.get_settings())

            def on_event(ev: dict[str, Any]) -> None:
                self.store.add_event(run_id, ev)
                module = (ev.get("payload") or {}).get("contract", {}).get("module_name")
                if ev["stage"] != "LLM":
                    self.store.update_progress(run_id, ev["stage"], module)

            state = pipeline.run(req.spec_text, run_id=run_id, on_event=on_event, cancel_check=cancel.is_set)
            self._finish(run_id, state, pipeline, time.perf_counter() - t0)
        except Exception as exc:  # noqa: BLE001 - a crashed run must be recorded, never lost
            self.store.add_event(run_id, {"seq": 10_000, "ts": time.time(), "stage": "FAILED", "status": "FAIL",
                                          "detail": f"{type(exc).__name__}: {exc}",
                                          "payload": {"trace": traceback.format_exc()[-3000:]}})
            self.store.finish_run(run_id, "failed", None, None, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}")
        finally:
            self._cancel.pop(run_id, None)

    def _finish(self, run_id: str, state: FlowState, pipeline: SiliconForgePipeline, duration: float) -> None:
        if state.stage == PipelineStage.CANCELLED:
            status = "cancelled"
        elif state.stage == PipelineStage.FAILED or state.error_message:
            status = "failed"
        else:
            status = "succeeded"
        state_dict = state.to_dict()
        # large per-event payloads are already in the events table; keep the state document lean
        state_dict["events"] = [{k: v for k, v in e.items() if k != "payload"} for e in state_dict.get("events", [])]
        cost = pipeline.cost_tracker.snapshot()
        cost.pop("calls_detail", None)
        cost["estimate_profile"] = asdict(pipeline.router.stats) if pipeline.router.stats else {}
        self.store.finish_run(run_id, status, state_dict, cost, duration, state.error_message)
