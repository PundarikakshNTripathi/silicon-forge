# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenAI-compatible chat-completions provider: llama.cpp `llama-server`, OpenAI, Gemini (compat endpoint), Ollama, LM Studio.

Streams responses so time-to-first-token and decode speed are measured, supports JSON-schema constrained decoding
(llama.cpp grammar / OpenAI structured outputs), and surfaces server-side prompt-cache hits.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from services.orchestrator.inference.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
    TransientProviderError,
    parse_retry_after,
    retry_call,
)


class OpenAICompatibleProvider(BaseLLMProvider):
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        name: str = "openai_compat",
        local: bool = False,
        supports_json_schema: bool = False,
        cache_prompt: bool = False,
        send_temperature: bool = True,
        max_tokens: int = 8192,
        max_tokens_param: str = "max_tokens",
        timeout_s: float = 300.0,
        stream: bool = True,
        extra_body: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.name = name
        self.local = local
        self.supports_json_schema = supports_json_schema
        self.cache_prompt = cache_prompt
        self.send_temperature = send_temperature
        self.max_tokens = max_tokens
        self.max_tokens_param = max_tokens_param
        self.stream = stream
        self.extra_body = extra_body or {}
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout_s, connect=10.0))

    # ----------------------------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(
        self, model: str, system_prompt: str, user_prompt: str, temperature: float, json_schema: dict | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            self.max_tokens_param: self.max_tokens,
            **self.extra_body,
        }
        if self.send_temperature:
            payload["temperature"] = temperature
        if self.cache_prompt:
            payload["cache_prompt"] = True  # llama.cpp: reuse the KV cache for the shared prefix
        if json_schema is not None and self.supports_json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "structured_output", "schema": json_schema},
            }
        if self.stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        payload = self._payload(model, system_prompt, user_prompt, temperature, json_schema)
        return retry_call(lambda: self._request(payload, model, system_prompt + user_prompt))

    def _request(self, payload: dict[str, Any], model: str, prompt_text: str) -> LLMResponse:
        t0 = time.perf_counter()
        url = f"{self.base_url}/chat/completions"
        try:
            if self.stream:
                return self._request_stream(url, payload, model, prompt_text, t0)
            resp = self._client.post(url, headers=self._headers(), json=payload)
            self._raise_for_status(resp)
            return self._parse_body(resp.json(), model, prompt_text, time.perf_counter() - t0, None)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientProviderError(f"{self.name}: {type(exc).__name__}: {exc}") from exc

    def _request_stream(
        self, url: str, payload: dict[str, Any], model: str, prompt_text: str, t0: float
    ) -> LLMResponse:
        parts: list[str] = []
        ttft: float | None = None
        usage: dict[str, Any] = {}
        timings: dict[str, Any] = {}
        with self._client.stream("POST", url, headers=self._headers(), json=payload) as resp:
            if resp.status_code >= 400:
                resp.read()
                self._raise_for_status(resp)
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                        parts.append(delta)
                usage = chunk.get("usage") or usage
                timings = chunk.get("timings") or timings
        body = {"choices": [{"message": {"content": "".join(parts)}}], "usage": usage, "timings": timings}
        return self._parse_body(body, model, prompt_text, time.perf_counter() - t0, ttft)

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientProviderError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}",
                                         retry_after=parse_retry_after(resp.headers.get("retry-after")))
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:500]}")

    def _parse_body(
        self, body: dict[str, Any], model: str, prompt_text: str, latency: float, ttft: float | None
    ) -> LLMResponse:
        content = body["choices"][0]["message"].get("content") or ""
        usage = body.get("usage") or {}
        timings = body.get("timings") or {}
        details = usage.get("prompt_tokens_details") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or max(1, len(prompt_text) // 4))
        completion_tokens = int(usage.get("completion_tokens") or max(1, len(content) // 4))
        # OpenAI: prompt_tokens_details.cached_tokens ; llama.cpp: timings.cache_n (tokens reused from the KV cache)
        cached = int(details.get("cached_tokens") or timings.get("cache_n") or 0)
        tps = timings.get("predicted_per_second")
        if tps is None and ttft is not None and completion_tokens > 1 and latency > ttft:
            tps = (completion_tokens - 1) / (latency - ttft)
        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model,
            cached_tokens=cached,
            latency_s=round(latency, 4),
            ttft_s=round(ttft, 4) if ttft is not None else None,
            provider=self.name,
            tokens_per_s=round(float(tps), 2) if tps else None,
            extra={"timings": timings} if timings else {},
        )

    def health(self) -> dict:
        root = self.base_url.removesuffix("/v1")
        try:
            r = self._client.get(f"{root}/health", timeout=3.0)
            if r.status_code == 200:
                return {"ok": True, "detail": r.json() if r.headers.get("content-type", "").startswith("application/json") else "ok"}
            r = self._client.get(f"{self.base_url}/models", headers=self._headers(), timeout=5.0)
            return {"ok": r.status_code == 200}
        except httpx.HTTPError as exc:
            return {"ok": False, "detail": str(exc)}
