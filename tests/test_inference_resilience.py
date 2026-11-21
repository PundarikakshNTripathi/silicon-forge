# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retry-After is honoured, and a provider's rate limit is paced client-side. Fake clock and sleeper: no real waiting."""
from __future__ import annotations

import httpx
import pytest

from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.catalog import Catalog, Target
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
    TransientProviderError,
    parse_retry_after,
    retry_call,
)
from services.orchestrator.inference.providers.openai_compat import OpenAICompatibleProvider
from services.orchestrator.inference.ratelimit import RateLimiter, RateLimitWait
from services.orchestrator.inference.router import ModelRouter

# ------------------------------------------------------------------------------------------ Retry-After


@pytest.mark.parametrize("raw, expected", [("7", 7.0), ("0", 0.0), ("2.5", 2.5), (" 12 ", 12.0), ("-3", 0.0),
                                           (None, None), ("soon", None), ("nan", None), ("inf", None)])
def test_retry_after_seconds(raw, expected):
    assert parse_retry_after(raw) == expected


def test_retry_after_http_date():
    from email.utils import formatdate

    now = 1_800_000_000.0
    assert parse_retry_after(formatdate(now + 30, usegmt=True), now=now) == pytest.approx(30.0, abs=1.0)
    assert parse_retry_after(formatdate(now - 500, usegmt=True), now=now) == 0.0  # a past date means "now", never a negative sleep


def test_retry_call_waits_the_servers_time_not_a_random_one():
    slept = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientProviderError("429", retry_after=11.0)
        return "ok"

    assert retry_call(flaky, attempts=3, sleep=slept.append) == "ok"
    assert slept == [11.0, 11.0]


def test_retry_call_does_not_wait_out_an_unreasonable_ask():
    slept = []

    def always():
        raise TransientProviderError("429", retry_after=900.0)

    with pytest.raises(TransientProviderError):
        retry_call(always, attempts=3, sleep=slept.append, max_retry_after=60.0)
    assert slept == []  # gave up at once so the router can fall back to another target


def test_without_retry_after_the_jittered_backoff_still_applies():
    slept = []
    n = {"i": 0}

    def flaky():
        n["i"] += 1
        if n["i"] < 2:
            raise TransientProviderError("503")
        return "ok"

    assert retry_call(flaky, attempts=3, base_delay=1.0, sleep=slept.append) == "ok"
    assert len(slept) == 1 and 0 <= slept[0] <= 1.0


def test_openai_compatible_provider_reads_the_header():
    p = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    p.name = "x"
    resp = httpx.Response(429, headers={"retry-after": "9"}, text="slow down", request=httpx.Request("POST", "http://x"))
    with pytest.raises(TransientProviderError) as err:
        p._raise_for_status(resp)
    assert err.value.retry_after == 9.0
    with pytest.raises(TransientProviderError) as err2:
        p._raise_for_status(httpx.Response(503, text="down", request=httpx.Request("POST", "http://x")))
    assert err2.value.retry_after is None


# ----------------------------------------------------------------------------------------------- limiter

class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def limiter(clock, **kw):
    return RateLimiter(clock=clock.now, sleep=clock.sleep, **kw)


def test_requests_under_the_limit_are_not_delayed():
    c = Clock()
    lim = limiter(c, rpm=3)
    assert [lim.acquire() for _ in range(3)] == [0.0, 0.0, 0.0] and c.slept == []


def test_the_request_over_rpm_waits_for_the_oldest_to_leave_the_window():
    c = Clock()
    lim = limiter(c, rpm=2, max_wait_s=100)
    lim.acquire()
    c.t += 10
    lim.acquire()
    waited = lim.acquire()  # third request: must wait until the first is 60 s old, i.e. 50 more seconds
    assert waited == pytest.approx(50.0) and c.slept == [pytest.approx(50.0)]


def test_tpm_counts_tokens_and_frees_them_as_requests_age_out():
    c = Clock()
    lim = limiter(c, tpm=1000, max_wait_s=100)
    lim.acquire(600)
    c.t += 20
    lim.acquire(300)
    assert lim.acquire(200) == pytest.approx(40.0)  # needs the 600 to leave: 60 - 20 s from the second request's time


def test_a_request_bigger_than_the_whole_allowance_is_refused_at_once():
    lim = limiter(Clock(), tpm=500)
    with pytest.raises(RateLimitWait, match="never fit"):
        lim.acquire(501)


def test_waiting_longer_than_max_wait_raises_instead_of_stalling():
    c = Clock()
    lim = limiter(c, rpm=1, max_wait_s=5)
    lim.acquire()
    with pytest.raises(RateLimitWait, match="over the 5s limit"):
        lim.acquire()
    assert c.slept == []  # it did not sleep a little and then give up


def test_config_is_validated():
    assert RateLimiter.from_config(None) is None and RateLimiter.from_config({}) is None
    assert RateLimiter.from_config({"rpm": 5, "max_wait_s": 3}).max_wait_s == 3.0
    with pytest.raises(ValueError, match="rate_limit must be"):
        RateLimiter.from_config({"rps": 5})
    with pytest.raises(ValueError, match="rpm"):
        RateLimiter.from_config({"rpm": 0})


# ----------------------------------------------------------------------------------------------- router

class Counting(BaseLLMProvider):
    name = "p"
    local = True

    def __init__(self):
        self.calls = 0

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        self.calls += 1
        return LLMResponse(content="ok", prompt_tokens=5, completion_tokens=5, model=model, provider="p")


def router(limit, targets=("p",)):
    providers = {n: {"kind": "replay", "local": True, **({"rate_limit": limit} if n == "p" else {})} for n in targets}
    chain = [Target(n, "m") for n in targets]
    cat = Catalog(providers=providers, profiles={"dev": {t: chain for t in ("local_fast", "balanced", "frontier_reasoning")}})
    prov = Counting()
    r = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(), provider=prov, catalog=cat)
    return r, prov


def test_router_paces_and_records_the_wait(monkeypatch):
    r, prov = router({"rpm": 1, "max_wait_s": 100})
    c = Clock()
    lim = r.limiter("p")
    lim._clock, lim._sleep = c.now, c.sleep
    r.complete("s", "agent_step", "sys", "a", bypass_cache=True)
    r.complete("s", "agent_step", "sys", "b", bypass_cache=True)
    assert prov.calls == 2 and r.stats.rate_wait_s == pytest.approx(60.0)


def test_when_pacing_would_take_too_long_the_router_falls_through_without_tripping_the_breaker():
    r, prov = router({"rpm": 1, "max_wait_s": 1})
    c = Clock()
    lim = r.limiter("p")
    lim._clock, lim._sleep = c.now, c.sleep
    r.complete("s", "agent_step", "sys", "a", bypass_cache=True)
    with pytest.raises(ProviderError, match="All targets failed"):
        r.complete("s", "agent_step", "sys", "b", bypass_cache=True)
    assert prov.calls == 1 and r.breaker("p").failures == 0
    assert any("rate limit" in str(a.get("skipped", "")) for a in r.stats.attempts)


def test_no_rate_limit_means_no_limiter_and_a_bad_one_is_loud():
    r, _ = router(None)
    assert r.limiter("p") is None
    bad, _ = router({"rps": 3})
    with pytest.raises(ValueError, match="rate_limit must be"):
        bad.complete("s", "agent_step", "sys", "a", bypass_cache=True)
