# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spans and metrics: every expected figure here is plain arithmetic on hand-built spans."""
from __future__ import annotations

import pytest

from services.orchestrator.telemetry import (
    JsonlSink,
    NullTracer,
    Span,
    Tracer,
    aggregate,
    percentile,
    read_spans,
    run_metrics,
    summarize,
)


def sp(kind, start, dur, name="x", status="ok", error="", trace="t1", **attrs):
    return Span(trace_id=trace, span_id=f"{kind}{start}", parent_id=None, name=name, kind=kind, start=start, duration_s=dur,
                status=status, error=error, attrs=attrs)


# ------------------------------------------------------------------------------------------------ tracer

class FakeTime:
    def __init__(self):
        self.t = 100.0

    def clock(self):
        return self.t

    def perf(self):
        return self.t

    def advance(self, s):
        self.t += s


def test_spans_nest_and_time_themselves():
    ft = FakeTime()
    tr = Tracer(clock=ft.clock, perf=ft.perf)
    with tr.span("invoke_agent", "run") as run:
        ft.advance(1)
        with tr.span("step 1", "step") as step:
            with tr.span("chat", "llm", tokens_in=10) as llm:
                ft.advance(2)
            with tr.span("read_file", "tool") as tool:
                ft.advance(0.5)
        ft.advance(1)
    assert [s.name for s in tr.spans] == ["chat", "read_file", "step 1", "invoke_agent"]  # children finish first
    assert llm.parent_id == step.span_id and tool.parent_id == step.span_id and step.parent_id == run.span_id
    assert {s.trace_id for s in tr.spans} == {run.trace_id}
    assert (llm.duration_s, tool.duration_s, run.duration_s) == (2.0, 0.5, 4.5)
    assert llm.attrs == {"tokens_in": 10} and run.start == 100.0


def test_an_exception_marks_the_span_an_error_and_still_propagates():
    tr = Tracer()
    with pytest.raises(ValueError), tr.span("t", "tool"):
        raise ValueError("secret-path-C:/Users/someone")
    assert tr.spans[0].status == "error" and tr.spans[0].error == "ValueError"
    assert "someone" not in str(tr.spans[0].to_dict())  # the message could hold a path or a secret: only the class is kept


def test_unknown_kind_is_rejected_and_the_null_tracer_records_nothing():
    with pytest.raises(ValueError, match="span kind"), Tracer().span("x", "database"):
        pass
    nt = NullTracer()
    with nt.span("anything", "tool", a=1) as s:
        s.fail("denied")
    assert nt.spans == []


def test_a_failing_sink_cannot_break_the_traced_code():
    def bad(_s):
        raise RuntimeError("disk full")

    tr = Tracer(sinks=[bad])
    with tr.span("x", "tool"):
        pass
    assert len(tr.spans) == 1


def test_jsonl_roundtrip_skips_a_torn_line(tmp_path):
    path = tmp_path / "traces" / "a.jsonl"
    tr = Tracer(sinks=[JsonlSink(path)])
    with tr.span("run", "run", solved=True), tr.span("t", "tool", cost=0.5):
        pass
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"trace_id": "x", "span_i')  # killed mid-write
    back = read_spans(path)
    assert [s.name for s in back] == ["t", "run"] and back[1].attrs["solved"] is True and back[0].duration_s >= 0
    assert read_spans(tmp_path / "missing.jsonl") == []


# ----------------------------------------------------------------------------------------------- latency

def test_latency_split_uses_interval_unions_so_parallel_calls_are_not_double_counted():
    spans = [
        sp("run", 0, 10, "run"),
        sp("llm", 0, 3), sp("llm", 6, 2),                       # reasoning: 5 s
        sp("tool", 3, 2, "a"), sp("tool", 3.5, 2, "b"),          # overlapping tools: union 3..5.5 = 2.5 s of action
    ]
    m = run_metrics(spans)
    assert m["e2e_s"] == 10 and m["reasoning_s"] == 5 and m["action_s"] == 2.5
    assert m["overhead_s"] == 2.5  # 10 - 5 - 2.5
    assert m["reasoning_s"] + m["action_s"] + m["overhead_s"] == m["e2e_s"]


def test_a_tool_running_inside_a_model_call_counts_as_reasoning_only():
    m = run_metrics([sp("run", 0, 4, "run"), sp("llm", 0, 4), sp("tool", 1, 1, "a")])
    assert (m["reasoning_s"], m["action_s"], m["overhead_s"]) == (4, 0, 0)


# -------------------------------------------------------------------------------------------- tool success

def test_policy_refusals_are_not_tool_failures_and_failures_are_classified():
    spans = [sp("run", 0, 10, "run")]
    spans += [sp("tool", i, 0.1, "read_file") for i in range(6)]                                     # 6 ok
    spans += [sp("tool", 6, 0.1, "eda", "error", "tool_error"), sp("tool", 7, 0.1, "eda", "error", "bad_args")]
    spans += [sp("tool", 8, 0.1, "write_file", "error", "denied"), sp("tool", 9, 0.1, "write_file", "error", "blocked")]
    m = run_metrics(spans)
    assert (m["tool_ok"], m["tool_failed"], m["policy_blocks"]) == (6, 2, 2)
    assert m["tool_success_rate"] == 0.75  # 6 / (6 + 2): the two refusals are outside the denominator
    assert m["failures_by_class"] == {"tool_error": 1, "bad_args": 1}
    assert m["args_valid_first_try"] == 0.9  # 1 bad-args call of 10


