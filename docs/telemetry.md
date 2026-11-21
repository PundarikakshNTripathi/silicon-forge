# Telemetry: tracing and agent metrics

SiliconForge records what an agent did as **spans**, and turns them into four figures that say whether it is any good: how long
it took and where the time went, whether its tools worked, what a solved task cost, and whether it recovers from mistakes.
Everything is metadata. A span holds a tool name, an error class, token counts and a cost; never a prompt, a tool argument or a
tool's output, so a trace is as safe to keep as the usage ledger.

## Turning it on

```bash
siliconforge chat --trace              # or: agent --trace "task"; writes .siliconforge/traces/<time>.jsonl
siliconforge trace summary             # every trace in this workspace; or pass files; --json for machine output
```

The API server records every agent session into its SQLite database automatically (same metadata-only spans):

```bash
curl localhost:8000/api/insights/agent?hours=24     # the four figures as JSON
curl localhost:8000/api/metrics                     # Prometheus text format, for a scraper
```

Both honour `SILICONFORGE_API_TOKEN` like every other endpoint.

## What a trace looks like

`run > step > llm | tool`. A **run** is one user turn (`invoke_agent`), a **step** is one loop iteration, an **llm** span is one
model call (provider, model, tokens, time to first token, cost) and a **tool** span is one tool call (name, permission class,
result size, error class). Durations come from a monotonic clock; start times are wall-clock so spans from different processes
line up. Time a human spends deciding on an approval prompt is recorded as `approval_wait_s` and kept **out** of the tool's
duration, so a slow reviewer does not make a tool look slow.

## The four figures, as defined in code (`telemetry/metrics.py`)

1. **End-to-end latency**, split into *reasoning* (time inside model calls), *action* (time inside tools, minus any overlap with
   reasoning) and *overhead* (everything else: framework code, scheduling, parsing, waiting on a human). Computed from interval
   unions, so parallel tool calls are not double counted and the three parts sum to the whole.
2. **Tool success rate** = calls without an error / (those + calls that failed). A call refused by policy (denied in `plan`
   mode, blocked by a deny rule or a hook, declined by the user) is **not** a failure and is counted separately. Also reported: the
   share of calls whose arguments were valid on the first try, and a failure taxonomy (`bad_args`, `tool_error`, `timeout`,
   `unknown_tool`, `crash`).
3. **Cost per task**: USD, tokens and model calls per run, and per **solved** run (total over all runs divided by runs solved),
   because a cheap agent that rarely succeeds is expensive per success. Local models are priced at $0, so tokens and time are the
   honest cost there. The solved-based figures are `None` unless a verdict was attached to the run; they are never guessed.
4. **Failure recovery.** A *failure* is a tool call that failed or a model reply that was not a valid action. It is *recovered* if
   the agent later succeeds at the same thing: for a tool, a later successful call of the same tool in the same run; for an invalid
   action, the next reply being valid. Reported: recovery rate, median steps and tokens spent recovering, and, for runs that had a
   failure, the share that still ended solved.

## Prometheus and OpenTelemetry

`/api/metrics` exposes runs by status, cost and tokens, agent tool calls by tool and outcome (`ok`, `failed`, `refused`), model
calls by provider and model, tokens, invalid actions, a run-time histogram, time by phase and recovery counts. Counters are
cumulative over the stored data.

For OpenTelemetry (Phoenix, Langfuse, Jaeger, Grafana Tempo, any OTLP collector):

```bash
pip install "siliconforge[otel]"
```

```python
from services.orchestrator.telemetry import Tracer
from services.orchestrator.telemetry.otel import OtlpSink, make_provider

tracer = Tracer(sinks=[OtlpSink(make_provider("http://localhost:4318/v1/traces"))])
session = create_session(ws, tracer=tracer)
```

Spans follow the OpenTelemetry GenAI conventions: `invoke_agent siliconforge`, `chat <model>`, `execute_tool <tool>`, with
`gen_ai.usage.input_tokens`, `gen_ai.tool.name` and `error.type`; SiliconForge-specific attributes sit under `siliconforge.`.
Exported timestamps are the measured ones. Those upstream conventions are still marked experimental, so names may change.

## Limits

- The metrics are only as good as the spans: a tool that blocks without being wrapped is invisible. Built-in tools, MCP tools and
  hooks all run through the instrumented path; a framework-built agent is measured at the same two seams (see the agent benchmark).
- Pipeline runs (spec to GDSII) keep their own event stream; they are not yet expressed as spans.
- "Solved" needs an outside verdict. In the benchmark that is the task's verifier; in ordinary use there is none, so the
  per-solved figures stay empty.
- Recovery is defined by the rule above. A different, reasonable rule would give different numbers, which is why the rule is written
  down and tested.
