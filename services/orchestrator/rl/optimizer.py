# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hybrid Clipped-PPO + UCB Exploration + Evolutionary Pareto-Front PPA Optimizer.

Supports batched, parallel episode evaluation (real P&R episodes take minutes) and early stopping once a
signoff-clean recipe is found.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from services.orchestrator.rl.ppa_env import PpaOptimizationEnv
from services.orchestrator.rl.reward import update_pareto_front
from services.orchestrator.state import PPAMetrics

EpisodeCallback = Callable[[int, PPAMetrics], None]


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

    def _ucb_scores(self, pending: np.ndarray | None = None) -> np.ndarray:
        probs = self.policy_probs()
        counts = self.counts + (pending if pending is not None else 0)
        scores = np.zeros(self.num_actions, dtype=np.float64)
        for a in range(self.num_actions):
            if counts[a] == 0:
                scores[a] = 1e6 + probs[a]
            else:
                bonus = self.exploration_c * math.sqrt(math.log(self.total_steps + 2) / counts[a])
                scores[a] = self.value_est[a] + bonus + 0.25 * probs[a]
        return scores

    def select_action(self) -> tuple[int, float]:
        # UCB bonus ensures unvisited synthesis/P&R recipes are systematically explored
        action = int(np.argmax(self._ucb_scores()))
        return action, float(self.policy_probs()[action])

    def select_batch(self, k: int) -> list[tuple[int, float]]:
        """Pick up to k distinct actions (treating already-picked ones as pending visits)."""
        pending = np.zeros(self.num_actions, dtype=np.int64)
        probs = self.policy_probs()
        picked: list[tuple[int, float]] = []
        for _ in range(min(k, self.num_actions)):
            scores = self._ucb_scores(pending)
            scores[pending > 0] = -np.inf  # distinct actions within one batch
            a = int(np.argmax(scores))
            pending[a] += 1
            picked.append((a, float(probs[a])))
        return picked

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

    def optimize(
        self,
        env: PpaOptimizationEnv,
        max_episodes: int = 6,
        batch_size: int = 1,
        is_clean: Callable[[PPAMetrics], bool] | None = None,
        on_episode: EpisodeCallback | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[list[PPAMetrics], list[PPAMetrics], PPAMetrics]:
        """Run up to `max_episodes` episodes in batches of `batch_size`; stop early on a clean result."""
        episodes: list[PPAMetrics] = []
        pareto: list[PPAMetrics] = []
        done = 0
        while done < max_episodes:
            if cancel_check is not None and cancel_check():
                break
            k = min(batch_size, max_episodes - done)
            picks = self.select_batch(k) if k > 1 else [self.select_action()]
            eps_idx = [env.next_episode() for _ in picks]
            if len(picks) == 1:
                results = [env.evaluate(picks[0][0], eps_idx[0])]
            else:
                with ThreadPoolExecutor(max_workers=len(picks)) as pool:
                    futures = [pool.submit(env.evaluate, a, e) for (a, _), e in zip(picks, eps_idx, strict=True)]
                    results = [f.result() for f in futures]
            for (action, old_prob), (metrics, reward) in zip(picks, results, strict=True):
                self.update(action, old_prob, reward)
                episodes.append(metrics)
                pareto = update_pareto_front(pareto, metrics, max_size=self.pareto_archive_size)
                done += 1
                if on_episode is not None:
                    on_episode(done, metrics)
            if is_clean is not None and any(is_clean(m) for m, _ in results):
                break  # signoff-clean recipe found: stop spending EDA time

        if not episodes:
            raise RuntimeError("PPA optimization produced no episodes (cancelled?)")
        best = max(episodes, key=lambda m: m.reward)
        return episodes, pareto, best
