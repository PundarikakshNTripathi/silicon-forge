# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end pipeline tests on the simulated backend with the replay provider (offline, CI-safe)."""
from __future__ import annotations

import json

import pytest

from services.orchestrator.eda.bridge import EDABridge
from services.orchestrator.eda.errors import ToolMissingError
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.providers import ReplayMissError, ReplayProvider
from services.orchestrator.pipeline import SiliconForgePipeline
from services.orchestrator.state import PipelineStage
from tests.conftest import EXAMPLES, example_names


def _spec(name: str) -> str:
    return (EXAMPLES / name / "spec.md").read_text(encoding="utf-8")


def test_axi_lite_alu_formal_counterexample_repair(sim_config):
    pipeline = SiliconForgePipeline(sim_config, provider=ReplayProvider(inject_initial_bug=True))
    events: list[dict] = []
    state = pipeline.run(_spec("axi_lite_alu"), run_id="e2e_axi", on_event=events.append)

    assert state.stage == PipelineStage.SIGNOFF, state.error_message
    assert state.signoff_passed and state.lec_passed
    assert state.eda_mode == "simulated"
    # first formal pass fails on the injected bug, second converges after COI-sliced repair
    assert [r.passed for r in state.formal_history] == [False, True]
    assert state.formal_history[0].mode == "simulated"
    assert state.best_ppa is not None and state.best_ppa.mode == "simulated"
    # simulated flows never produce a (fake) GDSII
    assert state.gds_path is None
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert (sim_config.workspace_root / "e2e_axi" / "signoff_report.json").exists()
    assert len(state.rtl_versions) >= 2
    assert all(c["passed"] for c in state.signoff_checks)


@pytest.mark.parametrize("name", example_names())
def test_every_example_runs_clean_when_simulated(sim_config, name):
    pipeline = SiliconForgePipeline(sim_config, provider=ReplayProvider())
    state = pipeline.run(_spec(name), run_id=f"e2e_{name}")
    assert state.signoff_passed, state.error_message
    assert state.contract.module_name == name


def test_unknown_spec_fails_loudly_instead_of_returning_someone_elses_design(sim_config):
    pipeline = SiliconForgePipeline(sim_config, provider=ReplayProvider())
    state = pipeline.run("Design a RISC-V core with caches.", run_id="e2e_unknown")
    assert state.stage == PipelineStage.FAILED
    assert "replay fixture" in (state.error_message or "")
    with pytest.raises(ReplayMissError):
        ReplayProvider().generate("m", "# Role: Principal ASIC Micro-Architect", "a cpu")


def test_budget_exceeded_fails_run_gracefully(sim_config):
    tracker = CostTracker(max_budget_usd=15.0, max_total_tokens=100)  # replay calls are free: trip the token ceiling
    pipeline = SiliconForgePipeline(sim_config, provider=ReplayProvider(), cost_tracker=tracker)
    state = pipeline.run(_spec("axi_lite_alu"), run_id="e2e_budget")
    assert state.stage == PipelineStage.FAILED
    assert "budget" in (state.error_message or "").lower()
    # a failed run still persists its report
    report = json.loads((sim_config.workspace_root / "e2e_budget" / "signoff_report.json").read_text())
    assert report["state"]["stage"] == "FAILED"


def test_cancellation_stops_between_stages(sim_config):
    pipeline = SiliconForgePipeline(sim_config, provider=ReplayProvider())
    calls = {"n": 0}

    def cancel_after_first_stage() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    state = pipeline.run(_spec("axi_lite_alu"), run_id="e2e_cancel", cancel_check=cancel_after_first_stage)
    assert state.stage == PipelineStage.CANCELLED


def test_real_backend_without_tools_raises_instead_of_silently_mocking(sim_config, monkeypatch):
    sim_config.eda_mode = "native"
    bridge = EDABridge(sim_config)
    monkeypatch.setattr(bridge.backend.runner, "has_tool", lambda tool: False)
    bridge.backend._tools_cache = None
    pipeline = SiliconForgePipeline(sim_config, provider=ReplayProvider(), eda=bridge)
    state = pipeline.run(_spec("axi_lite_alu"), run_id="e2e_missing_tools")
    assert state.stage == PipelineStage.FAILED
    assert "missing required tools" in (state.error_message or "")
    with pytest.raises(ToolMissingError):
        bridge.backend.require("formal")
