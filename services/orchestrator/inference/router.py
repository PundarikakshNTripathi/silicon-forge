"""Tiered LLM inference router with SHA-256 semantic prompt caching and cost enforcement."""
from __future__ import annotations

import hashlib

from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.providers import (
    BaseLLMProvider,
    LLMResponse,
    MockChipDesignLLM,
    OpenAICompatibleProvider,
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
}


class ModelRouter:
    def __init__(
        self,
        config: InferenceConfig,
        cost_tracker: CostTracker,
        provider: BaseLLMProvider | None = None,
    ) -> None:
        self.config = config
        self.cost_tracker = cost_tracker
        if provider is not None:
            self.provider = provider
        elif config.provider == "mock":
            self.provider = MockChipDesignLLM()
        else:
            self.provider = OpenAICompatibleProvider()
        self._cache: dict[str, LLMResponse] = {}

    def select_tier(self, task_type: str, retry_attempt: int = 0) -> str:
        tier = TASK_TIER_MAP.get(task_type, "balanced")
        # Escalate tier automatically on repeated repair failures
        if retry_attempt >= 2 and tier != "frontier_reasoning":
            return "frontier_reasoning"
        if retry_attempt == 1 and tier == "local_fast":
            return "balanced"
        return tier

    def _cache_key(self, model: str, system_prompt: str, user_prompt: str, temperature: float) -> str:
        raw = f"{model}||{temperature:.2f}||{system_prompt}||{user_prompt}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def complete(
        self,
        stage: str,
        task_type: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        retry_attempt: int = 0,
        bypass_cache: bool = False,
    ) -> LLMResponse:
        self.cost_tracker.check_budget()
        tier_name = self.select_tier(task_type, retry_attempt=retry_attempt)
        tier_cfg = self.config.tiers[tier_name]
        key = self._cache_key(tier_cfg.model, system_prompt, user_prompt, temperature)

        if self.config.enable_semantic_cache and not bypass_cache and key in self._cache:
            cached_resp = self._cache[key]
            self.cost_tracker.record(
                stage=stage,
                tier=tier_name,
                model=tier_cfg.model,
                prompt_tokens=cached_resp.prompt_tokens,
                completion_tokens=cached_resp.completion_tokens,
                input_cost_per_1m=tier_cfg.input_cost_per_1m,
                output_cost_per_1m=tier_cfg.output_cost_per_1m,
                cached=True,
            )
            return cached_resp

        resp = self.provider.generate(
            model=tier_cfg.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
        )
        if self.config.enable_semantic_cache:
            self._cache[key] = resp

        self.cost_tracker.record(
            stage=stage,
            tier=tier_name,
            model=tier_cfg.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            input_cost_per_1m=tier_cfg.input_cost_per_1m,
            output_cost_per_1m=tier_cfg.output_cost_per_1m,
            cached=False,
        )
        return resp
