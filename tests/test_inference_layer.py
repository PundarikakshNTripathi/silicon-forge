# SPDX-License-Identifier: AGPL-3.0-or-later
"""Providers, catalog pricing, router fallback / circuit breaker / budgets, and the persistent response cache."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.cache import ResponseCache
from services.orchestrator.inference.catalog import Catalog, ModelPrice, Target, load_catalog
from services.orchestrator.inference.cost_tracker import BudgetExceededError, CostTracker
from services.orchestrator.inference.estimate import estimate_profile_cost
from services.orchestrator.inference.providers.anthropic_provider import AnthropicProvider
from services.orchestrator.inference.providers.base import (
    BaseLLMProvider,
    CircuitBreaker,
    LLMResponse,
    ProviderError,
    RefusalError,
    TransientProviderError,
    retry_call,
)
from services.orchestrator.inference.providers.cli_bridge import CliBridgeProvider
from services.orchestrator.inference.providers.openai_compat import OpenAICompatibleProvider
from services.orchestrator.inference.router import ModelRouter

# --- OpenAI-compatible (llama.cpp / OpenAI) --------------------------------------------------------------------

def _sse(chunks: list[dict]) -> bytes:
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def test_openai_compat_streams_and_reads_llamacpp_cache_hits():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        chunks = [
            {"choices": [{"delta": {"content": "mod"}}]},
            {"choices": [{"delta": {"content": "ule x;"}}]},
            {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 12},
             "timings": {"cache_n": 640, "predicted_per_second": 41.5}},
        ]
        return httpx.Response(200, content=_sse(chunks), headers={"content-type": "text/event-stream"})

    p = OpenAICompatibleProvider(
        base_url="http://llama.test/v1", name="llamacpp", local=True, supports_json_schema=True, cache_prompt=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    r = p.generate("local", "sys", "usr", 0.1, json_schema={"type": "object"})
    assert r.content == "module x;"
    assert (r.prompt_tokens, r.completion_tokens, r.cached_tokens) == (900, 12, 640)
    assert r.tokens_per_s == 41.5 and r.ttft_s is not None and r.provider == "llamacpp"
    body = seen["body"]
    assert body["cache_prompt"] is True and body["stream"] is True
    assert body["response_format"]["type"] == "json_schema"
    assert body["messages"][0]["role"] == "system"


def test_openai_compat_retries_transient_and_fails_fast_on_client_errors(monkeypatch):
    monkeypatch.setattr("services.orchestrator.inference.providers.base.time.sleep", lambda s: None)
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}})

    p = OpenAICompatibleProvider(base_url="http://x/v1", stream=False, client=httpx.Client(transport=httpx.MockTransport(flaky)))
    assert p.generate("m", "s", "u").content == "ok" and calls["n"] == 3

    bad = OpenAICompatibleProvider(base_url="http://x/v1", stream=False,
                                   client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no"))))
    with pytest.raises(ProviderError) as ei:
        bad.generate("m", "s", "u")
    assert not isinstance(ei.value, TransientProviderError)


# --- Anthropic ---------------------------------------------------------------------------------------------------

class FakeAnthropic:
    def __init__(self, msg):
        self.msg, self.calls = msg, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return self.msg


def _msg(stop="end_turn", read=0, write=0, fresh=100, out=50):
    return SimpleNamespace(
        stop_reason=stop, stop_details=SimpleNamespace(category="cyber"), model="claude-sonnet-5-5",
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="hello")],
        usage=SimpleNamespace(input_tokens=fresh, output_tokens=out, cache_read_input_tokens=read,
                              cache_creation_input_tokens=write),
    )


def test_anthropic_provider_caches_prefix_and_never_sends_sampling_params():
    fake = FakeAnthropic(_msg(read=800, write=0, fresh=200))
    p = AnthropicProvider(client=fake, efforts={"claude-sonnet-5-5": "low"})
    r = p.generate("claude-sonnet-5-5", "SYSTEM", "USER", temperature=0.3)
    call = fake.calls[0]
    assert "temperature" not in call and "top_p" not in call
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"] == {"effort": "low"}
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert (r.content, r.prompt_tokens, r.cached_tokens, r.completion_tokens) == ("hello", 1000, 800, 50)


def test_anthropic_refusal_is_a_refusal_error():
    p = AnthropicProvider(client=FakeAnthropic(_msg(stop="refusal")), fallbacks=False)
    with pytest.raises(RefusalError, match="cyber"):
        p.generate("claude-sonnet-5-5", "s", "u")


# --- CLI bridge (opt-in) ---------------------------------------------------------------------------------------

def test_cli_bridge_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SILICONFORGE_ENABLE_CLI_BRIDGE", raising=False)
    p = CliBridgeProvider(cli="claude")
    assert not p.enabled and not p.available()
    with pytest.raises(ProviderError, match="disabled"):
        p.generate("claude", "s", "u")


def test_cli_bridge_runs_the_official_cli_on_stdin_when_enabled():
    import sys

    p = CliBridgeProvider(cli="claude", enabled=True, command=[sys.executable, "-c",
                          "import sys; d=sys.stdin.read(); print('echo:' + str(len(d)))"])
    r = p.generate("claude", "SYS", "USR")
    assert r.content.startswith("echo:") and r.extra["billing"] == "subscription"


# --- primitives ------------------------------------------------------------------------------------------------

def test_retry_call_and_circuit_breaker():
    n = {"i": 0}

    def fn():
        n["i"] += 1
        if n["i"] < 3:
            raise TransientProviderError("x")
        return "done"

    assert retry_call(fn, attempts=3, sleep=lambda s: None) == "done"

    t = {"now": 0.0}
    b = CircuitBreaker(threshold=2, cooldown_s=10, clock=lambda: t["now"])
    b.failure()
    b.failure()
    assert not b.allow()
    t["now"] = 11.0
    assert b.allow()  # half-open probe
    b.success()
    assert b.allow() and b.failures == 0


# --- catalog / pricing -----------------------------------------------------------------------------------------

def test_catalog_pricing_applies_cache_discounts():
    cat = load_catalog()
    sonnet = cat.price("claude-sonnet-5-5")
    full = sonnet.cost_usd(10_000, 1_000)
    cached = sonnet.cost_usd(10_000, 1_000, cached_tokens=9_000)
    assert cached < full
    assert sonnet.cost_usd(1_000_000, 0) == pytest.approx(2.00)
    assert {"replay", "local", "hybrid", "cloud", "subscription"} <= set(cat.profiles)
    est = estimate_profile_cost(cat, "cloud")
    assert est["usd"] > 0 and estimate_profile_cost(cat, "local")["usd"] == 0.0


# --- router ----------------------------------------------------------------------------------------------------

class Scripted(BaseLLMProvider):
    def __init__(self, name, outcomes, local=False):
        self.name, self.outcomes, self.local, self.calls = name, list(outcomes), local, 0

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        self.calls += 1
        out = self.outcomes.pop(0) if self.outcomes else self.outcomes_default()
        if isinstance(out, Exception):
            raise out
        return out

    def outcomes_default(self):
        return LLMResponse("ok", 1000, 100, "m", provider=self.name)


def _router(chain_a, chain_b, cfg=None, cache=None):
    catalog = Catalog(
        providers={"a": {"kind": "openai_compat"}, "b": {"kind": "openai_compat", "local": True}},
        models={"big": ModelPrice(input=10.0, output=30.0, cache_read=1.0)},
        profiles={"p": {t: [Target("a", "big"), Target("b", "small")] for t in ("local_fast", "balanced", "frontier_reasoning")}},
    )
    cfg = cfg or InferenceConfig(profile="p", enable_semantic_cache=cache is not None)
    router = ModelRouter(cfg, CostTracker(max_budget_usd=5, max_total_tokens=10**6), catalog=catalog, cache=cache)
    router._providers = {"a": chain_a, "b": chain_b}
    return router


def test_router_falls_back_on_failure_refusal_and_open_breaker():
    a = Scripted("a", [TransientProviderError("down")] * 3 + [RefusalError("no")])
    b = Scripted("b", [], local=True)
    router = _router(a, b)
    r = router.complete("S", "rtl_generation", "s", "u1")
    assert r.provider == "b" and router.stats.fallbacks == 1
    # three consecutive failures open a's breaker; subsequent calls skip it without calling
    router.complete("S", "rtl_generation", "s", "u2")
    router.complete("S", "rtl_generation", "s", "u3")
    router.complete("S", "rtl_generation", "s", "u4")
    calls_before = a.calls
    router.complete("S", "rtl_generation", "s", "u5")
    assert a.calls == calls_before
    assert router.cost_tracker.records[-1].billing == "local" and router.cost_tracker.records[-1].cost_usd == 0.0


def test_router_prices_cached_tokens_and_reports_savings():
    resp = LLMResponse("ok", prompt_tokens=100_000, completion_tokens=1_000, model="big", cached_tokens=80_000, provider="a")
    router = _router(Scripted("a", [resp]), Scripted("b", [], local=True))
    router.complete("S", "rtl_generation", "s", "u")
    rec = router.cost_tracker.records[-1]
    # 20k fresh @10 + 80k cached @1 + 1k out @30 = 0.2 + 0.08 + 0.03 = 0.31
    assert rec.cost_usd == pytest.approx(0.31)
    assert rec.saved_usd == pytest.approx(0.72)  # full price 1.03 - actual 0.31
    assert router.cost_tracker.prefix_cache_ratio == pytest.approx(0.8)


def test_response_cache_persists_across_processes(tmp_path):
    path = tmp_path / "llm.sqlite"
    a1 = Scripted("a", [], local=False)
    r1 = _router(a1, Scripted("b", [], local=True), cache=ResponseCache(path))
    r1.complete("S", "rtl_generation", "sys", "same prompt")
    assert a1.calls == 1

    a2 = Scripted("a", [])
    r2 = _router(a2, Scripted("b", [], local=True), cache=ResponseCache(path))  # a "new process"
    r2.complete("S", "rtl_generation", "sys", "same prompt")
    assert a2.calls == 0 and r2.cost_tracker.records[-1].cached
    assert r2.cost_tracker.cache_hit_rate == 1.0
    # high-temperature calls are never served from the cache
    r2.complete("S", "rtl_generation", "sys", "same prompt", temperature=0.9)
    assert a2.calls == 1


def test_stage_token_budget_is_enforced():
    cfg = InferenceConfig(profile="p", enable_semantic_cache=False, stage_token_budgets={"RTL_GEN": 500})
    router = _router(Scripted("a", []), Scripted("b", [], local=True), cfg=cfg)
    with pytest.raises(BudgetExceededError, match="Stage 'RTL_GEN'"):
        router.complete("RTL_GEN", "rtl_generation", "s", "u")  # the scripted reply alone is 1100 tokens


def test_all_targets_failing_raises_a_clear_error():
    a = Scripted("a", [ProviderError("bad key")])
    b = Scripted("b", [ProviderError("server down")], local=True)
    with pytest.raises(ProviderError, match="All targets failed"):
        _router(a, b).complete("S", "rtl_generation", "s", "u")
