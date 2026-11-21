# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn run records into the benchmark's result document and a readable report.

The unit of analysis is the **task**, not the run: a task's score is its success rate over trials, and intervals come from
resampling tasks (`evals/stats.py`), because tasks are what would differ in another benchmark. With about a dozen tasks the
intervals are wide, and the report says so instead of declaring winners: a paired difference is called "separated" only when its
interval excludes zero. Skipped tasks (those that need an EDA backend that was not available) are listed, never counted.
"""
from __future__ import annotations

import statistics
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from typing import Any

from services.orchestrator.agentbench.runner import RunRecord
from services.orchestrator.evals.stats import bootstrap_ci, paired_difference
from services.orchestrator.telemetry import aggregate
from services.orchestrator.telemetry.report import render as render_metrics

SCHEMA = "siliconforge.agentbench/1"


def _task_scores(runs: list[RunRecord], lenient: bool = False) -> dict[str, float]:
    """Per task: the share of runs that passed. Strict by default; `lenient` uses the secondary content-correct reading."""
    by_task: dict[str, list[bool]] = defaultdict(list)
    for r in runs:
        by_task[r.task].append(r.content_ok if lenient else (r.solved and not r.violations))
    return {t: sum(v) / len(v) for t, v in by_task.items()}


def _tool_totals(runs: list[RunRecord]) -> dict[str, dict[str, int]]:
    """{tool: {outcome: calls}} summed over runs: which tools an arm leans on and which of them fail, and how."""
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for r in runs:
        for key, n in r.tool_breakdown.items():
            tool, _, outcome = key.partition(":")
            out[tool][outcome] += n
    return {t: dict(c) for t, c in sorted(out.items())}


def summarize(records: list[RunRecord]) -> dict[str, Any]:
    ran = [r for r in records if not r.skipped]
    arms = sorted({r.arm for r in ran})
    skipped = sorted({(r.task, r.reasons[0] if r.reasons else "") for r in records if r.skipped})
    per_arm: dict[str, Any] = {}
    scores: dict[str, dict[str, float]] = {}
    for arm in arms:
        runs = [r for r in ran if r.arm == arm]
        ts = _task_scores(runs)
        scores[arm] = ts
        vals = list(ts.values())
        lo, hi = bootstrap_ci(vals, seed=0)
        cvals = list(_task_scores(runs, lenient=True).values())
        clo, chi = bootstrap_ci(cvals, seed=0)
        by_cat: dict[str, list[float]] = defaultdict(list)
        for t, s in ts.items():
            by_cat[next(r.category for r in runs if r.task == t)].append(s)
        solved_runs = sum(1 for r in runs if r.solved and not r.violations)
        stopped = Counter(r.stopped for r in runs if not (r.solved and not r.violations))
        per_arm[arm] = {
            "runs": len(runs), "tasks": len(ts), "solved_runs": solved_runs,
            "task_success": {"mean": statistics.fmean(vals), "ci95": [lo, hi]},
            "task_content_ok": {"mean": statistics.fmean(cvals), "ci95": [clo, chi]},  # secondary: forgives a missing format line
            "by_category": {c: round(statistics.fmean(v), 4) for c, v in sorted(by_cat.items())},
            "by_task": {t: round(s, 4) for t, s in sorted(ts.items())},
            "safety_violation_runs": sum(1 for r in runs if r.violations),
            "violations": sorted({v for r in runs for v in r.violations}),
            "unsolved_by_stop": dict(stopped),
            "first_prompt_tokens_median": statistics.median([r.first_prompt_tokens for r in runs if r.first_prompt_tokens])
            if any(r.first_prompt_tokens for r in runs) else None,
            "metrics": aggregate([r.metrics for r in runs if r.metrics]),
            "tool_breakdown": _tool_totals(runs),
        }
    paired: dict[str, Any] = {}
    base = "custom" if "custom" in arms else (arms[0] if arms else None)
    for arm in arms:
        if arm == base or base is None:
            continue
        common = sorted(set(scores[arm]) & set(scores[base]))
        if common:
            d = paired_difference([scores[arm][t] for t in common], [scores[base][t] for t in common])
            paired[f"{arm} - {base}"] = {**d, "tasks": common}
    return {"arms": per_arm, "paired_vs_baseline": paired, "baseline_arm": base,
            "skipped": [{"task": t, "reason": why} for t, why in skipped]}


def build_doc(records: list[RunRecord], meta: dict[str, Any]) -> dict[str, Any]:
    return {"schema": SCHEMA, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **meta, "summary": summarize(records),
            "runs": [asdict(r) for r in records]}


def render(doc: dict[str, Any]) -> str:
    s = doc["summary"]
    lines = [f"AgentBench-SF: {doc.get('model', '?')}, {doc.get('trials', '?')} trial(s) per task", ""]
    for arm, a in s["arms"].items():
        ts = a["task_success"]
        lines.append(f"[{arm}] task success {ts['mean'] * 100:.1f}% (95% interval {ts['ci95'][0] * 100:.1f} to {ts['ci95'][1] * 100:.1f}) over "
                     f"{a['tasks']} tasks, {a['solved_runs']}/{a['runs']} runs; safety violations in {a['safety_violation_runs']} run(s)")
        co = a.get("task_content_ok")
        if co and abs(co["mean"] - ts["mean"]) > 1e-9:
            lines.append(f"  content-correct (secondary; forgives a missing answer-format line): {co['mean'] * 100:.1f}% "
                         f"({co['ci95'][0] * 100:.1f} to {co['ci95'][1] * 100:.1f})")
        if a["by_category"]:
            lines.append("  by category: " + ", ".join(f"{c} {v * 100:.0f}%" for c, v in a["by_category"].items()))
        if a["first_prompt_tokens_median"]:
            lines.append(f"  first model call: {a['first_prompt_tokens_median']:.0f} prompt tokens (the framework's fixed overhead)")
        lines += ["  " + ln for ln in render_metrics(a["metrics"]).splitlines()[1:]]
        lines.append("")
    for name, d in s["paired_vs_baseline"].items():
        verdict = "the interval excludes 0" if d["separated"] else "the interval includes 0: not separated"
        lines.append(f"{name}: {d['mean'] * 100:+.1f} points (95% interval {d['ci95'][0] * 100:+.1f} to {d['ci95'][1] * 100:+.1f}) over "
                     f"{d['problems']} tasks; {verdict}")
    for sk in s["skipped"]:
        lines.append(f"skipped {sk['task']}: {sk['reason']}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------- the same arm over time (drift)

def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(b - a, 4)


def compare_docs(before: dict[str, Any], after: dict[str, Any], max_drop: float = 0.10) -> dict[str, Any]:
    """Did an arm get worse between two runs of the same benchmark (a later model version, a changed prompt, a framework upgrade)?

    Per arm present in both files: the paired difference in per-task success (after minus before, over tasks present in both)
    with a bootstrap interval, and the change in the headline metrics. An arm is flagged as a **regression** only when the mean
    drop is at least `max_drop` *and* the interval excludes zero, so a noisy dozen tasks cannot raise a false alarm by themselves.
    The mean drop alone is reported separately, since a drop the interval cannot rule out is still worth a look."""
    out: dict[str, Any] = {"max_drop": max_drop, "arms": {}, "regressions": []}
    for arm in sorted(set(before["summary"]["arms"]) & set(after["summary"]["arms"])):
        a, b = before["summary"]["arms"][arm], after["summary"]["arms"][arm]
        common = sorted(set(a["by_task"]) & set(b["by_task"]))
        if not common:
            continue
        d = paired_difference([b["by_task"][t] for t in common], [a["by_task"][t] for t in common])
        am, bm = a["metrics"], b["metrics"]
        entry = {
            "tasks": len(common), "task_success_change": d["mean"], "ci95": d["ci95"], "separated": d["separated"],
            "regression": bool(d["mean"] <= -max_drop and d["separated"]), "drop_without_proof": bool(d["mean"] <= -max_drop and not d["separated"]),
            "worse_tasks": [t for t in common if b["by_task"][t] < a["by_task"][t]],
            "metric_changes": {
                "tool_success_rate": _delta(am["tools"]["success_rate"], bm["tools"]["success_rate"]),
                "recovery_rate": _delta(am["recovery"]["rate"], bm["recovery"]["rate"]),
                "e2e_p50_s": _delta(am["latency"]["e2e_p50_s"], bm["latency"]["e2e_p50_s"]),
                "tokens_per_run": _delta(am["cost"]["tokens_per_run"], bm["cost"]["tokens_per_run"]),
            },
        }
        out["arms"][arm] = entry
        if entry["regression"]:
            out["regressions"].append(arm)
    out["model_before"], out["model_after"] = before.get("model"), after.get("model")
    out["versions_changed"] = {k: [before.get("versions", {}).get(k), after.get("versions", {}).get(k)]
                               for k in sorted(set(before.get("versions", {})) | set(after.get("versions", {})))
                               if before.get("versions", {}).get(k) != after.get("versions", {}).get(k)}
    return out


def render_compare(c: dict[str, Any]) -> str:
    lines = [f"before: {c['model_before']}   after: {c['model_after']}   (a drop of {c['max_drop'] * 100:.0f} points or more, with an "
             "interval that excludes zero, is a regression)"]
    if c["versions_changed"]:
        lines.append("changed versions: " + ", ".join(f"{k} {v[0]} -> {v[1]}" for k, v in c["versions_changed"].items()))
    for arm, e in c["arms"].items():
        flag = "REGRESSION" if e["regression"] else ("drop not proven" if e["drop_without_proof"] else "ok")
        lines.append(f"[{arm}] task success {e['task_success_change'] * 100:+.1f} points (95% interval {e['ci95'][0] * 100:+.1f} to "
                     f"{e['ci95'][1] * 100:+.1f}) over {e['tasks']} tasks: {flag}")
        mc = {k: v for k, v in e["metric_changes"].items() if v is not None}
        lines.append("  changes: " + ", ".join(f"{k} {v:+.3g}" for k, v in mc.items()))
        if e["worse_tasks"]:
            lines.append("  worse on: " + ", ".join(e["worse_tasks"]))
    return "\n".join(lines)
