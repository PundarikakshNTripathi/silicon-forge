"""Per-stage token and USD cost ledger with hard budget circuit breakers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


class BudgetExceededError(RuntimeError):
    """Raised when an agent run exceeds configured USD or token ceilings."""


@dataclass
class UsageRecord:
    stage: str
    tier: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cached: bool = False


@dataclass
class CostTracker:
    max_budget_usd: float = 15.0
    max_total_tokens: int = 500_000
    records: List[UsageRecord] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(r.cost_usd for r in self.records), 6)

    @property
    def total_tokens(self) -> int:
        return sum(r.prompt_tokens + r.completion_tokens for r in self.records)

    @property
    def cache_hit_rate(self) -> float:
        if not self.records:
            return 0.0
        hits = sum(1 for r in self.records if r.cached)
        return round(hits / len(self.records), 4)

    def check_budget(self) -> None:
        if self.total_cost_usd > self.max_budget_usd:
            raise BudgetExceededError(
                f"USD budget exceeded: ${self.total_cost_usd:.4f} > ${self.max_budget_usd:.4f}"
            )
        if self.total_tokens > self.max_total_tokens:
            raise BudgetExceededError(
                f"Token budget exceeded: {self.total_tokens} > {self.max_total_tokens}"
            )

    def record(
        self,
        stage: str,
        tier: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        input_cost_per_1m: float,
        output_cost_per_1m: float,
        cached: bool = False,
    ) -> UsageRecord:
        cost = 0.0 if cached else (
            (prompt_tokens / 1_000_000.0) * input_cost_per_1m
            + (completion_tokens / 1_000_000.0) * output_cost_per_1m
        )
        rec = UsageRecord(
            stage=stage,
            tier=tier,
            model=model,
            prompt_tokens=0 if cached else prompt_tokens,
            completion_tokens=0 if cached else completion_tokens,
            cost_usd=round(cost, 6),
            cached=cached,
        )
        self.records.append(rec)
        self.check_budget()
        return rec

    def summary_by_stage(self) -> Dict[str, Dict[str, float]]:
        summary: Dict[str, Dict[str, float]] = {}
        for r in self.records:
            bucket = summary.setdefault(r.stage, {"tokens": 0, "cost_usd": 0.0, "calls": 0, "cache_hits": 0})
            bucket["tokens"] += r.prompt_tokens + r.completion_tokens
            bucket["cost_usd"] = round(bucket["cost_usd"] + r.cost_usd, 6)
            bucket["calls"] += 1
            bucket["cache_hits"] += 1 if r.cached else 0
        return summary
