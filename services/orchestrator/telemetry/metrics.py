# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn spans into the four numbers an agent should be judged on, with the definitions written down.

1. **End-to-end latency**, split into *reasoning* (time inside model calls), *action* (time inside tools, minus any overlap with
   reasoning) and *overhead* (everything else: framework code, scheduling, parsing). Computed from interval unions, so parallel
   tool calls are not double counted and the three parts never exceed the whole.
2. **Tool success rate** = calls that returned without error / (those + calls that failed). A call refused by policy (denied,
   blocked by a rule or hook, declined by the user) is not a tool failure and is reported separately. Also: the share of calls
   whose arguments were valid on the first try, and a failure taxonomy by error class.
3. **Cost per task**: USD, tokens and model calls per run, and per *solved* run (total over all runs / runs solved), because a cheap
   agent that rarely solves the task is expensive per success.
4. **Failure recovery.** A *failure* is a tool call that failed or a model reply that was not a valid action. It is *recovered* if
   the agent later succeeds at the same thing: for a tool failure, a later successful call of the same tool in the same run; for an
   invalid action, the next model reply being valid. Reported: recovery rate, steps and tokens spent recovering, and, for runs that
   had at least one failure, the share that still ended solved.

`solved` comes from the run span's `solved` attribute, set by whoever knows the verdict (the benchmark's verifier); without it the
solved-based figures are None rather than guessed.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any

from services.orchestrator.telemetry.trace import Span

#: refusals by policy, not tool failures
POLICY_ERRORS = frozenset({"denied", "blocked", "declined"})
INVALID_ACTION = "invalid_action"


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile, q in [0, 100]. None for an empty list."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _length(intervals: list[tuple[float, float]]) -> float:
    return sum(e - s for s, e in intervals)


