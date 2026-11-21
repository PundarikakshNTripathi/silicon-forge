# SPDX-License-Identifier: AGPL-3.0-or-later
"""RTL-to-GDSII PPA optimization environment with an on-disk EDA result cache."""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from services.orchestrator.eda.bridge import EDABridge
from services.orchestrator.eda.errors import ToolRunError
from services.orchestrator.rl.reward import compute_ppa_reward
from services.orchestrator.state import DesignContract, PPAMetrics

#: Reward of a flow that did not complete. The reward is unbounded below (a flow that misses timing by 3 ns already scores
#: about -37), so this sits below any result a completed flow is likely to get.
FAILED_FLOW_REWARD = -50.0


def failed_flow_metrics(knobs: dict[str, Any], message: str, mode: str) -> PPAMetrics:
    """The record of a flow that failed: no area, power or layout, worst-case slack, not clean. `extra["flow_failed"]` holds the
    tool's last lines so the failure can be read later."""
    return PPAMetrics(wns_ns=-99.0, tns_ns=-99.0, area_um2=0.0, total_power_mw=0.0, cell_count=0, drc_violations=0,
                      lvs_clean=False, knobs=dict(knobs), mode=mode, extra={"flow_failed": message[-400:]})

# Discrete-combinatorial OpenROAD/LibreLane knob action space (every key is a real LibreLane variable)
BASE_ACTIONS: list[dict[str, Any]] = [
    {"SYNTH_STRATEGY": "AREA 0",  "FP_CORE_UTIL": 45, "PL_TARGET_DENSITY_PCT": 60, "GRT_ADJUSTMENT": 0.30},
    {"SYNTH_STRATEGY": "AREA 2",  "FP_CORE_UTIL": 52, "PL_TARGET_DENSITY_PCT": 65, "GRT_ADJUSTMENT": 0.30},
    {"SYNTH_STRATEGY": "DELAY 0", "FP_CORE_UTIL": 45, "PL_TARGET_DENSITY_PCT": 58, "GRT_ADJUSTMENT": 0.30},
    {"SYNTH_STRATEGY": "DELAY 4", "FP_CORE_UTIL": 42, "PL_TARGET_DENSITY_PCT": 55, "GRT_ADJUSTMENT": 0.35},
    {"SYNTH_STRATEGY": "DELAY 4", "FP_CORE_UTIL": 50, "PL_TARGET_DENSITY_PCT": 62, "GRT_ADJUSTMENT": 0.30},
    {"SYNTH_STRATEGY": "DELAY 2", "FP_CORE_UTIL": 38, "PL_TARGET_DENSITY_PCT": 52, "GRT_ADJUSTMENT": 0.35},
    {"SYNTH_STRATEGY": "AREA 3",  "FP_CORE_UTIL": 65, "PL_TARGET_DENSITY_PCT": 75, "GRT_ADJUSTMENT": 0.20},
    {"SYNTH_STRATEGY": "DELAY 4", "FP_CORE_UTIL": 48, "PL_TARGET_DENSITY_PCT": 56, "GRT_ADJUSTMENT": 0.32},
]
ACTION_SPACE = BASE_ACTIONS  # backward-compatible name

CLOCK_RELAX_SCALES = (1.15, 1.3, 1.5)


def build_action_space(allow_clock_relax: bool) -> list[dict[str, Any]]:
    """Base recipes; optionally add clock-relaxed variants of the timing-oriented recipes."""
    actions = [dict(a) for a in BASE_ACTIONS]
    if allow_clock_relax:
        timing_recipes = [a for a in BASE_ACTIONS if str(a["SYNTH_STRATEGY"]).startswith("DELAY")][:2]
        for scale in CLOCK_RELAX_SCALES:
            for recipe in timing_recipes:
                actions.append({**recipe, "CLOCK_SCALE": scale})
    return actions


