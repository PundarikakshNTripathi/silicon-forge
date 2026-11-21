# SPDX-License-Identifier: AGPL-3.0-or-later
"""A read-only investigator the agent can hand a question to.

The child runs in `plan` mode (it cannot edit or run commands), starts from an empty transcript, and has a small step budget; only
its short written answer comes back. The parent's context therefore pays for the answer, not for the reading it took: a way to
search a large project without filling a small model's window. The child has no `investigate` tool, so it cannot recurse.
"""
from __future__ import annotations

from typing import Any

from services.orchestrator.agent.tools import READ, ToolBox, ToolSpec

MAX_ANSWER_CHARS = 1800
CHILD_STEPS = 8
BRIEF = ("You are a read-only investigator inside the project. Find out what the question asks using the read-only tools, then answer in "
          "at most 12 lines: state what you found, cite file paths and line numbers, and say plainly if you could not find it. "
          "You cannot change anything. Question: ")


def investigate_tool(router: Any, ws: Any, *, runner_factory: Any = None, tracer: Any = None) -> ToolSpec:
    # imported here: the agent package imports session before this module is needed
    from services.orchestrator.agent.session import AgentSession

    def run(question: str) -> str:
        if not str(question).strip():
            raise ValueError("ask a specific question")
        child = AgentSession(router, ws, ToolBox(ws, runner_factory=runner_factory), mode="plan", max_steps=CHILD_STEPS, tracer=tracer)
        res = child.send(BRIEF + str(question))
        answer = (res.reply or "").strip()[:MAX_ANSWER_CHARS]
        suffix = "" if res.stopped == "done" else f"\n[the investigation stopped early: {res.stopped}]"
        return f"[investigated in {res.steps} step(s)]\n{answer}{suffix}"

    return ToolSpec("investigate", READ,
                    "Hand a question about the project to a read-only helper with its own fresh context; only its short answer comes "
                    "back. Use it to search or understand a large project without filling your own context.",
                    {"question": "string, specific and self-contained"}, run)
