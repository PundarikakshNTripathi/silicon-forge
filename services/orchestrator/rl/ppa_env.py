"""Gymnasium-compatible RTL-to-GDSII PPA Optimization Environment."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from services.orchestrator.eda.bridge import EDABridge
from services.orchestrator.rl.reward import compute_ppa_reward
from services.orchestrator.state import DesignContract, PPAMetrics


# Discrete-combinatorial OpenROAD/LibreLane knob action space
ACTION_SPACE: List[Dict[str, Any]] = [
    {"SYNTH_STRATEGY": "AREA 0",  "FP_CORE_UTIL": 45, "PL_TARGET_DENSITY_PCT": 60, "GRT_ADJUSTMENT": 0.30},
    {"SYNTH_STRATEGY": "AREA 2",  "FP_CORE_UTIL": 52, "PL_TARGET_DENSITY_PCT": 65, "GRT_ADJUSTMENT": 0.30},
    {"SYNTH_STRATEGY": "DELAY 0", "FP_CORE_UTIL": 45, "PL_TARGET_DENSITY_PCT": 58, "GRT_ADJUSTMENT": 0.30},
    {"SYNTH_STRATEGY": "DELAY 4", "FP_CORE_UTIL": 42, "PL_TARGET_DENSITY_PCT": 55, "GRT_ADJUSTMENT": 0.35},
    {"SYNTH_STRATEGY": "DELAY 4", "FP_CORE_UTIL": 50, "PL_TARGET_DENSITY_PCT": 62, "GRT_ADJUSTMENT": 0.30},
    {"SYNTH_STRATEGY": "DELAY 2", "FP_CORE_UTIL": 38, "PL_TARGET_DENSITY_PCT": 52, "GRT_ADJUSTMENT": 0.35},
    {"SYNTH_STRATEGY": "AREA 3",  "FP_CORE_UTIL": 65, "PL_TARGET_DENSITY_PCT": 75, "GRT_ADJUSTMENT": 0.20},
    {"SYNTH_STRATEGY": "DELAY 4", "FP_CORE_UTIL": 48, "PL_TARGET_DENSITY_PCT": 56, "GRT_ADJUSTMENT": 0.32},
]


class PpaOptimizationEnv:
    def __init__(
        self,
        eda_bridge: EDABridge,
        run_dir: Path,
        contract: DesignContract,
        targets: Dict[str, float],
        reward_weights: Dict[str, float],
    ) -> None:
        self.eda_bridge = eda_bridge
        self.run_dir = run_dir
        self.contract = contract
        self.targets = targets
        self.reward_weights = reward_weights
        self.episode = 0

    @property
    def num_actions(self) -> int:
        return len(ACTION_SPACE)

    def step(self, action_idx: int) -> Tuple[PPAMetrics, float]:
        self.episode += 1
        knobs = dict(ACTION_SPACE[action_idx % len(ACTION_SPACE)])
        knobs["CLOCK_PERIOD"] = self.targets.get("clock_period_ns", self.contract.target_clock_ns)
        metrics = self.eda_bridge.run_physical_implementation(
            run_dir=self.run_dir,
            contract=self.contract,
            knobs=knobs,
            episode_idx=self.episode,
        )
        reward = compute_ppa_reward(metrics, self.targets, self.reward_weights)
        metrics.reward = reward
        return metrics, reward