def cache_key(rtl: str, knobs: dict[str, Any], pdk: str, mode: str) -> str:
    raw = json.dumps({"rtl": rtl, "knobs": knobs, "pdk": pdk, "mode": mode}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class PpaOptimizationEnv:
    def __init__(
        self,
        eda_bridge: EDABridge,
        run_dir: Path,
        contract: DesignContract,
        targets: dict[str, float],
        reward_weights: dict[str, float],
        rtl_source: str = "",
        allow_clock_relax: bool = False,
        cache_dir: Path | None = None,
        quality_only: bool = False,
    ) -> None:
        self.eda_bridge = eda_bridge
        self.run_dir = run_dir
        self.contract = contract
        self.targets = targets
        self.reward_weights = reward_weights
        self.rtl_source = rtl_source
        self.actions = build_action_space(allow_clock_relax)
        self.cache_dir = cache_dir
        #: drop the runtime term: it depends on machine load, which would make equal results score differently
        self.quality_only = quality_only
        self._lock = threading.Lock()
        self.episode = 0
        self.cache_hits = 0

    @property
    def num_actions(self) -> int:
        return len(self.actions)

    def knobs_for(self, action_idx: int) -> dict[str, Any]:
        knobs = dict(self.actions[action_idx % len(self.actions)])
        base_clk = float(self.targets.get("clock_period_ns", self.contract.target_clock_ns))
        knobs["CLOCK_PERIOD"] = round(base_clk * float(knobs.pop("CLOCK_SCALE", 1.0)), 3)
        return knobs

    def next_episode(self) -> int:
        with self._lock:
            self.episode += 1
            return self.episode

    def step(self, action_idx: int) -> tuple[PPAMetrics, float]:
        return self.evaluate(action_idx, self.next_episode())

    def evaluate(self, action_idx: int, episode_idx: int) -> tuple[PPAMetrics, float]:
        """Run (or fetch from cache) one P&R episode. Safe to call from worker threads."""
        return self.evaluate_knobs(self.knobs_for(action_idx), episode_idx)

    def evaluate_knobs(self, knobs: dict[str, Any], episode_idx: int) -> tuple[PPAMetrics, float]:
        """As `evaluate`, for an arbitrary knob dict (the Bayesian optimizer and the baselines propose knobs, not recipe
        indices). `CLOCK_PERIOD` defaults to the target clock."""
        knobs = dict(knobs)
        knobs.setdefault("CLOCK_PERIOD", round(float(self.targets.get("clock_period_ns", self.contract.target_clock_ns)), 3))
        key = cache_key(self.rtl_source, knobs, self.eda_bridge.config.pdk, self.eda_bridge.mode)
        metrics = self._cache_get(key)
        if metrics is None:
            try:
                metrics = self.eda_bridge.run_physical_implementation(
                    run_dir=self.run_dir,
                    contract=self.contract,
                    rtl_source=self.rtl_source,
                    knobs=knobs,
                    episode_idx=episode_idx,
                )
            except ToolRunError as exc:
                # Aggressive knobs make the flow fail (for example detailed placement at too high a utilization). That is
                # information for a search, not a reason to abort it: record the failure, score it far below any real
                # result, and cache it so the same point is not paid for twice.
                metrics = failed_flow_metrics(knobs, str(exc), self.eda_bridge.mode)
            self._cache_put(key, metrics)
        else:
            with self._lock:
                self.cache_hits += 1
            self._materialize(metrics, episode_idx)
        if metrics.extra.get("flow_failed"):
            metrics.reward = FAILED_FLOW_REWARD
            return metrics, FAILED_FLOW_REWARD
        weights = {**self.reward_weights, "runtime_penalty": 0.0} if self.quality_only else self.reward_weights
        reward = compute_ppa_reward(metrics, self.targets, weights)
        metrics.reward = reward
        return metrics, reward

    def _materialize(self, metrics: PPAMetrics, episode_idx: int) -> None:
        """A cached episode's artifacts live in the run that produced it: copy the final outputs into this run."""
        import shutil

        src_dir = Path(metrics.run_dir) if metrics.run_dir else None
        if src_dir is None or not src_dir.exists():
            return
        dest = self.run_dir / f"pnr_ep_{episode_idx:02d}"
        if dest.resolve() == src_dir.resolve():
            return
        final_src = src_dir / "runs" / "sf" / "final"
        if final_src.exists():
            final_dst = dest / "runs" / "sf" / "final"
            for sub in ("gds", "render"):
                if (final_src / sub).exists():
                    shutil.copytree(final_src / sub, final_dst / sub, dirs_exist_ok=True)
            if (final_src / "metrics.json").exists():
                final_dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(final_src / "metrics.json", final_dst / "metrics.json")
            gds = next((final_dst / "gds").glob("*.gds"), None) if (final_dst / "gds").exists() else None
            metrics.gds_path = str(gds) if gds else metrics.gds_path
            render = next((final_dst / "render").glob("*.png"), None) if (final_dst / "render").exists() else None
            if render:
                metrics.extra["render_png"] = str(render)
        metrics.run_dir = str(dest)

    # --- on-disk result cache (real flows only; simulated results are cheap) --------------------------

    def _cache_path(self, key: str) -> Path | None:
        if self.cache_dir is None or self.eda_bridge.mode == "simulated":
            return None
        return self.cache_dir / f"{key}.json"

    def _cache_get(self, key: str) -> PPAMetrics | None:
        path = self._cache_path(key)
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("gds_path") and not Path(data["gds_path"]).exists():
                return None  # artifacts were pruned: recompute
            data["cached"] = True
            return PPAMetrics(**data)
        except (OSError, ValueError, TypeError):
            return None

    def _cache_put(self, key: str, metrics: PPAMetrics) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(metrics), indent=2), encoding="utf-8")
