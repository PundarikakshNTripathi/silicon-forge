# SPDX-License-Identifier: AGPL-3.0-or-later
"""Evaluation arms: ways of turning a problem statement into RTL, from one shot to a verifier-driven loop.

Every arm sees the same thing the others see at the start (the problem statement) and may use only the *compile
diagnostic* of its own candidate afterwards. The scoring testbench is never available to an arm (two-level protocol,
arXiv:2606.28279): an agent that could read it would be graded on how well it can overfit to the grader.
"""
from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from services.orchestrator.debug import AttemptMemory, error_lines
from services.orchestrator.evals import verilogeval as ve


@dataclass
class LLMReply:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0


LLM = Callable[[str, str, float], LLMReply]  # (system prompt, user prompt, temperature) -> reply


@dataclass
class Attempt:
    rtl: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    rounds: int = 1  # model calls that produced a candidate (1 = no repair)
    compiled: bool | None = None  # known only to arms that compile their own candidates
    log: list[str] = field(default_factory=list)

    def add(self, r: LLMReply) -> None:
        self.calls += 1
        self.prompt_tokens += r.prompt_tokens
        self.completion_tokens += r.completion_tokens
        self.latency_s += r.latency_s


class Arm:
    name = "arm"

    def describe(self) -> dict:
        return {"arm": self.name}

    def solve(self, problem: ve.Problem, llm: LLM, runner, temperature: float = 0.0, workdir: Path | None = None) -> Attempt:
        raise NotImplementedError


class SingleShot(Arm):
    """One model call, the reply is the answer. The baseline every other arm is compared against."""

    name = "single"

    def solve(self, problem, llm, runner, temperature=0.0, workdir=None) -> Attempt:
        a = Attempt(rtl="")
        r = llm(ve.SYSTEM_PROMPT, ve.user_prompt(problem), temperature)
        a.add(r)
        a.rtl = ve.extract_module(r.text)
        return a


class CompileFeedback(Arm):
    """Generate, compile the candidate on its own, and on an error ask again with the compiler's message, the lines it
    points at and what earlier rounds tried. At most `rounds` model calls. Same debug-context pieces as the pipeline."""

    name = "feedback"

    def __init__(self, rounds: int = 3, context: bool = True, guidance: bool = False) -> None:
        self.rounds = rounds
        self.context = context
        self.guidance = guidance
        self._skills = None
        if not context:
            self.name = "feedback-plain"
        elif guidance:
            self.name = "feedback-guided"

    def describe(self) -> dict:
        return {"arm": self.name, "rounds": self.rounds, "debug_context": self.context, "knowledge_packs": self.guidance}

    def guide(self, diag: str) -> str:
        """Knowledge-pack sections for the compiler's error lines (warnings are left out so that they do not steer retrieval)."""
        if not self.guidance:
            return ""
        if self._skills is None:
            from services.orchestrator.skills.registry import SkillRegistry

            self._skills = SkillRegistry()
        errors = " ".join(ln for ln in diag.splitlines() if "error" in ln.lower() or "syntax" in ln.lower())
        return self._skills.format_context(errors or diag, top_k=2)

    def solve(self, problem, llm, runner, temperature=0.0, workdir=None) -> Attempt:
        a = Attempt(rtl="")
        base = ve.user_prompt(problem)
        memory = AttemptMemory()
        r = llm(ve.SYSTEM_PROMPT, base, temperature)
        a.add(r)
        rtl = ve.extract_module(r.text)
        with tempfile.TemporaryDirectory(prefix="sf_ev_") as tmp:
            work = workdir or Path(tmp)
            for rnd in range(1, self.rounds + 1):
                diag = ve.compile_diagnostic(runner, work, rtl)
                a.rtl, a.rounds, a.compiled = rtl, rnd, not diag
                if not diag or rnd == self.rounds:
                    break
                a.log.append(f"round {rnd}: {diag.splitlines()[0][:160]}")
                evidence = error_lines(diag, rtl) if self.context else ""
                history = memory.render() if self.context else ""
                notes = self.guide(diag)
                prompt = (f"{base}\n\nYour previous attempt does not compile.\nCompiler output:\n{diag}\n"
                          + (f"\nLines it points at (>> marks them):\n{evidence}\n" if evidence else "")
                          + (f"\nRelevant guidance:\n{notes}\n" if notes else "")
                          + (f"\nEarlier attempts, do not repeat them:\n{history}\n" if history else "")
                          + "\nReply with the corrected module only, in one ```verilog block.")
                r = llm(ve.SYSTEM_PROMPT, prompt, temperature)
                a.add(r)
                new = ve.extract_module(r.text)
                memory.record(f"round {rnd}", diag, rtl, new)
                rtl = new
        return a


class Sampled(Arm):
    """`n` independent runs of a base arm (the first at the requested temperature, the rest at 0.7), then the first
    candidate that compiles. Ranking is by compilation only, so the scoring testbench stays out of the loop."""

    def __init__(self, base: Arm, n: int = 3) -> None:
        self.base, self.n = base, n
        self.name = f"sampled{n}-{base.name}"

    def describe(self) -> dict:
        return {"arm": self.name, "candidates": self.n, "base": self.base.describe()}

    def solve(self, problem, llm, runner, temperature=0.0, workdir=None) -> Attempt:
        total = Attempt(rtl="")
        best: Attempt | None = None
        for k in range(self.n):
            cand = self.base.solve(problem, llm, runner, temperature if k == 0 else 0.7, workdir)
            total.calls += cand.calls
            total.prompt_tokens += cand.prompt_tokens
            total.completion_tokens += cand.completion_tokens
            total.latency_s += cand.latency_s
            if best is None or (cand.compiled and not best.compiled):
                best = cand
            if best.compiled:
                break  # nothing cheaper to rank on; stop spending tokens once a candidate compiles
        assert best is not None
        total.rtl, total.rounds, total.compiled, total.log = best.rtl, best.rounds, best.compiled, best.log
        return total


def build_arm(name: str, rounds: int = 3, candidates: int = 3) -> Arm:
    if name == "single":
        return SingleShot()
    if name == "feedback":
        return CompileFeedback(rounds)
    if name == "feedback-plain":
        return CompileFeedback(rounds, context=False)
    if name == "feedback-guided":
        return CompileFeedback(rounds, guidance=True)
    if name == "sampled":
        return Sampled(CompileFeedback(rounds), candidates)
    raise ValueError(f"unknown arm '{name}' (single, feedback, feedback-plain, feedback-guided, sampled)")


def timed(fn: Callable[[], LLMReply]) -> LLMReply:
    t0 = time.perf_counter()
    r = fn()
    r.latency_s = r.latency_s or round(time.perf_counter() - t0, 3)
    return r
