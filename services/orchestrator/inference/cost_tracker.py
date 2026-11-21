# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-call token, latency and USD ledger with budget circuit breakers and cache-savings accounting."""
from __future__ import annotations

import statistics
import threading
from dataclasses import asdict, dataclass, field


class BudgetExceededError(RuntimeError):
    """Raised when an agent run exceeds configured USD, token or per-stage token ceilings."""


@dataclass
class UsageRecord:
    stage: str
    tier: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cached: bool = False  # served from the local response cache (no provider call)
    provider: str = ""
    cached_tokens: int = 0  # prompt tokens read from a provider/server prefix cache
    cache_write_tokens: int = 0
    latency_s: float = 0.0
    ttft_s: float | None = None
    tokens_per_s: float | None = None
    saved_usd: float = 0.0  # what caching saved vs paying full input price
    local: bool = False
    billing: str = "metered"  # metered | local | subscription | cache


@dataclass
class CostTracker:
    max_budget_usd: float = 15.0
    max_total_tokens: int = 500_000
    stage_token_budgets: dict[str, int] = field(default_factory=dict)
    records: list[UsageRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    # --- totals -------------------------------------------------------------------------------------

    @property
    def total_cost_usd(self) -> float:
        return round(sum(r.cost_usd for r in self.records), 6)

    @property
    def total_tokens(self) -> int:
        return sum(r.prompt_tokens + r.completion_tokens for r in self.records)

    @property
    def cache_hit_rate(self) -> float:
        """Share of calls served from the local response cache."""
        if not self.records:
            return 0.0
        return round(sum(1 for r in self.records if r.cached) / len(self.records), 4)

    @property
    def prefix_cache_ratio(self) -> float:
        """Share of provider-side prompt tokens that were cache reads."""
        prompt = sum(r.prompt_tokens for r in self.records if not r.cached)
        return round(sum(r.cached_tokens for r in self.records) / prompt, 4) if prompt else 0.0

    @property
    def total_saved_usd(self) -> float:
        return round(sum(r.saved_usd for r in self.records), 6)

    # --- budgets ------------------------------------------------------------------------------------

    def check_budget(self, stage: str | None = None) -> None:
        if self.total_cost_usd > self.max_budget_usd:
            raise BudgetExceededError(
                f"USD budget exceeded: ${self.total_cost_usd:.4f} > ${self.max_budget_usd:.4f}"
            )
        if self.total_tokens > self.max_total_tokens:
            raise BudgetExceededError(f"Token budget exceeded: {self.total_tokens} > {self.max_total_tokens}")
        if stage and stage in self.stage_token_budgets:
            used = sum(r.prompt_tokens + r.completion_tokens for r in self.records if r.stage == stage)
            if used > self.stage_token_budgets[stage]:
                raise BudgetExceededError(
                    f"Stage '{stage}' token budget exceeded: {used} > {self.stage_token_budgets[stage]}"
                )

    # --- recording ----------------------------------------------------------------------------------

    def add(self, rec: UsageRecord) -> UsageRecord:
        with self._lock:
            self.records.append(rec)
        self.check_budget(rec.stage)
        return rec

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
        """Legacy flat-price recording (kept for callers that price manually)."""
        cost = 0.0 if cached else (
            (prompt_tokens / 1_000_000.0) * input_cost_per_1m
            + (completion_tokens / 1_000_000.0) * output_cost_per_1m
        )
        return self.add(UsageRecord(
            stage=stage, tier=tier, model=model,
            prompt_tokens=0 if cached else prompt_tokens,
            completion_tokens=0 if cached else completion_tokens,
            cost_usd=round(cost, 6), cached=cached, billing="cache" if cached else "metered",
        ))

    # --- reporting ----------------------------------------------------------------------------------

    def summary_by_stage(self) -> dict[str, dict[str, float]]:
        return self._group(lambda r: r.stage)

    def summary_by_tier(self) -> dict[str, dict[str, float]]:
        return self._group(lambda r: r.tier)

    def summary_by_provider(self) -> dict[str, dict[str, float]]:
        return self._group(lambda r: r.provider or "n/a")

    def _group(self, key) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for r in self.records:
            b = out.setdefault(key(r), {"tokens": 0, "cost_usd": 0.0, "calls": 0, "cache_hits": 0,
                                        "saved_usd": 0.0, "latency_s": 0.0})
            b["tokens"] += r.prompt_tokens + r.completion_tokens
            b["cost_usd"] = round(b["cost_usd"] + r.cost_usd, 6)
            b["calls"] += 1
            b["cache_hits"] += 1 if r.cached else 0
            b["saved_usd"] = round(b["saved_usd"] + r.saved_usd, 6)
            b["latency_s"] = round(b["latency_s"] + r.latency_s, 3)
        return out

    def snapshot(self) -> dict:
        lat = [r.latency_s for r in self.records if r.latency_s and not r.cached]
        tps = [r.tokens_per_s for r in self.records if r.tokens_per_s]
        ttft = [r.ttft_s for r in self.records if r.ttft_s]
        return {
            "total_cost_usd": self.total_cost_usd,
            "total_tokens": self.total_tokens,
            "calls": len(self.records),
            "cache_hit_rate": self.cache_hit_rate,
            "prefix_cache_ratio": self.prefix_cache_ratio,
            "saved_usd": self.total_saved_usd,
            "by_stage": self.summary_by_stage(),
            "by_tier": self.summary_by_tier(),
            "by_provider": self.summary_by_provider(),
            "latency_p50_s": round(statistics.median(lat), 3) if lat else None,
            "ttft_p50_s": round(statistics.median(ttft), 3) if ttft else None,
            "tokens_per_s_p50": round(statistics.median(tps), 1) if tps else None,
            "calls_detail": [asdict(r) for r in self.records],
        }
