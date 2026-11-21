# SPDX-License-Identifier: AGPL-3.0-or-later
"""Client-side pacing for a provider: stay under requests-per-minute and tokens-per-minute limits instead of discovering them
as HTTP 429s.

```yaml
# configs/models.yaml
providers:
  openai:
    kind: openai_compat
    rate_limit: {rpm: 60, tpm: 200000, max_wait_s: 30}
```

A sliding 60-second window. `acquire` sleeps until the request fits and returns how long it waited. If fitting would take
longer than `max_wait_s`, it raises `RateLimitWait` and the router moves to the next target in the chain rather than stall a
run. A single request larger than the whole per-minute token allowance can never fit and is refused immediately. Token counts
are the router's estimate of the prompt (about four characters per token); the provider's own accounting is authoritative, so
leave some headroom in `tpm`.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from services.orchestrator.inference.providers.base import ProviderError

WINDOW_S = 60.0


class RateLimitWait(ProviderError):
    """The request cannot be paced within the allowed wait. Not a provider failure: it must not trip a circuit breaker."""


class RateLimiter:
    def __init__(self, rpm: int | None = None, tpm: int | None = None, max_wait_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        if rpm is not None and rpm < 1:
            raise ValueError("rpm must be at least 1")
        if tpm is not None and tpm < 1:
            raise ValueError("tpm must be at least 1")
        self.rpm, self.tpm, self.max_wait_s = rpm, tpm, max_wait_s
        self._clock, self._sleep = clock, sleep
        self._events: deque[tuple[float, int]] = deque()  # (time, tokens)
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg: dict | None, **kw) -> RateLimiter | None:
        """None when the provider has no `rate_limit`; a malformed one is an error, not silently ignored."""
        if not cfg:
            return None
        if not isinstance(cfg, dict) or not set(cfg) <= {"rpm", "tpm", "max_wait_s"}:
            raise ValueError(f"rate_limit must be a mapping with rpm, tpm and/or max_wait_s; got {cfg!r}")
        return cls(rpm=cfg.get("rpm"), tpm=cfg.get("tpm"), max_wait_s=float(cfg.get("max_wait_s", 30.0)), **kw)

    def _fit_delay(self, now: float, tokens: int) -> float:
        """0 if the request fits now, else seconds until enough of the window has expired."""
        while self._events and now - self._events[0][0] >= WINDOW_S:
            self._events.popleft()
        wait = 0.0
        if self.rpm is not None and len(self._events) >= self.rpm:
            wait = max(wait, self._events[0][0] + WINDOW_S - now)
        if self.tpm is not None:
            used = sum(t for _, t in self._events)
            if used + tokens > self.tpm:
                freed = 0
                for ts, tk in self._events:  # tokens come back as the oldest requests leave the window
                    freed += tk
                    if used - freed + tokens <= self.tpm:
                        wait = max(wait, ts + WINDOW_S - now)
                        break
        return wait

    def acquire(self, tokens: int = 0) -> float:
        if self.tpm is not None and tokens > self.tpm:
            raise RateLimitWait(f"a {tokens}-token request can never fit a limit of {self.tpm} tokens per minute")
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                delay = self._fit_delay(now, tokens)
                if delay <= 0:
                    self._events.append((now, tokens))
                    return waited
            if waited + delay > self.max_wait_s:
                raise RateLimitWait(f"pacing this request would take {waited + delay:.0f}s, over the {self.max_wait_s:g}s limit")
            self._sleep(delay)
            waited += delay
