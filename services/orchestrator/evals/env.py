# SPDX-License-Identifier: AGPL-3.0-or-later
"""A reinforcement-learning environment over VerilogEval, with the interface of Gymnasium (`reset`, `step`) and no dependency
on it, so any trainer can drive it.

One episode is one problem. The observation is the problem statement, the turn number and, after a failed compile, the
compiler's message. The action is a reply containing a Verilog module. Rewards come from verifiers, cheapest first:

* the candidate does not compile: -0.1 and another turn (until `max_turns`);
* it compiles but fails the benchmark's testbench: 0.0 and the episode ends;
* it passes the testbench: 1.0 and the episode ends.

The testbench output is never part of the observation: it is the scoring test, and an agent that could read it would learn to
fit the grader instead of the problem (two-level protocol, arXiv:2606.28279). Use `split="train"` for training and evaluate on
`split="test"` with `siliconforge evals run --split test`; the two partitions are disjoint by construction.
"""
from __future__ import annotations

import random
import tempfile
from pathlib import Path
from typing import Any

from services.orchestrator.evals import verilogeval as ve

COMPILE_PENALTY, WRONG, PASSED = -0.1, 0.0, 1.0


class VerilogEnv:
    def __init__(self, runner, split: str = "train", max_turns: int = 3, seed: int = 0, root: Path | None = None) -> None:
        self.runner, self.max_turns = runner, max_turns
        self.problems = ve.split_problems(ve.load_problems(root), split)
        if not self.problems:
            raise RuntimeError(f"no problems in the {split} split; run `siliconforge evals fetch verilogeval` first")
        self._rng = random.Random(seed)
        self._p: ve.Problem | None = None
        self._turn = 0
        self.system_prompt = ve.SYSTEM_PROMPT

    def reset(self, seed: int | None = None, options: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if seed is not None:
            self._rng = random.Random(seed)
        problem_id = (options or {}).get("problem")
        self._p = next((p for p in self.problems if p.id == problem_id), None) or self._rng.choice(self.problems)
        self._turn = 0
        return self._obs(""), {"problem": self._p.id}

    def _obs(self, diagnostic: str) -> dict[str, Any]:
        assert self._p is not None
        return {"prompt": ve.user_prompt(self._p), "turn": self._turn, "max_turns": self.max_turns, "diagnostic": diagnostic}

    def step(self, action: str) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        if self._p is None:
            raise RuntimeError("call reset() first")
        self._turn += 1
        rtl = ve.extract_module(action)
        with tempfile.TemporaryDirectory(prefix="sf_env_") as tmp:
            work = Path(tmp)
            diag = ve.compile_diagnostic(self.runner, work, rtl)
            if diag:
                over = self._turn >= self.max_turns
                return self._obs(diag), COMPILE_PENALTY, over, False, {"problem": self._p.id, "compiled": False}
            g = ve.grade(self.runner, work, self._p, rtl)
        # `mismatches` is returned for logging by the trainer's owner; it is not part of the observation
        info = {"problem": self._p.id, "compiled": True, "passed": g.passed, "mismatches": g.mismatches}
        return self._obs(""), (PASSED if g.passed else WRONG), True, False, info
