# SPDX-License-Identifier: AGPL-3.0-or-later
"""The real arms (our loop, LangGraph, Pydantic AI) driven end to end by a scripted fake model server through the measuring
proxy: each adapter must expose the same tools, pass arguments through unvalidated, return the final text, honour the step
limit, and be measured by the same code. No model and no GPU involved."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.orchestrator.agentbench import tasks as T
from services.orchestrator.agentbench.arms.custom import CustomArm
from services.orchestrator.agentbench.proxy import MeasuringProxy
from services.orchestrator.agentbench.runner import run_one


class ScriptedModel:
    """An OpenAI-compatible server that answers the n-th request with the n-th canned message and keeps what it was sent."""

    def __init__(self, messages):
        self.messages, self.requests = list(messages), []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                outer.requests.append(body)
                i = min(len(outer.requests) - 1, len(outer.messages) - 1)
                msg, finish = outer.messages[i]
                payload = json.dumps({"id": f"r{i}", "object": "chat.completion", "created": 0, "model": "local",
                                      "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                                      "usage": {"prompt_tokens": 100 + i, "completion_tokens": 10, "total_tokens": 110 + i},
                                      "timings": {"predicted_per_second": 40.0}}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def tool_call(name, args, cid="c1"):
    return ({"role": "assistant", "content": None,
             "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}, "tool_calls")


def text(t):
    return ({"role": "assistant", "content": t}, "stop")


def action(say, tool=None, **args):
    """What our own loop expects: one JSON action object as the message content."""
    return ({"role": "assistant", "content": json.dumps({"say": say, "tool": tool, "args": args})}, "stop")


def run_arm(arm, task_id, messages, **kw):
    model = ScriptedModel(messages)
    proxy = MeasuringProxy(model.url).start()
    try:
        rec = run_one(T.BY_ID[task_id], arm, 0, proxy, base_url=proxy.base_url, **kw)
        return rec, model
    finally:
        proxy.stop()
        model.close()


# the same story told in each arm's own protocol: read a mistyped path (fails), list the folder, answer 4
WRONG_PATH_FRAMEWORK = [tool_call("read_file", {"path": "rtl/couter.sv"}), tool_call("list_dir", {"path": "rtl"}), text("ANSWER: 4")]
WRONG_PATH_CUSTOM = [action("", "read_file", path="rtl/couter.sv"), action("", "list_dir", path="rtl"), action("ANSWER: 4")]


def common_checks(rec, model):
    assert rec.solved and rec.stopped == "done", (rec.reasons, rec.error)
    m = rec.metrics
    assert (m["llm_calls"], m["tool_calls"], m["tool_ok"], m["tool_failed"]) == (3, 2, 1, 1)
    assert m["failures_by_class"] == {"tool_error": 1} and m["tokens_in"] == 100 + 101 + 102 and m["tokens_out"] == 30
    assert m["tokens_per_s_p50"] == 40.0 and rec.first_prompt_tokens == 100
    assert m["reasoning_s"] > 0 and m["reasoning_s"] + m["action_s"] + m["overhead_s"] == pytest.approx(m["e2e_s"], abs=5e-3)


def test_custom_arm_end_to_end():
    rec, model = run_arm(CustomArm(), "wrong_path", WRONG_PATH_CUSTOM)
    common_checks(rec, model)
    body = model.requests[0]
    assert body["response_format"]["type"] == "json_schema" and not body.get("stream")  # constrained actions, non-streaming
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_custom_arm_reports_invalid_replies_and_unknown_tools():
    msgs = [({"role": "assistant", "content": "I will just talk."}, "stop"), action("", "no_such_tool"), action("ANSWER: 4")]
    rec, _ = run_arm(CustomArm(), "wrong_path", msgs)
    assert rec.metrics["invalid_actions"] == 1 and rec.metrics["failures_by_class"].get("unknown_tool") == 1
    assert rec.solved  # it recovered and answered


def test_pydantic_ai_arm_end_to_end():
    pytest.importorskip("pydantic_ai")
    from services.orchestrator.agentbench.arms.pydantic_ai_arm import PydanticAiArm

    rec, model = run_arm(PydanticAiArm(), "wrong_path", WRONG_PATH_FRAMEWORK)
    common_checks(rec, model)
    sent = model.requests[0]
    names = {t["function"]["name"] for t in sent["tools"]}
    assert names == {"list_dir", "read_file", "grep", "write_file", "edit_file", "lint_rtl"}  # no eda: no backend in this run
    read = next(t for t in sent["tools"] if t["function"]["name"] == "read_file")["function"]["parameters"]
    assert read["required"] == ["path"] and read["additionalProperties"] is False
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}


def test_langgraph_arm_end_to_end():
    pytest.importorskip("langgraph")
    from services.orchestrator.agentbench.arms.langgraph_arm import LangGraphArm

    rec, model = run_arm(LangGraphArm(), "wrong_path", WRONG_PATH_FRAMEWORK)
    common_checks(rec, model)
    sent = model.requests[0]
    assert {t["function"]["name"] for t in sent["tools"]} == {"list_dir", "read_file", "grep", "write_file", "edit_file", "lint_rtl"}
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("arm_name", ["pydantic_ai", "langgraph"])
def test_framework_arms_pass_bad_arguments_to_the_shared_tool_seam(arm_name):
    """A framework must not swallow a malformed call: it has to reach the shared seam so every arm is scored on it."""
    pytest.importorskip("pydantic_ai" if arm_name == "pydantic_ai" else "langgraph")
    from services.orchestrator.agentbench.arms.langgraph_arm import LangGraphArm
    from services.orchestrator.agentbench.arms.pydantic_ai_arm import PydanticAiArm

    arm = PydanticAiArm() if arm_name == "pydantic_ai" else LangGraphArm()
    msgs = [tool_call("read_file", {"wrong_name": "x"}), tool_call("read_file", {"path": "rtl/counter.sv"}), text("ANSWER: 4")]
    rec, _ = run_arm(arm, "wrong_path", msgs)
    assert rec.metrics["failures_by_class"] == {"bad_args": 1}, rec.metrics["failures_by_class"]
    assert rec.metrics["args_valid_first_try"] == 0.5 and rec.solved


@pytest.mark.parametrize("arm_name", ["pydantic_ai", "langgraph"])
def test_framework_arms_stop_at_the_step_limit(arm_name):
    pytest.importorskip("pydantic_ai" if arm_name == "pydantic_ai" else "langgraph")
    from services.orchestrator.agentbench.arms.langgraph_arm import LangGraphArm
    from services.orchestrator.agentbench.arms.pydantic_ai_arm import PydanticAiArm

    arm = PydanticAiArm() if arm_name == "pydantic_ai" else LangGraphArm()
    forever = [tool_call("list_dir", {"path": "rtl"}, cid=f"c{i}") for i in range(50)]
    rec, model = run_arm(arm, "wrong_path", forever, max_steps=3)
    assert rec.stopped == "step_limit" and not rec.solved
    assert len(model.requests) <= 4  # the budget was honoured (a framework may need one more request to notice)


def test_a_framework_asking_for_a_nonexistent_tool_is_counted_as_a_failure():
    pytest.importorskip("pydantic_ai")
    from services.orchestrator.agentbench.arms.pydantic_ai_arm import PydanticAiArm

    msgs = [tool_call("teleport", {"to": "mars"}), tool_call("list_dir", {"path": "rtl"}), text("ANSWER: 4")]
    rec, _ = run_arm(PydanticAiArm(), "wrong_path", msgs)
    assert rec.metrics["failures_by_class"].get("unknown_tool") == 1  # seen at the HTTP seam, not at the (never-reached) tool seam
