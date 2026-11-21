# SPDX-License-Identifier: AGPL-3.0-or-later
"""Export spans as OpenTelemetry traces using the GenAI semantic conventions, so Phoenix, Langfuse, Jaeger, Grafana Tempo or any
OTLP collector can show them. Optional: needs `pip install "siliconforge[otel]"`; nothing else in SiliconForge imports it.

Mapping (the conventions are still marked experimental upstream, so names may move):

| SiliconForge span | OTel name                | `gen_ai.operation.name` |
| ----------------- | ------------------------ | ----------------------- |
| run               | `invoke_agent siliconforge` | `invoke_agent`       |
| llm               | `chat <model>`           | `chat`                  |
| tool              | `execute_tool <tool>`    | `execute_tool`          |
| step              | `step N` (internal)      | (none)                  |

Token counts go to `gen_ai.usage.input_tokens` / `output_tokens`, the tool name to `gen_ai.tool.name`, a failure to span status
ERROR with `error.type` set to our error class. Anything SiliconForge-specific (cost, TTFT, permission, verdict) is under the
`siliconforge.` prefix. Like the rest of the telemetry this carries no prompts, arguments or tool output.

Spans finish children-first, so `OtlpSink` buffers a trace and exports it, parents first, when its run span finishes. Timestamps
are the originals (nanoseconds), so exported durations equal the measured ones rather than the time of export.
"""
from __future__ import annotations

from typing import Any

from services.orchestrator.telemetry.trace import Span

AGENT_NAME = "siliconforge"
OPERATION = {"run": "invoke_agent", "llm": "chat", "tool": "execute_tool"}


def genai_name(span: Span) -> str:
    if span.kind == "run":
        return f"invoke_agent {AGENT_NAME}"
    if span.kind == "llm":
        return f"chat {span.attrs.get('model', '')}".strip()
    if span.kind == "tool":
        return f"execute_tool {span.name}"
    return span.name


def genai_attrs(span: Span) -> dict[str, Any]:
    a: dict[str, Any] = {}
    op = OPERATION.get(span.kind)
    if op:
        a["gen_ai.operation.name"] = op
    if span.kind == "run":
        a["gen_ai.agent.name"] = AGENT_NAME
    if span.kind == "llm":
        if span.attrs.get("model"):
            a["gen_ai.request.model"] = span.attrs["model"]
        if span.attrs.get("provider"):
            a["gen_ai.provider.name"] = span.attrs["provider"]
        if "tokens_in" in span.attrs:
            a["gen_ai.usage.input_tokens"] = int(span.attrs["tokens_in"])
        if "tokens_out" in span.attrs:
            a["gen_ai.usage.output_tokens"] = int(span.attrs["tokens_out"])
    if span.kind == "tool":
        a["gen_ai.tool.name"] = span.name
    if span.status == "error":
        a["error.type"] = span.error or "error"
    handled = {"model", "provider", "tokens_in", "tokens_out"}
    for k, v in span.attrs.items():
        if k not in handled and isinstance(v, (str, bool, int, float)):
            a[f"siliconforge.{k}"] = v
    return a


class OtlpSink:
    """A `Tracer` sink that forwards finished runs to an OpenTelemetry `TracerProvider`."""

    def __init__(self, provider: Any) -> None:
        self._tracer = provider.get_tracer("siliconforge", schema_url="https://opentelemetry.io/schemas/1.37.0")
        self._buffer: dict[str, list[Span]] = {}

    def __call__(self, span: Span) -> None:
        self._buffer.setdefault(span.trace_id, []).append(span)
        if span.kind == "run":
            self.flush(span.trace_id)

    def flush(self, trace_id: str) -> None:
        from opentelemetry import trace as otel_trace
        from opentelemetry.trace import Status, StatusCode

        buffered = self._buffer.pop(trace_id, [])
        by_id = {s.span_id: s for s in buffered}

        def depth(s: Span) -> int:  # parents before children even when their start times tie (coarse clocks do tie)
            d, cur = 0, s
            while cur.parent_id in by_id and d < len(by_id):
                cur, d = by_id[cur.parent_id], d + 1
            return d

        spans = sorted(buffered, key=lambda s: (depth(s), s.start))
        made: dict[str, Any] = {}
        for sp in spans:
            parent = made.get(sp.parent_id or "")
            ctx = otel_trace.set_span_in_context(parent) if parent is not None else None
            start_ns = int(sp.start * 1e9)
            o = self._tracer.start_span(genai_name(sp), context=ctx, start_time=start_ns, attributes=genai_attrs(sp))
            if sp.status == "error":
                o.set_status(Status(StatusCode.ERROR, sp.error or "error"))
            made[sp.span_id] = o
        for sp in reversed(spans):  # children end before parents
            made[sp.span_id].end(end_time=int((sp.start + sp.duration_s) * 1e9))


def make_provider(endpoint: str | None = None, service_name: str = "siliconforge") -> Any:
    """A `TracerProvider`; with an OTLP/HTTP `endpoint` (e.g. http://localhost:4318/v1/traces) spans are sent there."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    return provider
