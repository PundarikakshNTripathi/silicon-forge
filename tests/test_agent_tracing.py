# SPDX-License-Identifier: AGPL-3.0-or-later
"""A scripted agent run through every outcome class, checking the spans and the metrics computed from them."""
from __future__ import annotations

import json
import time

import pytest

from services.orchestrator.agent.session import AgentSession
from services.orchestrator.agent.tools import ToolBox
from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.catalog import Catalog, Target
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
)
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.telemetry import JsonlSink, Tracer, read_spans, run_metrics, summarize
from services.orchestrator.workspace import init_workspace


class Script(BaseLLMProvider):
    name = "script"
    local = True

    def __init__(self, replies):
        self.replies = list(replies)

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(content=json.dumps(reply), prompt_tokens=50, completion_tokens=10, model=model, provider="script",
                           ttft_s=0.01, tokens_per_s=100.0)


def act(say="", tool=None, **args):
    return {"say": say, "tool": tool, "args": args}


@pytest.fixture
def make(tmp_path):
    ws = init_workspace(tmp_path / "p", profile="dev", backend="simulated")
    (ws.root / "a.txt").write_text("alpha\n")
    catalog = Catalog(providers={"script": {"kind": "replay", "local": True}},
                      profiles={"dev": {t: [Target("script", "m")] for t in ("local_fast", "balanced", "frontier_reasoning")}})

    def build(replies, mode="auto", approver=None, tracer=None):
        router = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(), provider=Script(replies),
                             catalog=catalog)
        tr = tracer or Tracer()
        return AgentSession(router, ws, ToolBox(ws), mode=mode, approver=approver, tracer=tr), tr

    return ws, build


def kinds(tr):
    return [s.kind for s in tr.spans]


def test_a_normal_turn_gives_nested_spans_and_consistent_metrics(make):
    _, build = make
    s, tr = build([act("", "read_file", path="a.txt"), act("done")])
    res = s.send("read it")
    assert res.stopped == "done"
    assert sorted(kinds(tr)) == ["llm", "llm", "run", "step", "step", "tool"]
    run = next(x for x in tr.spans if x.kind == "run")
    assert run.attrs["stopped"] == "done" and run.attrs["steps"] == 2 and run.status == "ok"
    assert {x.trace_id for x in tr.spans} == {run.trace_id}
    tool = next(x for x in tr.spans if x.kind == "tool")
    assert tool.name == "read_file" and tool.attrs["permission"] == "read" and tool.attrs["result_chars"] > 0
    m = run_metrics(tr.spans)
    assert (m["llm_calls"], m["tool_calls"], m["steps"], m["tokens_in"], m["tokens_out"]) == (2, 1, 2, 100, 20)
    assert m["tool_success_rate"] == 1.0 and m["failures"] == 0 and m["cost_usd"] == 0.0
    assert m["ttft_p50_s"] == 0.01 and m["tokens_per_s_p50"] == 100.0
    assert m["reasoning_s"] + m["action_s"] + m["overhead_s"] == pytest.approx(m["e2e_s"], abs=1e-3)


def test_bad_arguments_are_classified_then_recovered_by_a_correct_call(make):
    _, build = make
    s, tr = build([act("", "read_file", wrong_name="a.txt"), act("", "read_file", path="a.txt"), act("done")])
    s.send("read it")
    m = run_metrics(tr.spans)
    assert m["failures_by_class"] == {"bad_args": 1} and m["tool_ok"] == 1 and m["tool_failed"] == 1
    assert m["args_valid_first_try"] == 0.5
    assert (m["failures"], m["recovered"]) == (1, 1)
    # steps are counted by timestamp; on Windows' coarse clock two adjacent steps can share a tick and both count, so one or two
    assert len(m["steps_to_recover"]) == 1 and m["steps_to_recover"][0] in (1, 2)


def test_unknown_tool_is_a_failure_and_is_not_recovered_by_other_calls(make):
    _, build = make
    s, tr = build([act("", "no_such_tool"), act("", "read_file", path="a.txt"), act("done")])
    s.send("x")
    m = run_metrics(tr.spans)
    assert m["failures_by_class"] == {"unknown_tool": 1} and (m["failures"], m["recovered"]) == (1, 0)