def _minus(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The parts of `a` not covered by `b` (both already merged)."""
    out: list[tuple[float, float]] = []
    for s, e in a:
        cur = s
        for bs, be in b:
            if be <= cur or bs >= e:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
        if cur < e:
            out.append((cur, e))
    return out


def _iv(sp: Span) -> tuple[float, float]:
    return (sp.start, sp.start + sp.duration_s)


def run_metrics(spans: list[Span]) -> dict[str, Any]:
    """Metrics for the spans of ONE trace (one agent run / task attempt)."""
    run = next((s for s in spans if s.kind == "run"), None) or (min(spans, key=lambda s: s.start) if spans else None)
    if run is None:
        return {}
    llms = sorted((s for s in spans if s.kind == "llm"), key=lambda s: s.start)
    tools = sorted((s for s in spans if s.kind == "tool"), key=lambda s: s.start)
    steps = [s for s in spans if s.kind == "step"]

    llm_u = _union([_iv(s) for s in llms])
    tool_u = _minus(_union([_iv(s) for s in tools]), llm_u)
    both = _union(llm_u + tool_u)
    e2e = run.duration_s
    reasoning, action = _length(llm_u), _length(tool_u)
    overhead = max(0.0, e2e - _length(both))

    ok = [t for t in tools if t.status == "ok"]
    policy = [t for t in tools if t.status == "error" and t.error in POLICY_ERRORS]
    failed = [t for t in tools if t.status == "error" and t.error not in POLICY_ERRORS]
    by_error = Counter(t.error or "unknown" for t in failed)
    attempted = len(ok) + len(failed)
    bad_args = sum(1 for t in failed if t.error == "bad_args")

    tokens_in = sum(int(s.attrs.get("tokens_in", 0)) for s in llms)
    tokens_out = sum(int(s.attrs.get("tokens_out", 0)) for s in llms)
    cost = sum(float(s.attrs.get("cost_usd", 0.0)) for s in llms)
    ttfts = [float(s.attrs["ttft_s"]) for s in llms if s.attrs.get("ttft_s")]
    tps = [float(s.attrs["tokens_per_s"]) for s in llms if s.attrs.get("tokens_per_s")]

    recoveries = _recoveries(llms, tools, steps)
    solved = run.attrs.get("solved")
    return {
        "trace_id": run.trace_id,
        "solved": solved if isinstance(solved, bool) else None,
        "stopped": run.attrs.get("stopped"),
        "e2e_s": round(e2e, 4), "reasoning_s": round(reasoning, 4), "action_s": round(action, 4), "overhead_s": round(overhead, 4),
        "steps": len(steps) or len(llms), "llm_calls": len(llms), "tool_calls": len(tools),
        "tool_ok": len(ok), "tool_failed": len(failed), "policy_blocks": len(policy),
        "tool_success_rate": round(len(ok) / attempted, 4) if attempted else None,
        "args_valid_first_try": round(1 - bad_args / len(tools), 4) if tools else None,
        "failures_by_class": dict(by_error),
        "invalid_actions": sum(1 for s in llms if s.status == "error" and s.error == INVALID_ACTION),
        "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": round(cost, 6),
        "ttft_p50_s": percentile(ttfts, 50), "tokens_per_s_p50": percentile(tps, 50),
        "failures": recoveries["failures"], "recovered": recoveries["recovered"],
        "steps_to_recover": recoveries["steps"], "tokens_to_recover": recoveries["tokens"],
    }


def _recoveries(llms: list[Span], tools: list[Span], steps: list[Span]) -> dict[str, Any]:
    failures = recovered = 0
    steps_to: list[int] = []
    tokens_to: list[int] = []
    step_starts = sorted(s.start for s in steps)

    def steps_between(a: float, b: float) -> int:
        return sum(1 for t in step_starts if a <= t <= b)

    def tokens_between(a: float, b: float) -> int:
        return sum(int(s.attrs.get("tokens_in", 0)) + int(s.attrs.get("tokens_out", 0)) for s in llms if a <= s.start <= b)

    for i, t in enumerate(tools):
        if t.status != "error" or t.error in POLICY_ERRORS:
            continue
        failures += 1
        later = next((u for u in tools[i + 1:] if u.name == t.name and u.status == "ok"), None)
        if later is not None:
            recovered += 1
            end_a, end_b = t.start + t.duration_s, later.start + later.duration_s
            steps_to.append(max(1, steps_between(end_a, end_b)))
            tokens_to.append(tokens_between(end_a, end_b))
    for i, s in enumerate(llms):
        if s.status == "error" and s.error == INVALID_ACTION:
            failures += 1
            nxt = llms[i + 1] if i + 1 < len(llms) else None
            if nxt is not None and not (nxt.status == "error" and nxt.error == INVALID_ACTION):
                recovered += 1
                steps_to.append(1)
                tokens_to.append(int(nxt.attrs.get("tokens_in", 0)) + int(nxt.attrs.get("tokens_out", 0)))
    return {"failures": failures, "recovered": recovered, "steps": steps_to, "tokens": tokens_to}


def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool per-run metrics. Rates are pooled over calls (not averaged over runs), so one long run counts as much as it did."""
    runs = [r for r in runs if r]
    if not runs:
        return {"runs": 0}
    judged = [r for r in runs if r["solved"] is not None]
    solved_n = sum(1 for r in judged if r["solved"])
    ok, failed = sum(r["tool_ok"] for r in runs), sum(r["tool_failed"] for r in runs)
    calls = sum(r["tool_calls"] for r in runs)
    bad_args = calls - sum((r["args_valid_first_try"] if r["args_valid_first_try"] is not None else 1.0) * r["tool_calls"] for r in runs)
    failures, recovered = sum(r["failures"] for r in runs), sum(r["recovered"] for r in runs)
    classes: Counter[str] = Counter()
    for r in runs:
        classes.update(r["failures_by_class"])
    cost, tok = sum(r["cost_usd"] for r in runs), sum(r["tokens_in"] + r["tokens_out"] for r in runs)
    e2e = [r["e2e_s"] for r in runs]
    had_failure = [r for r in judged if r["failures"] > 0]
    steps_to = [x for r in runs for x in r["steps_to_recover"]]
    tokens_to = [x for r in runs for x in r["tokens_to_recover"]]
    total_e2e = sum(e2e) or 1.0
    return {
        "runs": len(runs), "judged": len(judged), "solved": solved_n if judged else None,
        "success_rate": round(solved_n / len(judged), 4) if judged else None,
        "latency": {
            "e2e_p50_s": percentile(e2e, 50), "e2e_p95_s": percentile(e2e, 95), "e2e_mean_s": round(statistics.fmean(e2e), 4),
            "reasoning_share": round(sum(r["reasoning_s"] for r in runs) / total_e2e, 4),
            "action_share": round(sum(r["action_s"] for r in runs) / total_e2e, 4),
            "overhead_share": round(sum(r["overhead_s"] for r in runs) / total_e2e, 4),
            "ttft_p50_s": percentile([r["ttft_p50_s"] for r in runs if r["ttft_p50_s"]], 50),
        },
        "tools": {
            "calls": calls, "success_rate": round(ok / (ok + failed), 4) if (ok + failed) else None,
            "args_valid_first_try": round(1 - bad_args / calls, 4) if calls else None,
            "policy_blocks": sum(r["policy_blocks"] for r in runs), "failures_by_class": dict(classes),
        },
        "model": {"calls": sum(r["llm_calls"] for r in runs), "invalid_actions": sum(r["invalid_actions"] for r in runs),
                  "steps_mean": round(statistics.fmean(r["steps"] for r in runs), 3)},
        "cost": {
            "usd_total": round(cost, 6), "tokens_total": tok,
            "usd_per_run": round(cost / len(runs), 6), "tokens_per_run": round(tok / len(runs), 1),
            "usd_per_solved": round(cost / solved_n, 6) if solved_n else None,
            "tokens_per_solved": round(tok / solved_n, 1) if solved_n else None,
        },
        "recovery": {
            "failures": failures, "recovered": recovered, "rate": round(recovered / failures, 4) if failures else None,
            "steps_to_recover_median": percentile([float(x) for x in steps_to], 50),
            "tokens_to_recover_median": percentile([float(x) for x in tokens_to], 50),
            "runs_with_failure": len(had_failure),
            "solved_despite_failure": round(sum(1 for r in had_failure if r["solved"]) / len(had_failure), 4) if had_failure else None,
        },
    }


def summarize(spans: list[Span]) -> dict[str, Any]:
    by_trace: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        by_trace[s.trace_id].append(s)
    return aggregate([run_metrics(v) for v in by_trace.values()])
