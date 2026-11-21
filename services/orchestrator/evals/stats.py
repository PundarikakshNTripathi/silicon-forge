# SPDX-License-Identifier: AGPL-3.0-or-later
"""Statistics for model evaluations: the unbiased pass@k estimator, bootstrap intervals and paired comparison.

With n samples per problem of which c pass, pass@k is the chance that at least one of k samples drawn without replacement
passes: 1 - C(n-c, k) / C(n, k) (Chen et al., 2021). Averaging that over problems gives the benchmark score. Intervals come
from resampling problems, because the problems, not the samples, are the unit that varies between runs of a benchmark.
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate of pass@k from `c` passes in `n` samples. Requires 1 <= k <= n."""
    if k < 1 or n < k:
        raise ValueError(f"pass@{k} needs at least {k} samples, got n={n}")
    if c < 0 or c > n:
        raise ValueError(f"c={c} passes out of n={n} samples")
    if n - c < k:
        return 1.0
    # product form avoids huge binomials: prod_{i=n-c+1}^{n} (1 - k/i)
    return 1.0 - math.prod(1.0 - k / i for i in range(n - c + 1, n + 1))


def bootstrap_ci(values: Sequence[float], reps: int = 4000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean of `values`. Deterministic for a given seed."""
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    lo = means[int((alpha / 2) * reps)]
    hi = means[min(reps - 1, int((1 - alpha / 2) * reps))]
    return (lo, hi)


def summarize(samples: Sequence[tuple[int, int]], k: int = 1, seed: int = 0) -> dict:
    """`samples` is one `(n, c)` pair per problem. Returns the mean pass@k, its 95% interval and the problem count.
    Problems with fewer than k samples are skipped and counted."""
    scores, skipped = [], 0
    for n, c in samples:
        if n < k:
            skipped += 1
            continue
        scores.append(pass_at_k(n, c, k))
    mean = sum(scores) / len(scores) if scores else float("nan")
    lo, hi = bootstrap_ci(scores, seed=seed)
    return {"k": k, "mean": mean, "ci95": [lo, hi], "problems": len(scores), "skipped": skipped}


def paired_difference(a: Sequence[float], b: Sequence[float], reps: int = 4000, seed: int = 0) -> dict:
    """Mean of `a - b` over the same problems with a bootstrap interval. If the interval excludes 0 the two arms differ on
    this set; if it contains 0 the evidence does not separate them, whatever the point estimates look like."""
    if len(a) != len(b):
        raise ValueError("paired samples must have the same length")
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    lo, hi = bootstrap_ci(diffs, reps=reps, seed=seed)
    mean = sum(diffs) / len(diffs) if diffs else float("nan")
    return {"mean": mean, "ci95": [lo, hi], "problems": len(diffs), "separated": not (lo <= 0.0 <= hi)}
