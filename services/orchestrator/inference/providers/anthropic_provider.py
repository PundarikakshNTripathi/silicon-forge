"""Claude via the official Anthropic SDK, with prompt caching and refusal handling.

Notes that matter on the current Claude 5.x models (see the claude-api reference):
  * `temperature`/`top_p`/`top_k` are rejected, so they are never sent; depth is controlled with `output_config.effort`.
  * Thinking is on by default; thinking blocks are ignored and only `text` blocks are returned.
  * `cache_control` marks the stable prefix (system prompt) so repeated calls read it from the cache (~5% of input price).
  * `stop_reason == "refusal"` is surfaced as RefusalError; optional server-side `fallbacks` rescue refused requests.
"""
from __future__ import annotations

import os
import time
from typing import Any

from services.orchestrator.inference.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
    RefusalError,
    TransientProviderError,
    retry_call,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: Any | None = None,
        max_tokens: int = 16000,
        efforts: dict[str, str] | None = None,
        default_effort: str = "medium",
        fallbacks: bool = True,
        cache_system_prompt: bool = True,
    ) -> None:
        self.max_tokens = max_tokens
        self.efforts = efforts or {}
        self.default_effort = default_effort
        self.fallbacks = fallbacks
        self.cache_system_prompt = cache_system_prompt
        if client is not None:
            self._client = client
        else:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - exercised only without the extra
                raise ProviderError("Install the Anthropic SDK: uv pip install -e '.[providers]'") from exc
            self._client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,  # accepted for interface symmetry; Claude 5.x rejects sampling params
        json_schema: dict | None = None,
    ) -> LLMResponse:
        system: Any = system_prompt
        if self.cache_system_prompt:
            system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_prompt}],
            "output_config": {"effort": self.efforts.get(model, self.default_effort)},
        }
        return retry_call(lambda: self._call(params), attempts=2)

    def _call(self, params: dict[str, Any]) -> LLMResponse:
        import anthropic

        t0 = time.perf_counter()
        try:
            if self.fallbacks:
                try:
                    msg = self._client.beta.messages.create(
                        **params, betas=[FALLBACK_BETA], fallbacks="default"
                    )
                except anthropic.BadRequestError as exc:
                    if "fallback" not in str(exc).lower():
                        raise
                    self.fallbacks = False  # this model/platform has no server-side fallbacks: stop sending them
                    msg = self._client.messages.create(**params)
            else:
                msg = self._client.messages.create(**params)
        except anthropic.RateLimitError as exc:
            raise TransientProviderError(f"anthropic: rate limited: {exc}") from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise TransientProviderError(f"anthropic: connection error: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientProviderError(f"anthropic: server error {exc.status_code}: {exc}") from exc
            raise ProviderError(f"anthropic: {exc.status_code}: {exc}") from exc
        return self._to_response(msg, time.perf_counter() - t0)

    def _to_response(self, msg: Any, latency: float) -> LLMResponse:
        if getattr(msg, "stop_reason", None) == "refusal":
            details = getattr(msg, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise RefusalError(f"anthropic: request refused (category={category})")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        u = msg.usage
        read = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        write = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        fresh = int(getattr(u, "input_tokens", 0) or 0)
        return LLMResponse(
            content=text,
            prompt_tokens=fresh + read + write,
            completion_tokens=int(getattr(u, "output_tokens", 0) or 0),
            model=getattr(msg, "model", ""),
            cached_tokens=read,
            cache_write_tokens=write,
            latency_s=round(latency, 4),
            provider=self.name,
            extra={"request_id": getattr(msg, "_request_id", None)},
        )

    def health(self) -> dict:
        return {"ok": bool(os.getenv("ANTHROPIC_API_KEY")) or self._client is not None}
