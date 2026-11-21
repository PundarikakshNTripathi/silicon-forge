# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenTelemetry mapping: GenAI names and attributes, parent links, original timing, error status. Needs the optional SDK."""
from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from opentelemetry.trace import StatusCode  # noqa: E402

from services.orchestrator.telemetry import Span, Tracer  # noqa: E402
from services.orchestrator.telemetry.otel import OtlpSink, genai_attrs, genai_name, make_provider  # noqa: E402


class Fake:
    def __init__(self):
        self.t = 1_700_000_000.0

    def clock(self):
        return self.t

    def perf(self):
        return self.t

    def advance(self, s):
        self.t += s


def run_traced():
    ft = Fake()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tr = Tracer(sinks=[OtlpSink(provider)], clock=ft.clock, perf=ft.perf)
    with tr.span("invoke_agent", "run", solved=True, stopped="done"):
        with tr.span("step 1", "step"):
            with tr.span("chat", "llm", model="qwen3.5-9b", provider="llamacpp", tokens_in=120, tokens_out=30, cost_usd=0.0,
                         ttft_s=0.2):
                ft.advance(2.0)
            with tr.span("eda", "tool", permission="command") as bad:
                ft.advance(0.5)
                bad.fail("timeout")
    return exporter.get_finished_spans()


def test_names_attributes_and_hierarchy_follow_the_genai_conventions():
    spans = {s.name: s for s in run_traced()}
    assert set(spans) == {"invoke_agent siliconforge", "step 1", "chat qwen3.5-9b", "execute_tool eda"}
    run, chat, tool = spans["invoke_agent siliconforge"], spans["chat qwen3.5-9b"], spans["execute_tool eda"]
    assert run.attributes["gen_ai.operation.name"] == "invoke_agent" and run.attributes["gen_ai.agent.name"] == "siliconforge"
    assert chat.attributes["gen_ai.operation.name"] == "chat" and chat.attributes["gen_ai.request.model"] == "qwen3.5-9b"
    assert chat.attributes["gen_ai.usage.input_tokens"] == 120 and chat.attributes["gen_ai.usage.output_tokens"] == 30
    assert tool.attributes["gen_ai.operation.name"] == "execute_tool" and tool.attributes["gen_ai.tool.name"] == "eda"
    assert chat.attributes["siliconforge.ttft_s"] == 0.2 and run.attributes["siliconforge.solved"] is True
    # parent links reproduce the nesting: tool and chat under the step, the step under the run
    step = spans["step 1"]
    assert chat.parent.span_id == step.context.span_id == tool.parent.span_id and step.parent.span_id == run.context.span_id
    assert len({s.context.trace_id for s in spans.values()}) == 1


def test_a_failed_tool_is_an_error_span_with_the_error_class():
    tool = next(s for s in run_traced() if s.name == "execute_tool eda")
    assert tool.status.status_code == StatusCode.ERROR and tool.attributes["error.type"] == "timeout"


def test_exported_timestamps_are_the_measured_ones_not_the_export_time():
    spans = {s.name: s for s in run_traced()}
    chat, tool = spans["chat qwen3.5-9b"], spans["execute_tool eda"]
    assert (chat.end_time - chat.start_time) / 1e9 == pytest.approx(2.0, abs=1e-6)
    assert (tool.end_time - tool.start_time) / 1e9 == pytest.approx(0.5, abs=1e-6)
    assert chat.start_time == int(1_700_000_000.0 * 1e9)  # the original wall-clock start, not "now"


def test_no_content_leaks_into_exported_attributes():
    sp = Span(trace_id="t", span_id="s", parent_id=None, name="read_file", kind="tool", start=0.0,
              attrs={"permission": "read", "result_chars": 12, "nested": {"a": 1}, "args": ["secret"]})
    attrs = genai_attrs(sp)
    assert "siliconforge.nested" not in attrs and attrs["siliconforge.result_chars"] == 12  # only scalars are exported
    assert genai_name(sp) == "execute_tool read_file"


def test_make_provider_without_an_endpoint_exports_nowhere():
    provider = make_provider()
    assert provider.get_tracer("x") is not None  # builds, and has no span processors to send to a network
