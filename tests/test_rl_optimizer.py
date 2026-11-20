"""Unit tests for PPA reward shaping, Pareto front, and Hybrid RL Optimizer."""
import tempfile
import unittest
from pathlib import Path

from services.orchestrator.config import load_flow_config
from services.orchestrator.eda.bridge import EDABridge
from services.orchestrator.rl.optimizer import HybridPpaOptimizer
from services.orchestrator.rl.ppa_env import PpaOptimizationEnv
from services.orchestrator.rl.reward import compute_ppa_reward, dominates, update_pareto_front
from services.orchestrator.state import DesignContract, PortSpec, PPAMetrics


class TestRLOptimizer(unittest.TestCase):
    def test_pareto_dominance_and_reward(self) -> None:
        clean_fast = PPAMetrics(0.5, 0.0, 18000.0, 2.0, 1200, 0, True, 0.1)
        dirty_slow = PPAMetrics(-0.4, -2.0, 20000.0, 2.5, 1300, 3, True, 0.1)
        self.assertTrue(dominates(clean_fast, dirty_slow))
        self.assertFalse(dominates(dirty_slow, clean_fast))

        targets = {"max_area_um2": 25000.0, "max_power_mw": 5.0, "min_wns_ns": 0.0}
        weights = {"timing_wns": 4.0, "timing_tns": 1.5, "area": 1.5, "power": 1.5, "drc_penalty": 5.0}
        r1 = compute_ppa_reward(clean_fast, targets, weights)
        r2 = compute_ppa_reward(dirty_slow, targets, weights)
        self.assertGreater(r1, r2)

        front = update_pareto_front([dirty_slow], clean_fast)
        self.assertEqual(len(front), 1)
        self.assertEqual(front[0].drc_violations, 0)

    def test_hybrid_optimizer_converges_to_clean_signoff(self) -> None:
        cfg = load_flow_config()
        eda = EDABridge(cfg)
        contract = DesignContract(
            module_name="axi_lite_alu",
            clock_port="clk",
            reset_port="rst_n",
            reset_active_low=True,
            reset_sync=False,
            ports=[PortSpec("clk", "input", 1)],
            functional_requirements=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            env = PpaOptimizationEnv(eda, Path(tmp), contract, cfg.targets, cfg.rl_optimizer["reward_weights"])
            opt = HybridPpaOptimizer(num_actions=env.num_actions)
            episodes, pareto, best = opt.optimize(env, max_episodes=6)
            self.assertEqual(len(episodes), 6)
            self.assertGreaterEqual(len(pareto), 1)
            self.assertGreaterEqual(best.wns_ns, 0.0)
            self.assertEqual(best.drc_violations, 0)
            self.assertTrue(best.lvs_clean)


if __name__ == "__main__":
    unittest.main()
