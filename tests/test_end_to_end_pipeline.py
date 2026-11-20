"""End-to-end integration test for SiliconForge Spec -> RTL -> Formal Repair -> LEC -> RL PPA -> Signoff."""
import tempfile
import unittest
from pathlib import Path

from services.orchestrator.config import load_flow_config
from services.orchestrator.inference.providers import MockChipDesignLLM
from services.orchestrator.pipeline import SiliconForgePipeline
from services.orchestrator.state import PipelineStage


class TestEndToEndPipeline(unittest.TestCase):
    def test_full_flow_with_formal_counterexample_repair(self) -> None:
        cfg = load_flow_config()
        spec_path = Path(__file__).resolve().parents[1] / "hardware" / "examples" / "axi_lite_alu" / "spec.md"
        spec_text = spec_path.read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            cfg.workspace_root = Path(tmp)
            provider = MockChipDesignLLM(inject_initial_bug=True)
            pipeline = SiliconForgePipeline(cfg, provider=provider)
            state = pipeline.run(spec_text, run_id="test_e2e_run")

            self.assertEqual(state.stage, PipelineStage.SIGNOFF)
            self.assertTrue(state.signoff_passed, f"Signoff failed: {state.error_message}")
            self.assertTrue(state.lec_passed)
            # Formal verification should fail on iter 1 (injected bug) and converge on iter 2
            self.assertEqual(len(state.formal_history), 2)
            self.assertFalse(state.formal_history[0].passed)
            self.assertTrue(state.formal_history[1].passed)
            # RL PPA should find a WNS >= 0, DRC == 0, LVS clean solution
            self.assertIsNotNone(state.best_ppa)
            self.assertGreaterEqual(state.best_ppa.wns_ns, 0.0)
            self.assertEqual(state.best_ppa.drc_violations, 0)
            self.assertTrue(state.best_ppa.lvs_clean)
            self.assertTrue(Path(state.gds_path).exists())
            self.assertTrue((Path(tmp) / "test_e2e_run" / "signoff_report.json").exists())


if __name__ == "__main__":
    unittest.main()
