# SPDX-License-Identifier: AGPL-3.0-or-later
"""A scripted arm: replays a fixed list of tool calls and a final answer through the shared tool seam.

It exists to test the harness without a model. Two arms given the same script must produce the same tool metrics and the same
verdict, which is how we know a difference between real arms comes from the arms and not from the harness.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from services.orchestrator.agentbench.arms.base import ArmContext, ArmResult, LlmRecord


@dataclass
class Step:
    tool: str | None = None  # None means "answer with `text`" and ends the run
    args: dict[str, Any] = field(default_factory=dict)
    text: str = ""


class FakeLlmLog:
    """Stands in for the measuring proxy: the arm appends a record per pretend model call."""

    def __init__(self) -> None:
        self.records: list[LlmRecord] = []
        self._lock = threading.Lock()

    def add(self, rec: LlmRecord) -> None:
        with self._lock:
            self.records.append(rec)

    def records_between(self, t0: float, t1: float) -> list[LlmRecord]:
        with self._lock:
            return [r for r in self.records if t0 <= r.start <= t1]


class ScriptedArm:
    def __init__(self, name: str, steps: list[Step], log: FakeLlmLog, prompt_tokens: int = 200, completion_tokens: int = 20,
                 llm_s: float = 0.002, guarded: bool = False, invalid_at: tuple[int, ...] = ()) -> None:
        self.name, self.steps, self.log = name, steps, log
        self.prompt_tokens, self.completion_tokens, self.llm_s = prompt_tokens, completion_tokens, llm_s
        self.guarded = guarded
        self.invalid_at = invalid_at

    def run(self, ctx: ArmContext) -> ArmResult:
        invalid: list[int] = []
        calls = 0
        for step in self.steps:
            if calls >= ctx.max_steps:
                return ArmResult("", "step_limit", calls, invalid_calls=invalid)
            self.log.add(LlmRecord(start=time.time(), duration_s=self.llm_s, prompt_tokens=self.prompt_tokens,
                                   completion_tokens=self.completion_tokens, tokens_per_s=50.0,
                                   tool_call_names=[step.tool] if step.tool else []))
            time.sleep(self.llm_s)
            if calls in self.invalid_at:
                invalid.append(calls)
            calls += 1
            if step.tool is None:
                return ArmResult(step.text, "done", calls, invalid_calls=invalid)
            ctx.tools.call_text(step.tool, step.args)
        return ArmResult("", "step_limit", calls, invalid_calls=invalid)
