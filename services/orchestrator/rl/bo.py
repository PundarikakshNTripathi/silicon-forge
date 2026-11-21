# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gaussian-process Bayesian optimization over place-and-route knobs, with the baselines it has to beat.

Why this exists: the earlier optimizer was a stateless bandit over eight hand-written recipes. It has no model of how the knobs
affect the result and had never been compared with anything. This module adds

* a search space of continuous, integer and categorical knobs (`Dim`, `Space`);
* three samplers that need no model: uniform random, Latin hypercube, and the user's default point;
* `GPBayesOptimizer`: a Gaussian-process surrogate (Matern 5/2, lengthscale chosen by marginal likelihood) with expected
  improvement, numpy only.

Whether the GP wins is an empirical question: `benchmarks/bench_ppa.py` runs all of them on the real flow with several seeds
and reports best-so-far curves with intervals. Nothing here claims it does.

Categorical knobs are encoded by their position in `choices`, which is reasonable only when the choices are ordered (as the
`SYNTH_STRATEGY` list is, from area-oriented to delay-oriented). Floats are rounded to the knob's `step` so that proposals repeat
exactly and the flow cache can answer them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Dim:
    name: str
    kind: str  # "float" | "int" | "choice"
    low: float = 0.0
    high: float = 1.0
    step: float = 0.0  # rounding grid for floats (0 = none)
    choices: tuple[Any, ...] = ()

    def decode(self, u: float) -> Any:
        u = min(1.0, max(0.0, float(u)))
        if self.kind == "choice":
            return self.choices[min(len(self.choices) - 1, int(u * len(self.choices)))]
        v = self.low + u * (self.high - self.low)
        if self.kind == "int":
            return int(round(v))
        if self.step:
            v = round(round(v / self.step) * self.step, 10)
        return round(min(self.high, max(self.low, v)), 6)

    def encode(self, value: Any) -> float:
        if self.kind == "choice":
            return (self.choices.index(value) + 0.5) / len(self.choices)
        span = (self.high - self.low) or 1.0
        return min(1.0, max(0.0, (float(value) - self.low) / span))


@dataclass
class Space:
    dims: list[Dim] = field(default_factory=list)

    def decode(self, u: np.ndarray) -> dict[str, Any]:
        return {d.name: d.decode(x) for d, x in zip(self.dims, u, strict=True)}

    def encode(self, knobs: dict[str, Any]) -> np.ndarray:
        return np.array([d.encode(knobs[d.name]) for d in self.dims])

    def canonical(self, u: np.ndarray) -> np.ndarray:
        """Snap a point to the grid the knobs are actually evaluated on."""
        return self.encode(self.decode(u))


#: knobs that matter for a small digital block on the open flow; every name is a real LibreLane variable
DEFAULT_SPACE = Space([
    Dim("FP_CORE_UTIL", "float", 25.0, 65.0, step=1.0),
    Dim("PL_TARGET_DENSITY_PCT", "float", 40.0, 85.0, step=1.0),
    Dim("GRT_ADJUSTMENT", "float", 0.10, 0.40, step=0.01),
    Dim("SYNTH_STRATEGY", "choice", choices=("AREA 0", "AREA 1", "AREA 2", "AREA 3", "DELAY 0", "DELAY 1", "DELAY 2", "DELAY 3", "DELAY 4")),
])


# ------------------------------------------------------------------------------------------------ samplers


def random_points(space: Space, n: int, seed: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    return [space.decode(space.canonical(rng.random(len(space.dims)))) for _ in range(n)]


def latin_hypercube(space: Space, n: int, seed: int) -> list[dict[str, Any]]:
    """One point per stratum in every dimension: a space-filling design that needs no model."""
    rng = np.random.default_rng(seed)
    d = len(space.dims)
    u = np.empty((n, d))
    for j in range(d):
        u[:, j] = (rng.permutation(n) + rng.random(n)) / n
    return [space.decode(space.canonical(row)) for row in u]


# ---------------------------------------------------------------------------------------------- GP and EI


def _matern52(a: np.ndarray, b: np.ndarray, ls: float) -> np.ndarray:
    d = np.sqrt(np.maximum(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1), 0.0)) / ls
    s5 = math.sqrt(5.0)
    return (1.0 + s5 * d + 5.0 / 3.0 * d**2) * np.exp(-s5 * d)


