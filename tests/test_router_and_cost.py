"""ModelRouter tier escalation, response caching and the CostTracker circuit breaker."""
from __future__ import annotations

import pytest

from services.orchestrator.config import load_flow_config
from services.orchestrator.inference.cost_tracker import BudgetExceededError, CostTracker
from services.orchestrator.inference.providers import BaseLLMProvider, LLMResponse
from services.orchestrator.inference.router import ModelRouter


class EchoProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        self.calls += 1
        return LLMResponse(content=f"echo:{user_prompt}", prompt_tokens=100, completion_tokens=20, model=model)


def test_tier_escalation_and_cache():
    cfg = load_flow_config()
    cfg.inference.profile = "local"  # replay-only profiles intentionally skip the response cache
    cfg.inference.cache_path = None
    tracker = CostTracker(max_budget_usd=5.0, max_total_tokens=100_000)
    provider = EchoProvider()
    router = ModelRouter(cfg.inference, tracker, provider=provider)

    assert router.select_tier("lint_fix", retry_attempt=0) == "local_fast"
    assert router.select_tier("lint_fix", retry_attempt=1) == "balanced"
    assert router.select_tier("lint_fix", retry_attempt=2) == "frontier_reasoning"

    r1 = router.complete("ARCHITECT", "architecture_contract", "sys", "Build ALU")
    r2 = router.complete("ARCHITECT", "architecture_contract", "sys", "Build ALU")
    assert r1.content == r2.content
    assert provider.calls == 1  # second call served from the cache
    assert tracker.cache_hit_rate > 0.0


def test_cache_can_be_bypassed():
    cfg = load_flow_config()
    cfg.inference.profile = "local"  # replay-only profiles intentionally skip the response cache
    cfg.inference.cache_path = None
    provider = EchoProvider()
    router = ModelRouter(cfg.inference, CostTracker(), provider=provider)
    router.complete("S", "rtl_generation", "sys", "x")
    router.complete("S", "rtl_generation", "sys", "x", bypass_cache=True)
    assert provider.calls == 2


def test_budget_circuit_breaker():
    tracker = CostTracker(max_budget_usd=0.001, max_total_tokens=10_000)
    with pytest.raises(BudgetExceededError):
        tracker.record("RTL_GEN", "frontier_reasoning", "pro", 1000, 1000, 5.0, 15.0)
