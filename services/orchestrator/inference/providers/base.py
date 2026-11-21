# SPDX-License-Identifier: AGPL-3.0-or-later
"""Provider abstractions: normalized responses and usage, error taxonomy, retry with jitter, circuit breaker."""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int  # total input tokens (including cached)
    completion_tokens: int
    model: str
    cached_tokens: int = 0  # input tokens served from a provider/server prompt cache (cheap)
    cache_write_tokens: int = 0  # input tokens written to a provider cache (Anthropic; costs more)
    latency_s: float = 0.0
    ttft_s: float | None = None  # time to first token when streamed
    provider: str = ""
    tokens_per_s: float | None = None  # decode speed when the server reports it (llama.cpp)
    extra: dict = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Non-retryable provider failure (bad request, auth, refusal)."""


class TransientProviderError(ProviderError):
    """Retryable failure (rate limit, 5xx, timeout, connection)."""


class RefusalError(ProviderError):
    """The model declined the request (safety classifier)."""


class BaseLLMProvider:
    name = "base"
    #: local providers have no per-token cost
    local = False

    def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        raise NotImplementedError

    def health(self) -> dict:
        return {"ok": True}


def retry_call(
    fn: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry `fn` on TransientProviderError with exponential backoff and full jitter."""
    last: TransientProviderError | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientProviderError as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep(random.uniform(0, min(max_delay, base_delay * (2**i))))
    assert last is not None
    raise last


@dataclass
class CircuitBreaker:
    """Opens after `threshold` consecutive failures; half-opens after `cooldown_s`."""

    threshold: int = 3
    cooldown_s: float = 60.0
    failures: int = 0
    opened_at: float | None = None
    clock: Callable[[], float] = time.monotonic

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self.clock() - self.opened_at >= self.cooldown_s:
            return True  # half-open: let one request probe
        return False

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None and not self.allow()
