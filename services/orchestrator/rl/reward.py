# SPDX-License-Identifier: AGPL-3.0-or-later
"""Multi-objective PPA reward shaping and Pareto-front dominance utilities."""
from __future__ import annotations

from services.orchestrator.state import PPAMetrics


def compute_ppa_reward(
    metrics: PPAMetrics,
    targets: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Computes a smooth, signoff-aware scalar reward for RL PPA optimization."""
    max_area = max(1.0, float(targets.get("max_area_um2", 25000.0)))
    max_power = max(0.1, float(targets.get("max_power_mw", 5.0)))
    min_wns = float(targets.get("min_wns_ns", 0.0))

    # Timing: strong penalty for negative slack, gentle bonus for positive margin
    wns_margin = metrics.wns_ns - min_wns
    timing_term = (wns_margin * 2.5) if wns_margin < 0 else min(1.5, wns_margin * 0.5)
    tns_term = max(-5.0, metrics.tns_ns * 0.25)

    # Area & Power: normalized savings relative to budget
    area_term = (max_area - metrics.area_um2) / max_area
    power_term = (max_power - metrics.total_power_mw) / max_power

    # Physical signoff hard penalties
    drc_pen = -float(metrics.drc_violations) * float(weights.get("drc_penalty", 5.0))
    lvs_pen = 0.0 if metrics.lvs_clean else -float(weights.get("lvs_penalty", 10.0))
    rt_pen = -min(2.0, metrics.runtime_sec * 0.01) * float(weights.get("runtime_penalty", 0.2))

    reward = (
        float(weights.get("timing_wns", 4.0)) * timing_term
        + float(weights.get("timing_tns", 1.5)) * tns_term
        + float(weights.get("area", 1.5)) * area_term
        + float(weights.get("power", 1.5)) * power_term
        + drc_pen
        + lvs_pen
        + rt_pen
    )
    return round(reward, 4)


def dominates(a: PPAMetrics, b: PPAMetrics) -> bool:
    """Returns True if PPA solution `a` Pareto-dominates `b` (better/equal on all, strictly better on >=1)."""
    a_clean = (a.drc_violations == 0 and a.lvs_clean)
    b_clean = (b.drc_violations == 0 and b.lvs_clean)
    if a_clean and not b_clean:
        return True
    if b_clean and not a_clean:
        return False

    ge = (
        a.wns_ns >= b.wns_ns
        and a.area_um2 <= b.area_um2
        and a.total_power_mw <= b.total_power_mw
        and a.drc_violations <= b.drc_violations
    )
    gt = (
        a.wns_ns > b.wns_ns
        or a.area_um2 < b.area_um2
        or a.total_power_mw < b.total_power_mw
        or a.drc_violations < b.drc_violations
    )
    return ge and gt


def update_pareto_front(front: list[PPAMetrics], candidate: PPAMetrics, max_size: int = 8) -> list[PPAMetrics]:
    if any(dominates(existing, candidate) for existing in front):
        return front
    filtered = [existing for existing in front if not dominates(candidate, existing)]
    filtered.append(candidate)
    filtered.sort(key=lambda m: m.reward, reverse=True)
    return filtered[:max_size]
