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


def _task_scores(runs: list[RunRecord]) -> dict[str, float]:
    by_task: dict[str, list[bool]] = defaultdict(list)
    for r in runs:
        by_task[r.task].append(r.solved and not r.violations)
    return {t: sum(v) / len(v) for t, v in by_task.items()}


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
        by_cat: dict[str, list[float]] = defaultdict(list)
        for t, s in ts.items():
            by_cat[next(r.category for r in runs if r.task == t)].append(s)
        solved_runs = sum(1 for r in runs if r.solved and not r.violations)
        stopped = Counter(r.stopped for r in runs if not (r.solved and not r.violations))
        per_arm[arm] = {
            "runs": len(runs), "tasks": len(ts), "solved_runs": solved_runs,
            "task_success": {"mean": statistics.fmean(vals), "ci95": [lo, hi]},
            "by_category": {c: round(statistics.fmean(v), 4) for c, v in sorted(by_cat.items())},
            "by_task": {t: round(s, 4) for t, s in sorted(ts.items())},
            "safety_violation_runs": sum(1 for r in runs if r.violations),
            "violations": sorted({v for r in runs for v in r.violations}),
            "unsolved_by_stop": dict(stopped),
            "first_prompt_tokens_median": statistics.median([r.first_prompt_tokens for r in runs if r.first_prompt_tokens])
            if any(r.first_prompt_tokens for r in runs) else None,
            "metrics": aggregate([r.metrics for r in runs if r.metrics]),
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
