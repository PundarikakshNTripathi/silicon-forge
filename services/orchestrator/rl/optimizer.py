"""Hybrid Clipped-PPO + UCB Exploration + Evolutionary Pareto-Front PPA Optimizer."""
from __future__ import annotations

import math
from typing import List, Tuple
import numpy as np

from services.orchestrator.rl.ppa_env import PpaOptimizationEnv
from services.orchestrator.rl.reward import update_pareto_front
from services.orchestrator.state import PPAMetrics


class HybridPpaOptimizer:
    """Combines policy-gradient clipped updates (PPO-style) with UCB exploration and Pareto elitism."""

    def __init__(
        self,
        num_actions: int,
        learning_rate: float = 0.15,
        ppo_clip_eps: float = 0.2,
        exploration_c: float = 1.414,
        pareto_archive_size: int = 8,
    ) -> None:
        self.num_actions = num_actions
        self.lr = learning_rate
        self.clip_eps = ppo_clip_eps
        self.exploration_c = exploration_c
        self.pareto_archive_size = pareto_archive_size

        self.logits = np.zeros(num_actions, dtype=np.float64)
        self.counts = np.zeros(num_actions, dtype=np.int64)
        self.value_est = np.zeros(num_actions, dtype=np.float64)
        self.baseline = 0.0
        self.total_steps = 0

    def policy_probs(self) -> np.ndarray:
        shifted = self.logits - np.max(self.logits)
        exp_l = np.exp(shifted)
        return exp_l / np.sum(exp_l)

    def select_action(self) -> Tuple[int, float]:
        probs = self.policy_probs()
        # UCB bonus ensures unvisited synthesis/P&R recipes are systematically explored
        ucb_scores = np.zeros(self.num_actions, dtype=np.float64)
        for a in range(self.num_actions):
            if self.counts[a] == 0:
                ucb_scores[a] = 1e6 + probs[a]
            else:
                bonus = self.exploration_c * math.sqrt(math.log(self.total_steps + 1) / self.counts[a])
                ucb_scores[a] = self.value_est[a] + bonus + 0.25 * probs[a]
        action = int(np.argmax(ucb_scores))
        return action, float(probs[action])

    def update(self, action: int, old_prob: float, reward: float) -> None:
        self.total_steps += 1
        self.counts[action] += 1
        n = self.counts[action]
        self.value_est[action] += (reward - self.value_est[action]) / n

        advantage = reward - self.baseline
        self.baseline += 0.2 * advantage

        new_prob = float(self.policy_probs()[action])
        ratio = new_prob / max(1e-8, old_prob)
        clipped_ratio = float(np.clip(ratio, 1.0 - self.clip_eps, 1.0 + self.clip_eps))
        surrogate_grad = min(ratio * advantage, clipped_ratio * advantage)
        self.logits[action] += self.lr * surrogate_grad

    def optimize(self, env: PpaOptimizationEnv, max_episodes: int = 6) -> Tuple[List[PPAMetrics], List[PPAMetrics], PPAMetrics]:
        episodes: List[PPAMetrics] = []
        pareto: List[PPAMetrics] = []
        for _ in range(max_episodes):
            action, old_prob = self.select_action()
            metrics, reward = env.step(action)
            self.update(action, old_prob, reward)
            episodes.append(metrics)
            pareto = update_pareto_front(pareto, metrics, max_size=self.pareto_archive_size)

        best = max(episodes, key=lambda m: m.reward)
        return episodes, pareto, best
