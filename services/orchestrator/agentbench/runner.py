# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run tasks against arms, one fresh sandbox per run, and record the same measurements for every arm.

Per run: build the task's fixture in a temp workspace; give the arm the shared instrumented toolbox; time the arm inside a `run`
span; turn the proxy's per-call records into `llm` spans (the model seam) and read the toolbox's spans (the tool seam); only then
run the verifier, so verification time never counts as agent time. Runs are sequential, which is what lets the proxy attribute a
call to a run by time window, and arms are rotated per task so no arm always goes first.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from services.orchestrator.agentbench.arms.base import Arm, ArmContext, ArmResult, LlmLog, LlmRecord
from services.orchestrator.agentbench.faults import FaultInjector
from services.orchestrator.agentbench.sandbox import sandbox
from services.orchestrator.agentbench.tasks import EdaUnavailable, Outcome, Task, Verdict
from services.orchestrator.agentbench.toolkit import InstrumentedToolBox
from services.orchestrator.telemetry import Span, Tracer, run_metrics
from services.orchestrator.telemetry.metrics import INVALID_ACTION


@dataclass
class RunRecord:
    task: str
    category: str
    arm: str
    trial: int
    solved: bool = False
    skipped: bool = False
    reasons: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    stopped: str = ""
    steps: int = 0
    error: str = ""
    wall_s: float = 0.0
    first_prompt_tokens: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


def _llm_spans(run: Span, records: list[LlmRecord], res: ArmResult, model: str) -> list[Span]:
    spans: list[Span] = []
    for i, rec in enumerate(records):
        sp = Span(trace_id=run.trace_id, span_id=f"llm{i}", parent_id=run.span_id, name="chat", kind="llm", start=rec.start,
                  duration_s=rec.duration_s,
                  attrs={"provider": "llamacpp", "model": model, "tokens_in": rec.prompt_tokens, "tokens_out": rec.completion_tokens,
                         "cached_tokens": rec.cached_tokens, "cost_usd": 0.0})
        if rec.tokens_per_s:
            sp.attrs["tokens_per_s"] = rec.tokens_per_s
        if rec.failed:
            sp.fail("provider_error")
        elif i in res.invalid_calls or rec.bad_tool_arguments or rec.finish_reason == "length":
            sp.fail(INVALID_ACTION)
        spans.append(sp)
    return spans


def _unknown_tool_spans(run: Span, records: list[LlmRecord], res: ArmResult, allowed: tuple[str, ...]) -> list[Span]:
    """Tool names the model asked for that do not exist. A framework refuses these itself, so they never reach the tool seam."""
    out: list[Span] = []
    names = [n for rec in records for n in rec.tool_call_names if allowed and n not in allowed]
    for i in range(len(names) + res.unknown_tools):
        sp = Span(trace_id=run.trace_id, span_id=f"unk{i}", parent_id=run.span_id, name="unknown", kind="tool",
                  start=run.start + run.duration_s, duration_s=0.0)
        sp.fail("unknown_tool")
        out.append(sp)
    return out


def run_one(task: Task, arm: Arm, trial: int, llm_log: LlmLog, *, base_url: str, model: str = "local", runner: Any = None,
            deadline_s: float = 300.0, max_steps: int | None = None, temperature: float = 0.2,
            max_tokens: int = 2048) -> RunRecord:
    rec = RunRecord(task=task.id, category=task.category, arm=arm.name, trial=trial)
    if task.needs_eda and runner is None:
        rec.skipped, rec.reasons = True, ["needs a real EDA backend (wsl, native or docker)"]
        return rec
    allowed = task.tools if runner is not None else tuple(t for t in task.tools if t != "eda")
    tracer = Tracer()
    with sandbox(task.files, backend="wsl" if runner is not None else "simulated") as box:
        tools = InstrumentedToolBox(box.workspace, runner_factory=(lambda: runner) if runner is not None else None, tracer=tracer,
                                    allowed=allowed, injector=FaultInjector(task.faults),
                                    deadline=time.monotonic() + deadline_s, guarded=bool(getattr(arm, "guarded", False)))
        ctx = ArmContext(task=task, tools=tools, base_url=base_url, model=model, max_steps=max_steps or task.max_steps,
                         temperature=temperature, max_tokens=max_tokens)
        t0 = time.time()
        with tracer.span("invoke_agent", "run", arm=arm.name, task=task.id, trial=trial) as run:
            tools.run_span = run
            try:
                res = arm.run(ctx)
            except Exception as exc:  # noqa: BLE001 - an arm that crashes is a failed run to record, not a reason to stop the matrix
                res = ArmResult("", "error", 0, f"{type(exc).__name__}: {str(exc)[:160]}")
                run.fail("arm_error")
            run.set(stopped=res.stopped, steps=res.steps)
        t1 = time.time()
        records = llm_log.records_between(t0 - 1e-3, t1 + 1e-3)
        tracer.spans.extend(_llm_spans(run, records, res, model))
        tracer.spans.extend(_unknown_tool_spans(run, records, res, allowed))
        rec.wall_s, rec.stopped, rec.steps, rec.error = round(t1 - t0, 3), res.stopped, res.steps or len(records), res.error
        rec.first_prompt_tokens = records[0].prompt_tokens if records else None
        try:
            verdict = task.verify(Outcome(box=box, final_text=res.final_text, tools=tools, stopped=res.stopped, runner=runner))
        except EdaUnavailable as exc:
            rec.skipped, rec.reasons = True, [str(exc)]
            return rec
        except Exception as exc:  # noqa: BLE001 - a verifier crash is recorded as unsolved with the reason, not hidden
            verdict = Verdict(False, [f"verifier error: {type(exc).__name__}: {str(exc)[:120]}"])
        rec.solved, rec.reasons, rec.violations = verdict.solved, verdict.reasons, verdict.violations
        run.set(solved=verdict.solved)
        rec.metrics = run_metrics(tracer.spans)
    return rec


def run_matrix(tasks: list[Task], arms: list[Arm], trials: int, llm_log: LlmLog, *, base_url: str, model: str = "local",
               runner: Any = None, progress: Callable[[RunRecord], None] | None = None, **kw: Any) -> list[RunRecord]:
    """Every task x arm x trial, one at a time. Arms are rotated per task and trial so none always runs first."""
    out: list[RunRecord] = []
    for trial in range(trials):
        for ti, task in enumerate(tasks):
            shift = (trial + ti) % len(arms)
            for arm in arms[shift:] + arms[:shift]:
                rec = run_one(task, arm, trial, llm_log, base_url=base_url, model=model, runner=runner, **kw)
                out.append(rec)
                if progress:
                    progress(rec)
    return out
