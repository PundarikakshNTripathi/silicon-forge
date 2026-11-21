# SPDX-License-Identifier: AGPL-3.0-or-later
"""A measuring proxy in front of an OpenAI-compatible model server.

Every arm talks to this proxy instead of to llama-server, so each model call is timed and counted in exactly one place,
independent of what any framework reports about itself: wall time per call, HTTP status, prompt and completion tokens from the
server's own `usage`, decode speed and cache reuse from llama.cpp's `timings`, the finish reason, and which tools the model asked
for (and whether their arguments were valid JSON).

Calls are forwarded unchanged. A streamed response is buffered and then returned in one piece, which does not alter what is
measured here (the proxy times the upstream) but does change when the client sees the first byte; the benchmark therefore asks
every arm for non-streaming responses. Standard library plus httpx. Sequential runs let a call be attributed to a run by time
window, so the proxy keeps only what it needs for that.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from services.orchestrator.agentbench.arms.base import LlmRecord

HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "accept-encoding"}


def parse_response(raw: bytes, content_type: str) -> dict[str, Any]:
    """Fold a chat-completions response (plain JSON or a buffered SSE stream) into the few fields we record."""
    out: dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0, "tokens_per_s": None, "cached_tokens": 0,
                           "finish_reason": "", "tool_call_names": [], "bad_tool_arguments": False}
    text = raw.decode("utf-8", errors="replace")
    objs: list[dict[str, Any]] = []
    if "text/event-stream" in content_type or text.lstrip().startswith("data:"):
        for line in text.splitlines():
            if line.startswith("data:") and "[DONE]" not in line:
                try:
                    objs.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    continue
    else:
        try:
            objs.append(json.loads(text))
        except json.JSONDecodeError:
            return out
    calls: dict[tuple[int, int], dict[str, str]] = {}
    for obj in objs:
        usage = obj.get("usage") or {}
        out["prompt_tokens"] = int(usage.get("prompt_tokens") or out["prompt_tokens"])
        out["completion_tokens"] = int(usage.get("completion_tokens") or out["completion_tokens"])
        timings = obj.get("timings") or {}
        if timings.get("predicted_per_second"):
            out["tokens_per_s"] = float(timings["predicted_per_second"])
        if timings.get("cache_n"):
            out["cached_tokens"] = int(timings["cache_n"])
        for ci, choice in enumerate(obj.get("choices") or []):
            out["finish_reason"] = choice.get("finish_reason") or out["finish_reason"]
            msg = choice.get("message") or {}
            for ti, tc in enumerate(msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                calls[(ci, ti)] = {"name": fn.get("name", ""), "args": fn.get("arguments") or ""}
            for tc in (choice.get("delta") or {}).get("tool_calls") or []:  # streamed: pieces keyed by index
                fn = tc.get("function") or {}
                slot = calls.setdefault((ci, int(tc.get("index", 0))), {"name": "", "args": ""})
                slot["name"] += fn.get("name") or ""
                slot["args"] += fn.get("arguments") or ""
    for call in calls.values():
        out["tool_call_names"].append(call["name"])
        if call["args"].strip():
            try:
                json.loads(call["args"])
            except json.JSONDecodeError:
                out["bad_tool_arguments"] = True
    return out


class MeasuringProxy:
    def __init__(self, upstream: str, host: str = "127.0.0.1", port: int = 0, timeout_s: float = 600.0) -> None:
        self.upstream = upstream.rstrip("/")
        self.records: list[LlmRecord] = []
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=timeout_s)
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a: Any) -> None:  # silence the default stderr access log
                pass

            def _forward(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
                t0 = time.time()
                p0 = time.perf_counter()
                try:
                    resp = proxy._client.request(self.command, proxy.upstream + self.path, content=body, headers=headers)
                except httpx.HTTPError as exc:
                    proxy._record(LlmRecord(start=t0, duration_s=time.perf_counter() - p0, status=502,
                                            error=type(exc).__name__))
                    payload = json.dumps({"error": {"message": f"upstream unreachable: {type(exc).__name__}"}}).encode()
                    self.send_response(502)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                dur = time.perf_counter() - p0
                if "chat/completions" in self.path:
                    info = parse_response(resp.content, resp.headers.get("content-type", ""))
                    proxy._record(LlmRecord(start=t0, duration_s=dur, status=resp.status_code, **info))
                self.send_response(resp.status_code)
                self.send_header("content-type", resp.headers.get("content-type", "application/json"))
                self.send_header("content-length", str(len(resp.content)))
                self.end_headers()
                self.wfile.write(resp.content)

            do_POST = do_GET = _forward

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="agentbench-proxy", daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def _record(self, rec: LlmRecord) -> None:
        with self._lock:
            self.records.append(rec)

    def records_between(self, t0: float, t1: float) -> list[LlmRecord]:
        with self._lock:
            return [r for r in self.records if t0 <= r.start <= t1]

    def start(self) -> MeasuringProxy:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._client.close()

    def __enter__(self) -> MeasuringProxy:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
