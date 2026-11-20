"""Unit tests for ModelRouter tier escalation, semantic caching, and CostTracker circuit breaker."""
import unittest

from services.orchestrator.config import load_flow_config
from services.orchestrator.inference.cost_tracker import BudgetExceededError, CostTracker
from services.orchestrator.inference.router import ModelRouter


class TestRouterAndCost(unittest.TestCase):
    def test_tier_escalation_and_semantic_cache(self) -> None:
        cfg = load_flow_config()
        tracker = CostTracker(max_budget_usd=5.0, max_total_tokens=100_000)
        router = ModelRouter(cfg.inference, tracker)

        self.assertEqual(router.select_tier("lint_fix", retry_attempt=0), "local_fast")
        self.assertEqual(router.select_tier("lint_fix", retry_attempt=1), "balanced")
        self.assertEqual(router.select_tier("lint_fix", retry_attempt=2), "frontier_reasoning")

        # Two identical calls should trigger semantic cache hit on call 2
        r1 = router.complete("ARCHITECT", "architecture_contract", "Micro-Architect", "Build ALU")
        r2 = router.complete("ARCHITECT", "architecture_contract", "Micro-Architect", "Build ALU")
        self.assertEqual(r1.content, r2.content)
        self.assertGreater(tracker.cache_hit_rate, 0.0)

    def test_budget_circuit_breaker(self) -> None:
        tracker = CostTracker(max_budget_usd=0.001, max_total_tokens=10_000)
        with self.assertRaises(BudgetExceededError):
            tracker.record("RTL_GEN", "frontier_reasoning", "pro", 1000, 1000, 5.0, 15.0)


if __name__ == "__main__":
    unittest.main()