def test_no_tool_calls_gives_none_not_zero_or_one():
    m = run_metrics([sp("run", 0, 1, "run"), sp("llm", 0, 1)])
    assert m["tool_success_rate"] is None and m["args_valid_first_try"] is None


# ------------------------------------------------------------------------------------------------ recovery

def test_tool_failure_is_recovered_by_a_later_success_of_the_same_tool():
    spans = [sp("run", 0, 20, "run", solved=True),
             sp("step", 0, 1), sp("step", 5, 1), sp("step", 10, 1), sp("step", 15, 1),
             sp("llm", 1, 1, tokens_in=100, tokens_out=10), sp("llm", 6, 1, tokens_in=200, tokens_out=20),
             sp("llm", 11, 1, tokens_in=300, tokens_out=30),
             sp("tool", 2, 1, "eda", "error", "tool_error"),   # fails; ends at 3
             sp("tool", 7, 1, "read_file"),                     # another tool succeeding does not recover it
             sp("tool", 12, 1, "eda")]                          # same tool succeeds; ends at 13: recovered
    m = run_metrics(spans)
    assert (m["failures"], m["recovered"]) == (1, 1)
    assert m["steps_to_recover"] == [2]                         # steps that began between 3 and 13: those at 5 and 10
    assert m["tokens_to_recover"] == [200 + 20 + 300 + 30]      # model calls that began between 3 and 13


def test_an_unrecovered_failure_and_invalid_actions():
    spans = [sp("run", 0, 10, "run", solved=False), sp("step", 0, 1),
             sp("llm", 0, 1, "chat", "error", "invalid_action"), sp("llm", 2, 1, tokens_in=10, tokens_out=5),   # next reply valid
             sp("tool", 4, 1, "eda", "error", "timeout")]                                                        # never recovered
    m = run_metrics(spans)
    assert (m["failures"], m["recovered"], m["invalid_actions"]) == (2, 1, 1)


# --------------------------------------------------------------------------------------- cost, aggregate

def make_run(trace, solved, cost, e2e=10.0, fail=False, tokens=100):
    s = [sp("run", 0, e2e, "run", trace=trace, solved=solved),
         sp("llm", 0, e2e / 2, trace=trace, tokens_in=tokens, tokens_out=0, cost_usd=cost),
         sp("tool", e2e / 2, e2e / 4, "read_file", trace=trace)]
    if fail:
        s.append(sp("tool", e2e * 0.8, 0.1, "eda", "error", "tool_error", trace=trace))
        s.append(sp("tool", e2e * 0.9, 0.1, "eda", trace=trace))
    return s


def test_cost_per_solved_is_total_cost_over_solved_runs_not_over_all_runs():
    spans = make_run("a", True, 0.10) + make_run("b", False, 0.30) + make_run("c", False, 0.20) + make_run("d", True, 0.40)
    agg = summarize(spans)
    assert agg["runs"] == 4 and agg["solved"] == 2 and agg["success_rate"] == 0.5
    assert agg["cost"]["usd_total"] == pytest.approx(1.0) and agg["cost"]["usd_per_run"] == pytest.approx(0.25)
    assert agg["cost"]["usd_per_solved"] == pytest.approx(0.5)  # a cheap agent that rarely solves is expensive per success
    assert agg["cost"]["tokens_per_solved"] == 200.0


def test_without_verdicts_solved_figures_are_none_not_guessed():
    s = [sp("run", 0, 1, "run"), sp("llm", 0, 1, tokens_in=5, cost_usd=0.1)]
    agg = aggregate([run_metrics(s)])
    assert agg["success_rate"] is None and agg["cost"]["usd_per_solved"] is None and agg["solved"] is None


def test_pooled_rates_and_resilience():
    spans = make_run("a", True, 0.1, fail=True) + make_run("b", False, 0.1, fail=True) + make_run("c", True, 0.1)
    agg = summarize(spans)
    assert agg["tools"]["calls"] == 3 + 3 + 1  # a and b: read_file + a failed and a successful eda; c: read_file
    assert agg["recovery"]["failures"] == 2 and agg["recovery"]["rate"] == 1.0
    assert agg["recovery"]["runs_with_failure"] == 2 and agg["recovery"]["solved_despite_failure"] == 0.5
    lat = agg["latency"]
    assert lat["reasoning_share"] + lat["action_share"] + lat["overhead_share"] == pytest.approx(1.0, abs=1e-3)


def test_percentiles():
    assert percentile([], 50) is None and percentile([7.0], 95) == 7.0
    assert percentile([1, 2, 3, 4], 50) == 2.5 and percentile([1, 2, 3, 4, 5], 95) == pytest.approx(4.8)
    assert aggregate([]) == {"runs": 0}