def test_a_refusal_by_policy_is_not_a_tool_failure(make):
    _, build = make
    s, tr = build([act("", "write_file", path="b.txt", content="x"), act("ok")], mode="plan")
    s.send("write")
    m = run_metrics(tr.spans)
    assert m["policy_blocks"] == 1 and m["tool_failed"] == 0 and m["tool_success_rate"] is None and m["failures"] == 0
    assert next(x for x in tr.spans if x.kind == "tool").error == "denied"


def test_an_invalid_model_reply_is_marked_on_the_llm_span_and_recovery_is_counted(make):
    _, build = make
    s, tr = build([act("", "read_file", path="a.txt"), "this is not an action object", act("done")])
    # the scripted provider JSON-encodes the string, which parse_action rejects (not an object with 'say')
    s.send("x")
    bad = [x for x in tr.spans if x.kind == "llm" and x.status == "error"]
    assert len(bad) == 1 and bad[0].error == "invalid_action"
    m = run_metrics(tr.spans)
    assert m["invalid_actions"] == 1 and m["recovered"] >= 1


def test_a_provider_failure_marks_the_llm_and_run_spans(make):
    _, build = make
    s, tr = build([ProviderError("upstream down")])
    res = s.send("x")
    assert res.stopped == "error"
    llm = next(x for x in tr.spans if x.kind == "llm")
    run = next(x for x in tr.spans if x.kind == "run")
    assert (llm.status, llm.error, run.status, run.error) == ("error", "provider_error", "error", "error")
    assert "upstream down" not in json.dumps([x.to_dict() for x in tr.spans])  # error text could hold anything: only the class is kept


def test_human_approval_wait_is_not_counted_as_tool_time(make):
    _, build = make

    def slow_approver(kind, summary, details):
        time.sleep(0.25)
        return True

    s, tr = build([act("", "write_file", path="b.txt", content="hi"), act("done")], mode="ask", approver=slow_approver)
    s.send("write")
    tool = next(x for x in tr.spans if x.kind == "tool")
    assert tool.attrs["approval_wait_s"] >= 0.25 and tool.duration_s < 0.2
    m = run_metrics(tr.spans)
    assert m["action_s"] < 0.2 and m["overhead_s"] >= 0.2  # the wait shows up as overhead, not as a slow tool


def test_spans_stream_to_a_jsonl_file_and_summarize_reads_them_back(make, tmp_path):
    _, build = make
    path = tmp_path / "t.jsonl"
    s, _ = build([act("", "read_file", path="a.txt"), act("done")], tracer=Tracer(sinks=[JsonlSink(path)]))
    s.send("a")
    s2, _ = build([act("done")], tracer=Tracer(sinks=[JsonlSink(path)]))
    s2.send("b")
    agg = summarize(read_spans(path))
    assert agg["runs"] == 2 and agg["tools"]["calls"] == 1 and agg["model"]["calls"] == 3
    # no prompt text, tool arguments or tool output are in the file
    text = path.read_text(encoding="utf-8")
    assert "alpha" not in text and "a.txt" not in text


def test_the_default_tracer_records_nothing(make):
    ws, build = make
    from services.orchestrator.telemetry import NullTracer

    s, tr = build([act("", "read_file", path="a.txt"), act("done")], tracer=NullTracer())
    assert s.send("x").stopped == "done" and tr.spans == []


def test_trace_summary_command(make, tmp_path, capsys):
    from services.orchestrator.main import cli_main

    _, build = make
    path = tmp_path / "t.jsonl"
    s, _ = build([act("", "read_file", path="a.txt"), act("done")], tracer=Tracer(sinks=[JsonlSink(path)]))
    s.send("read")
    assert cli_main(["trace", "summary", str(path)]) == 0
    out = capsys.readouterr().out
    assert "1 run(s)" in out and "End-to-end latency" in out and "success rate 100.0%" in out and "Failure recovery" in out
    assert cli_main(["trace", "summary", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["tools"]["calls"] == 1
    assert cli_main(["trace", "summary", str(tmp_path / "missing.jsonl")]) == 1
