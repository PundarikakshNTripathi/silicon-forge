# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ask instead of guessing: spot a first request that is too vague to act on.

A small model that is told "make the design faster" edits something, because editing is what its tools are for. Every loop in the
AgentBench comparison failed that task, ours and both frameworks', although all of them were told to ask when a request is
ambiguous: an instruction in a prompt is weak next to a model's wish to use its tools. So the session checks mechanically, the same
way it checks a requested answer format, and when a request looks vague it reminds the model, once, to ask one specific question
and to touch nothing.

The test is deliberately narrow, so that it rarely fires on a request that is merely short:

* the request is brief (12 words or fewer);
* it asks for an improvement with a vague verb or comparative ("faster", "better", "clean up", "optimise", "improve");
* it names nothing concrete: no file, no backticked or quoted name, no identifier, no number, no signal-like word.

Only the first message of a session is judged, and a message that says to go ahead is never held. A false positive costs one
question; the held-out suite in `agentbench/tasks_heldout.py` includes short, concrete requests to measure that cost.
"""
from __future__ import annotations

import re

VAGUE = re.compile(
    r"\b(faster|quicker|better|nicer|cleaner|simpler|improve[ds]?|improvement|optimi[sz]e[ds]?|optimi[sz]ation|clean(?:ed)?(?:\s*up)?|"
    r"tidy|polish|enhance|speed\s*up|refactor|upgrade|fix\s+(?:it|this|that|things))\b", re.I)
CONCRETE = re.compile(
    r"([\w./\\-]+\.(?i:sv|svh|v|vh|py|md|yaml|yml|json|tcl|sdc|toml|txt)\b)"  # a file
    r"|`[^`]+`|\"[^\"]+\"|'[^']+'"  # a quoted or backticked name
    r"|\b[A-Za-z]+_[A-Za-z0-9_]+\b"  # snake_case identifier
    r"|\b[a-z]+[A-Z]\w*\b"  # camelCase identifier
    r"|\d")  # a number (matched case-sensitively: the camelCase rule above would match any word under re.I)
GO_AHEAD = re.compile(r"\b(go ahead|just do it|your call|you decide|any way|whatever you think|assume)\b", re.I)
MAX_WORDS = 12

NOTE = ("This request is underspecified: it asks for an improvement but names no file, no measure and no goal, and there are several "
        "reasonable readings. Do not edit anything and do not run any tool. Reply with ONE specific clarifying question (it must contain "
        "a question mark) that offers the likely options, and wait for the answer.")


def needs_clarification(message: str) -> bool:
    text = message.strip()
    if not text or len(text.split()) > MAX_WORDS or GO_AHEAD.search(text):
        return False
    return bool(VAGUE.search(text)) and not CONCRETE.search(text)
