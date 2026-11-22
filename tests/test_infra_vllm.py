# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `vllm` profile talks to any OpenAI-compatible server. A stub server in a thread stands in for vLLM, SGLang or TGI: the test checks
what is sent (the model name, a JSON-schema response format, the token cap, the bearer key), that streamed and plain replies are parsed
with their token usage and cached-token counts, that a 429 carries its Retry-After, and that the URL can be redirected by environment."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.catalog import load_catalog
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.factory import build_provider
from services.orchestrator.inference.providers.base import TransientProviderError
from services.orchestrator.inference.router import ModelRouter


class Stub:
    """A minimal OpenAI-compatible chat server. `seen` records every request body and the Authorization header."""

    def __init__(self, fail_with: int | None = None):
        self.seen: list[dict] = []
        self.auth: list[str] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.seen.append(body)
                stub.auth.append(self.headers.get("Authorization", ""))
                if fail_with:
                    self.send_response(fail_with)
                    self.send_header("Retry-After", "7")
                    self.end_headers()
                    self.wfile.write(b'{"error":"busy"}')
                    return
                usage = {"prompt_tokens": 120, "completion_tokens": 4, "prompt_tokens_details": {"cached_tokens": 100}}
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for delta in ("{\"say\":", " \"hi\"}"):
                        self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': delta}}]})}\n\n".encode())
                    self.wfile.write(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    payload = json.dumps({"choices": [{"message": {"content": "{\"say\": \"hi\"}"}}], "usage": usage}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


SCHEMA = {"type": "object", "properties": {"say": {"type": "string"}}, "required": ["say"]}


def test_the_vllm_profile_routes_every_tier_to_the_vllm_provider():
    cat = load_catalog()
    for tier in ("local_fast", "balanced", "frontier_reasoning"):
        assert [(t.provider, t.model) for t in cat.chain("vllm", tier)] == [("vllm", "local")]
    prov = cat.providers["vllm"]
    assert prov["kind"] == "openai_compat" and prov["local"] is True and prov["supports_json_schema"] is True


def test_url_comes_from_the_environment_and_a_request_carries_the_schema_cap_and_key(stub, monkeypatch):
    monkeypatch.setenv("SILICONFORGE_VLLM_URL", stub.url)
    monkeypatch.setenv("VLLM_API_KEY", "k-123")
    provider = build_provider("vllm", load_catalog())
    assert provider.base_url == stub.url
    for streaming in (True, False):
        provider.stream = streaming
        res = provider.generate("local", "system text", "user text", temperature=0.0, json_schema=SCHEMA)
        assert res.content == '{"say": "hi"}' and res.provider == "vllm"
        assert (res.prompt_tokens, res.completion_tokens, res.cached_tokens) == (120, 4, 100)  # vLLM's prefix-cache hits are read
    for body in stub.seen:
        assert body["model"] == "local" and body["max_tokens"] == 6000
        assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["schema"] == SCHEMA
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "cache_prompt" not in body  # that is a llama.cpp field; vLLM caches prefixes on its own
    assert set(stub.auth) == {"Bearer k-123"}


def test_through_the_router_with_cost_and_cache_accounting(stub, monkeypatch):
    monkeypatch.setenv("SILICONFORGE_VLLM_URL", stub.url)
    cfg = InferenceConfig(profile="vllm", enable_semantic_cache=False)
    tracker = CostTracker()
    router = ModelRouter(cfg, tracker)
    resp = router.complete("PLAN", "agent_step", "sys", "usr", temperature=0.0, bypass_cache=True, json_schema=SCHEMA)
    assert json.loads(resp.content) == {"say": "hi"}
    assert tracker.total_tokens == 124 and tracker.total_cost_usd == 0.0  # a server you run has no per-token price


def test_a_busy_server_is_a_transient_error_carrying_retry_after(monkeypatch):
    busy = Stub(fail_with=429)
    try:
        monkeypatch.setenv("SILICONFORGE_VLLM_URL", busy.url)
        provider = build_provider("vllm", load_catalog())
        provider.stream = False
        with pytest.raises(TransientProviderError) as err:
            provider._request(provider._payload("local", "s", "u", 0.0, None), "local", "su")
        assert err.value.retry_after == 7.0  # the server's Retry-After is honoured by the retry loop
    finally:
        busy.close()


def test_without_the_environment_variable_the_default_local_url_is_used(monkeypatch):
    monkeypatch.delenv("SILICONFORGE_VLLM_URL", raising=False)
    assert build_provider("vllm", load_catalog()).base_url == "http://127.0.0.1:8000/v1"
