"""Tiered model router: profile-driven fallback chains, circuit breakers, response cache, real cost accounting.

Per call: pick a tier from the task (escalating on repeated failures), take that tier's ordered chain of
(provider, model) targets from the active profile, and try them in order. Transient failures, refusals and open
circuit breakers fall through to the next target. Usage is priced with the catalog (including provider prompt-cache
discounts) and recorded in the CostTracker, which enforces USD / total / per-stage token budgets.
"""
from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass, field

from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.cache import ResponseCache
from services.orchestrator.inference.catalog import Catalog, Target, load_catalog
from services.orchestrator.inference.cost_tracker import CostTracker, UsageRecord
from services.orchestrator.inference.factory import build_provider
from services.orchestrator.inference.ledger import Ledger, prompt_hash
from services.orchestrator.inference.providers.base import (
    BaseLLMProvider,
    CircuitBreaker,
    LLMResponse,
    ProviderError,
    RefusalError,
)

# Task complexity -> model tier mapping
TASK_TIER_MAP: dict[str, str] = {
    "log_triage": "local_fast",
    "lint_fix": "local_fast",
    "rtl_generation": "balanced",
    "formal_ir_synthesis": "frontier_reasoning",
    "architecture_contract": "frontier_reasoning",
    "counterexample_repair": "frontier_reasoning",
    "physical_eco": "balanced",
    "agent_step": "balanced",
}

#: only low-temperature calls are cached: a replayed answer is as valid as a fresh sample
CACHE_MAX_TEMPERATURE = 0.11


def _model_allowed(target: Target, patterns: list[str]) -> bool:
    ident = f"{target.provider}:{target.model}"
    return any(fnmatch.fnmatchcase(target.model, p) or fnmatch.fnmatchcase(ident, p) for p in patterns)


@dataclass
class RouterStats:
    fallbacks: int = 0
    refusals: int = 0
    attempts: list[dict] = field(default_factory=list)