class GP:
    """Zero-mean GP on standardized targets, one shared lengthscale picked from a small grid by marginal likelihood."""

    GRID = (0.12, 0.2, 0.35, 0.6, 1.0)

    def __init__(self, noise: float = 1e-3) -> None:
        self.noise = noise
        self.x: np.ndarray = np.empty((0, 0))
        self.l_chol: np.ndarray = np.empty((0, 0))
        self.alpha: np.ndarray = np.empty(0)
        self.ls = 0.35
        self.mu = 0.0
        self.sd = 1.0

    def fit(self, x: np.ndarray, y: np.ndarray) -> GP:
        self.x = x
        self.mu = float(np.mean(y))
        self.sd = float(np.std(y)) or 1.0
        ys = (y - self.mu) / self.sd
        best = None
        for ls in self.GRID:
            k = _matern52(x, x, ls) + (self.noise + 1e-8) * np.eye(len(x))
            try:
                chol = np.linalg.cholesky(k)
            except np.linalg.LinAlgError:
                continue
            alpha = np.linalg.solve(chol.T, np.linalg.solve(chol, ys))
            ll = -0.5 * float(ys @ alpha) - float(np.log(np.diag(chol)).sum())
            if best is None or ll > best[0]:
                best = (ll, ls, chol, alpha)
        if best is None:  # numerically singular for every lengthscale: fall back to a very smooth, noisy fit
            self.ls, k = 1.0, _matern52(x, x, 1.0) + 1e-2 * np.eye(len(x))
            chol = np.linalg.cholesky(k)
            self.l_chol, self.alpha = chol, np.linalg.solve(chol.T, np.linalg.solve(chol, ys))
        else:
            _, self.ls, self.l_chol, self.alpha = best
        return self

    def predict(self, xs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ks = _matern52(xs, self.x, self.ls)
        mean = ks @ self.alpha
        v = np.linalg.solve(self.l_chol, ks.T)
        var = np.maximum(1.0 - (v**2).sum(0), 1e-12)
        return mean * self.sd + self.mu, np.sqrt(var) * self.sd


def expected_improvement(mean: np.ndarray, std: np.ndarray, best: float, xi: float = 0.01) -> np.ndarray:
    """EI for maximization. `xi` is a small exploration margin in the units of the objective."""
    z = (mean - best - xi) / np.maximum(std, 1e-12)
    pdf = np.exp(-0.5 * z**2) / math.sqrt(2 * math.pi)
    cdf = 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))
    return np.where(std > 1e-12, (mean - best - xi) * cdf + std * pdf, 0.0)


class GPBayesOptimizer:
    """Ask/tell. The first `n_init` proposals are a Latin hypercube; after that each proposal maximizes expected improvement
    over a pool of random candidates plus local perturbations of the best points seen. Points already evaluated are never
    proposed again (they would cost a cached lookup and teach nothing)."""

    def __init__(self, space: Space = DEFAULT_SPACE, seed: int = 0, n_init: int = 3, pool: int = 1500) -> None:
        self.space, self.n_init, self.pool = space, n_init, pool
        self.seed = seed
        self._rng = np.random.default_rng(seed)
        self._init = [space.encode(k) for k in latin_hypercube(space, n_init, seed)]
        self.xs: list[np.ndarray] = []
        self.ys: list[float] = []  # rewards as observed, for reporting
        self.failed: list[bool] = []
        self.last_model: dict[str, float] = {}

    def _seen(self, u: np.ndarray) -> bool:
        return any(np.allclose(u, x, atol=1e-9) for x in self.xs)

    def model_targets(self) -> np.ndarray:
        """What the surrogate is fitted to. A failed run (the flow did not complete) has a reward far below anything real; fitted
        as it is, it would set the scale of the whole model. It is imputed as slightly worse than the worst valid result, which
        still tells the model to avoid that region without wrecking its resolution where the results actually differ."""
        valid = [y for y, f in zip(self.ys, self.failed, strict=True) if not f]
        if not valid:
            return np.zeros(len(self.ys))
        floor = min(valid) - max(1.0, 0.25 * (max(valid) - min(valid)))
        return np.array([floor if f else y for y, f in zip(self.ys, self.failed, strict=True)])

    def suggest(self) -> dict[str, Any]:
        if len(self.xs) < self.n_init:
            return self.space.decode(self._init[len(self.xs)])
        targets = self.model_targets()
        gp = GP().fit(np.array(self.xs), targets)
        d = len(self.space.dims)
        cands = [self.space.canonical(r) for r in self._rng.random((self.pool, d))]
        order = np.argsort(targets)[::-1][:3]
        for i in order:  # local search around the best points
            for _ in range(self.pool // 6):
                cands.append(self.space.canonical(np.clip(self.xs[i] + self._rng.normal(0, 0.08, d), 0.0, 1.0)))
        cands = [c for c in cands if not self._seen(c)] or [self.space.canonical(self._rng.random(d))]
        arr = np.array(cands)
        mean, std = gp.predict(arr)
        ei = expected_improvement(mean, std, float(targets.max()))
        k = int(np.argmax(ei))
        self.last_model = {"lengthscale": gp.ls, "ei": float(ei[k]), "pred_mean": float(mean[k]), "pred_std": float(std[k])}
        return self.space.decode(arr[k])

    def observe(self, knobs: dict[str, Any], reward: float, failed: bool = False) -> None:
        self.xs.append(self.space.canonical(self.space.encode(knobs)))
        self.ys.append(float(reward))
        self.failed.append(bool(failed))

