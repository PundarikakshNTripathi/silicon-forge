# SPDX-License-Identifier: AGPL-3.0-or-later
"""The measuring proxy: forwards unchanged, records each chat call once, understands plain and streamed responses."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from services.orchestrator.agentbench.proxy import MeasuringProxy, parse_response


class Upstream:
    """A fake model server: answers with whatever `reply` currently holds, after an optional delay."""

    def __init__(self):
        self.reply = (200, "application/json", b"{}")
        self.delay = 0.0
        self.seen: list[tuple[str, dict, str]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                outer.seen.append((self.path, json.loads(body or b"{}"), self.headers.get("authorization", "")))
                time.sleep(outer.delay)
                status, ctype, payload = outer.reply
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stack():
    up = Upstream()
    proxy = MeasuringProxy(up.url).start()
    yield up, proxy
    proxy.stop()
    up.close()


COMPLETION = {
    "choices": [{"message": {"content": "hi", "tool_calls": [
        {"function": {"name": "read_file", "arguments": '{"path": "a.sv"}'}}]}, "finish_reason": "tool_calls"}],
    "usage": {"prompt_tokens": 321, "completion_tokens": 17},
    "timings": {"predicted_per_second": 41.5, "cache_n": 300},
}


def post(proxy, body, path="/chat/completions"):
    return httpx.post(proxy.base_url + path, json=body, headers={"Authorization": "Bearer k"}, timeout=10)


def test_forwards_unchanged_and_records_what_the_server_reported(stack):
    up, proxy = stack
    up.reply = (200, "application/json", json.dumps(COMPLETION).encode())
    up.delay = 0.12
    t0 = time.time()
    r = post(proxy, {"model": "m", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 200 and r.json() == COMPLETION  # the client sees the upstream's bytes
    path, body, auth = up.seen[0]
    assert path == "/v1/chat/completions" and body["messages"][0]["content"] == "x" and auth == "Bearer k"
    (rec,) = proxy.records_between(t0 - 1, time.time() + 1)
    assert (rec.prompt_tokens, rec.completion_tokens, rec.cached_tokens, rec.tokens_per_s) == (321, 17, 300, 41.5)
    assert rec.finish_reason == "tool_calls" and rec.tool_call_names == ["read_file"] and not rec.bad_tool_arguments
    assert rec.status == 200 and rec.duration_s >= 0.12 and not rec.failed


def test_invalid_tool_arguments_and_length_cutoffs_are_visible():
    bad = {"choices": [{"message": {"tool_calls": [{"function": {"name": "eda", "arguments": '{"tool": "yos'}}]},
                        "finish_reason": "length"}], "usage": {"prompt_tokens": 5, "completion_tokens": 9}}
    info = parse_response(json.dumps(bad).encode(), "application/json")
    assert info["bad_tool_arguments"] is True and info["finish_reason"] == "length" and info["tool_call_names"] == ["eda"]


def test_a_streamed_response_is_folded_into_one_record():
    chunks = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "read_", "arguments": '{"pa'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "file", "arguments": 'th": "a"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 4}},
    ]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    info = parse_response(sse.encode(), "text/event-stream")
    assert info["tool_call_names"] == ["read_file"] and not info["bad_tool_arguments"]
    assert (info["prompt_tokens"], info["completion_tokens"], info["finish_reason"]) == (10, 4, "tool_calls")


def test_http_errors_are_recorded_as_failed_calls(stack):
    up, proxy = stack
    up.reply = (500, "application/json", b'{"error": "boom"}')
    r = post(proxy, {"model": "m"})
    assert r.status_code == 500  # passed through
    assert proxy.records[-1].failed and proxy.records[-1].status == 500


def test_an_unreachable_upstream_is_a_502_and_a_recorded_error():
    proxy = MeasuringProxy("http://127.0.0.1:1").start()  # nothing listens on port 1
    try:
        r = post(proxy, {"model": "m"})
        assert r.status_code == 502 and "unreachable" in r.json()["error"]["message"]
        assert proxy.records[-1].failed and proxy.records[-1].error
    finally:
        proxy.stop()


def test_only_chat_completions_are_recorded_and_windows_filter_by_time(stack):
    up, proxy = stack
    up.reply = (200, "application/json", json.dumps(COMPLETION).encode())
    post(proxy, {}, path="/models")
    assert proxy.records == []
    post(proxy, {"a": 1})
    mid = time.time()
    time.sleep(0.02)
    post(proxy, {"a": 2})
    assert len(proxy.records) == 2
    assert len(proxy.records_between(mid, time.time() + 1)) == 1 and len(proxy.records_between(0, mid)) == 1