class ModelRouter:
    def __init__(
        self,
        config: InferenceConfig,
        cost_tracker: CostTracker,
        provider: BaseLLMProvider | None = None,
        catalog: Catalog | None = None,
        cache: ResponseCache | None = None,
    ) -> None:
        self.config = config
        self.cost_tracker = cost_tracker
        self.catalog = catalog or load_catalog(config.catalog_path)
        self.profile = config.profile
        self._override = provider  # a single provider serving every target (tests, CLI replay mode)
        self._providers: dict[str, BaseLLMProvider] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self.stats = RouterStats()
        self.ledger = Ledger(config.ledger_path) if config.ledger_path else None
        #: optional observer called once per completed call (used to stream the agent log to the UI)
        self.on_call: Callable[[dict], None] | None = None
        replay_only = self._override is not None and self._override.name == "replay" or all(
            self.catalog.providers.get(t.provider, {}).get("kind") == "replay"
            for chain in self.catalog.profiles.get(self.profile, {}).values()
            for t in chain
        )
        if cache is not None:
            self.cache: ResponseCache | None = cache
        elif config.enable_semantic_cache and not replay_only:  # replay fixtures are already free and deterministic
            self.cache = ResponseCache(config.cache_path)
        else:
            self.cache = None
        if config.stage_token_budgets and not cost_tracker.stage_token_budgets:
            cost_tracker.stage_token_budgets = dict(config.stage_token_budgets)

    # ----------------------------------------------------------------------------------------------

    def select_tier(self, task_type: str, retry_attempt: int = 0) -> str:
        tier = TASK_TIER_MAP.get(task_type, "balanced")
        # Escalate tier automatically on repeated repair failures
        if retry_attempt >= 2 and tier != "frontier_reasoning":
            return "frontier_reasoning"
        if retry_attempt == 1 and tier == "local_fast":
            return "balanced"
        return tier

    def provider_for(self, name: str) -> BaseLLMProvider:
        if self._override is not None:
            return self._override
        if name not in self._providers:
            self._providers[name] = build_provider(name, self.catalog, self.config.cli_bridge_enabled)
        return self._providers[name]

    def breaker(self, name: str) -> CircuitBreaker:
        return self._breakers.setdefault(name, CircuitBreaker())

    def chain(self, tier: str) -> list[Target]:
        chain = self.catalog.chain(self.profile, tier)
        allowed = self.config.allowed_models
        if not allowed:
            return chain
        kept = [t for t in chain if _model_allowed(t, allowed)]
        if not kept:
            raise ProviderError(
                f"Model policy forbids every target in tier '{tier}' of profile '{self.profile}' "
                f"(allowed: {', '.join(allowed)}; chain: {', '.join(f'{t.provider}:{t.model}' for t in chain)})."
            )
        return kept

    # ----------------------------------------------------------------------------------------------

    def complete(
        self,
        stage: str,
        task_type: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        retry_attempt: int = 0,
        bypass_cache: bool = False,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        self.cost_tracker.check_budget(stage)
        tier = self.select_tier(task_type, retry_attempt=retry_attempt)
        chain = self.chain(tier)
        primary = chain[0]

        cache_key = None
        if self.cache is not None and not bypass_cache and temperature <= CACHE_MAX_TEMPERATURE:
            cache_key = ResponseCache.key(
                primary.provider, primary.model, system_prompt, user_prompt, temperature, json_schema
            )
            hit = self.cache.get(cache_key)
            if hit is not None:
                self._record_cache_hit(stage, tier, primary, hit)
                self._ledger(stage, task_type, tier, primary, hit, system_prompt, user_prompt, cache_hit=True)
                self._emit(stage, tier, primary, hit, system_prompt, user_prompt, cache_hit=True)
                return hit

        last_error: Exception | None = None
        for i, target in enumerate(chain):
            breaker = self.breaker(target.provider)
            if not breaker.allow():
                self.stats.attempts.append({"target": target.provider, "skipped": "circuit open"})
                continue
            try:
                provider = self.provider_for(target.provider)
                resp = provider.generate(
                    model=target.model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    json_schema=json_schema,
                )
            except RefusalError as exc:
                self.stats.refusals += 1
                last_error = exc
                self.stats.attempts.append({"target": target.provider, "error": "refusal"})
                continue
            except ProviderError as exc:
                breaker.failure()
                last_error = exc
                self.stats.attempts.append({"target": target.provider, "error": str(exc)[:200]})
                continue
            breaker.success()
            if i > 0:
                self.stats.fallbacks += 1
            resp.provider = resp.provider or target.provider
            if cache_key is not None and self.cache is not None:
                self.cache.put(cache_key, resp)
            self._record(stage, tier, target, provider, resp)
            self._ledger(stage, task_type, tier, target, resp, system_prompt, user_prompt, cache_hit=False)
            self._emit(stage, tier, target, resp, system_prompt, user_prompt, cache_hit=False)
            return resp

        raise ProviderError(
            f"All targets failed for tier '{tier}' in profile '{self.profile}': {last_error}"
        ) from last_error

    # ----------------------------------------------------------------------------------------------

    def _ledger(self, stage: str, task_type: str, tier: str, target: Target, resp: LLMResponse, system_prompt: str,
                user_prompt: str, cache_hit: bool) -> None:
        if self.ledger is None:
            return
        self.ledger.record(
            stage=stage, task=task_type, tier=tier, provider=target.provider, model=resp.model or target.model,
            prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens, cached_tokens=resp.cached_tokens,
            latency_s=resp.latency_s, billing=str(resp.extra.get("billing", "local" if self.catalog.provider_is_local(target.provider) else "metered")),
            cache_hit=cache_hit, phash=prompt_hash(system_prompt, user_prompt),
        )

    def _emit(self, stage: str, tier: str, target: Target, resp: LLMResponse, system_prompt: str,
              user_prompt: str, cache_hit: bool) -> None:
        if self.on_call is None:
            return
        rec = self.cost_tracker.records[-1]
        clip = 20_000
        self.on_call({
            "stage": stage, "tier": tier, "provider": target.provider, "model": target.model,
            "cache_hit": cache_hit, "prompt_tokens": resp.prompt_tokens, "completion_tokens": resp.completion_tokens,
            "cached_tokens": resp.cached_tokens, "latency_s": resp.latency_s, "ttft_s": resp.ttft_s,
            "tokens_per_s": resp.tokens_per_s, "cost_usd": rec.cost_usd, "saved_usd": rec.saved_usd,
            "system_prompt": system_prompt[:clip], "user_prompt": user_prompt[:clip], "response": resp.content[:clip],
        })

    def _record(self, stage: str, tier: str, target: Target, provider: BaseLLMProvider, resp: LLMResponse) -> None:
        price = self.catalog.price(target.model)
        is_subscription = resp.extra.get("billing") == "subscription"
        is_local = provider.local or self.catalog.provider_is_local(target.provider)
        cost = 0.0 if (is_local or is_subscription) else price.cost_usd(
            resp.prompt_tokens, resp.completion_tokens, resp.cached_tokens, resp.cache_write_tokens
        )
        full = price.cost_usd(resp.prompt_tokens, resp.completion_tokens)
        saved = 0.0 if (is_local or is_subscription) else max(0.0, full - cost)
        self.cost_tracker.add(UsageRecord(
            stage=stage, tier=tier, model=target.model,
            prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
            cost_usd=round(cost, 6), provider=target.provider,
            cached_tokens=resp.cached_tokens, cache_write_tokens=resp.cache_write_tokens,
            latency_s=resp.latency_s, ttft_s=resp.ttft_s, tokens_per_s=resp.tokens_per_s,
            saved_usd=round(saved, 6), local=is_local,
            billing="subscription" if is_subscription else ("local" if is_local else "metered"),
        ))

    def _record_cache_hit(self, stage: str, tier: str, target: Target, resp: LLMResponse) -> None:
        price = self.catalog.price(target.model)
        saved = 0.0 if self.catalog.provider_is_local(target.provider) else price.cost_usd(
            resp.prompt_tokens, resp.completion_tokens
        )
        self.cost_tracker.add(UsageRecord(
            stage=stage, tier=tier, model=target.model, prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
            cached=True, provider="cache", saved_usd=round(saved, 6), latency_s=0.0, billing="cache",
        ))

    def estimate_run_cost(self) -> dict:
        """Rough pre-run USD estimate for the active profile (typical token volumes per stage)."""
        from services.orchestrator.inference.estimate import estimate_profile_cost

        return estimate_profile_cost(self.catalog, self.profile)

