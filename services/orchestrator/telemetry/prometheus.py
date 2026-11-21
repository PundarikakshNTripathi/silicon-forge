# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prometheus text exposition (format 0.0.4) of run and agent metrics, for `GET /api/metrics`.

Everything is derived from what the server already stores (runs and spans), so there is no second source of truth and no
new dependency. Counters here are *cumulative over the stored data* (they reset only if the database is cleared), which is
what a scraper expects of a counter; the latency histogram is cumulative as well.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from services.orchestrator.telemetry.metrics import INVALID_ACTION, POLICY_ERRORS, run_metrics
from services.orchestrator.telemetry.trace import Span

BUCKETS = (0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600)


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(**kw: str) -> str:
    return "{" + ",".join(f'{k}="{_esc(v)}"' for k, v in kw.items()) + "}" if kw else ""


class _Out:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def metric(self, name: str, kind: str, help_: str) -> None:
        self.lines += [f"# HELP {name} {help_}", f"# TYPE {name} {kind}"]

    def sample(self, name: str, value: float, **labels: str) -> None:
        v = int(value) if float(value).is_integer() else round(float(value), 6)
        self.lines.append(f"{name}{_labels(**labels)} {v}")


def render(runs: dict[str, Any], spans: list[Span]) -> str:
    o = _Out()
    o.metric("siliconforge_runs", "gauge", "Pipeline runs by status.")
    for status, n in sorted(runs.get("by_status", {}).items()):
        o.sample("siliconforge_runs", n, status=status)
    o.metric("siliconforge_run_cost_usd_total", "counter", "Model cost of all pipeline runs, USD.")
    o.sample("siliconforge_run_cost_usd_total", runs.get("cost_usd", 0.0))
    o.metric("siliconforge_run_tokens_total", "counter", "Model tokens used by all pipeline runs.")
    o.sample("siliconforge_run_tokens_total", runs.get("tokens", 0))
    o.metric("siliconforge_run_duration_seconds", "summary", "Wall time of finished pipeline runs.")
    o.sample("siliconforge_run_duration_seconds_sum", runs.get("duration_s_sum", 0.0))
    o.sample("siliconforge_run_duration_seconds_count", runs.get("duration_n", 0))
    o.metric("siliconforge_signoff_passed_total", "counter", "Pipeline runs whose signoff closed.")
    o.sample("siliconforge_signoff_passed_total", runs.get("signoff_passed", 0))

    # agent spans -> counters
    tools: Counter[tuple[str, str]] = Counter()
    llm: Counter[tuple[str, str]] = Counter()
    tokens = {"input": 0, "output": 0}
    cost = 0.0
    invalid = 0
    by_trace: dict[str, list[Span]] = {}
    for s in spans:
        by_trace.setdefault(s.trace_id, []).append(s)
        if s.kind == "tool":
            outcome = "ok" if s.status == "ok" else ("refused" if s.error in POLICY_ERRORS else "failed")
            tools[(s.name, outcome)] += 1
        elif s.kind == "llm":
            llm[(str(s.attrs.get("provider", "")), str(s.attrs.get("model", "")))] += 1
            tokens["input"] += int(s.attrs.get("tokens_in", 0))
            tokens["output"] += int(s.attrs.get("tokens_out", 0))
            cost += float(s.attrs.get("cost_usd", 0.0))
            invalid += int(s.status == "error" and s.error == INVALID_ACTION)
    o.metric("siliconforge_agent_tool_calls_total", "counter", "Agent tool calls by tool and outcome (ok, failed, refused by policy).")
    for (tool, outcome), n in sorted(tools.items()):
        o.sample("siliconforge_agent_tool_calls_total", n, tool=tool, outcome=outcome)
    o.metric("siliconforge_agent_llm_calls_total", "counter", "Agent model calls by provider and model.")
    for (prov, model), n in sorted(llm.items()):
        o.sample("siliconforge_agent_llm_calls_total", n, provider=prov, model=model)
    o.metric("siliconforge_agent_tokens_total", "counter", "Agent model tokens by direction.")
    for direction, n in tokens.items():
        o.sample("siliconforge_agent_tokens_total", n, direction=direction)
    o.metric("siliconforge_agent_cost_usd_total", "counter", "Agent model cost, USD.")
    o.sample("siliconforge_agent_cost_usd_total", cost)
    o.metric("siliconforge_agent_invalid_actions_total", "counter", "Model replies that were not a valid action.")
    o.sample("siliconforge_agent_invalid_actions_total", invalid)

    per_run = [m for m in (run_metrics(v) for v in by_trace.values()) if m]
    o.metric("siliconforge_agent_run_seconds", "histogram", "End-to-end wall time of agent runs.")
    cum = 0
    for b in BUCKETS:
        cum = sum(1 for m in per_run if m["e2e_s"] <= b)
        o.sample("siliconforge_agent_run_seconds_bucket", cum, le=str(b))
    o.sample("siliconforge_agent_run_seconds_bucket", len(per_run), le="+Inf")
    o.sample("siliconforge_agent_run_seconds_sum", sum(m["e2e_s"] for m in per_run))
    o.sample("siliconforge_agent_run_seconds_count", len(per_run))
    o.metric("siliconforge_agent_phase_seconds_total", "counter", "Agent wall time by phase: reasoning, action (tools), overhead.")
    for phase, key in (("reasoning", "reasoning_s"), ("action", "action_s"), ("overhead", "overhead_s")):
        o.sample("siliconforge_agent_phase_seconds_total", sum(m[key] for m in per_run), phase=phase)
    o.metric("siliconforge_agent_recovery_total", "counter", "Agent failures by whether the agent later recovered.")
    o.sample("siliconforge_agent_recovery_total", sum(m["recovered"] for m in per_run), outcome="recovered")
    o.sample("siliconforge_agent_recovery_total", sum(m["failures"] - m["recovered"] for m in per_run), outcome="unrecovered")
    return "\n".join(o.lines) + "\n"
