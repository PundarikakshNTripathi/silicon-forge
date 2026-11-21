# SPDX-License-Identifier: AGPL-3.0-or-later
"""Attempt memory for repair loops.

Repair was a series of independent single shots: each attempt saw the current RTL and the latest diagnostic, and nothing
about what had already been tried. That invites the same fix twice. This keeps, per loop, what each attempt changed
(a unified diff, trimmed) and what the verifier said afterwards, and renders it for the next prompt.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field


@dataclass
class Attempt:
    label: str
    diagnostic: str  # what the verifier reported for the RTL *before* this attempt
    diff: str  # what the attempt changed (empty for the first observation)


def _norm(diag: str) -> str:
    """Diagnostics compared modulo numbers (line numbers, cycle counts, times shift between runs)."""
    return re.sub(r"\d+", "#", diag).strip()


@dataclass
class AttemptMemory:
    attempts: list[Attempt] = field(default_factory=list)
    _last_rtl: str | None = None

    def record(self, label: str, diagnostic: str, rtl_before: str, rtl_after: str | None = None) -> None:
        """Remember one failed round. `rtl_after` is the attempt's result, when there is one."""
        diff = ""
        if rtl_after is not None:
            diff = "\n".join(list(difflib.unified_diff(
                rtl_before.splitlines(), rtl_after.splitlines(), "before", "after", n=1, lineterm=""))[:40])
        self.attempts.append(Attempt(label, diagnostic.strip(), diff))
        self._last_rtl = rtl_after if rtl_after is not None else rtl_before

    def repeats_last(self, diagnostic: str) -> bool:
        """True when the verifier reports (modulo numbers) the same failure as after the previous attempt."""
        return bool(self.attempts) and _norm(self.attempts[-1].diagnostic) == _norm(diagnostic)

    def stuck(self) -> bool:
        """The last two rounds ended with the same failure: more of the same is unlikely to help."""
        a = self.attempts
        return len(a) >= 2 and _norm(a[-1].diagnostic) == _norm(a[-2].diagnostic)

    def render(self, max_chars: int = 2500) -> str:
        if not self.attempts:
            return ""
        blocks = []
        for k, a in enumerate(self.attempts, 1):
            lines = [f"Attempt {k} ({a.label}) was followed by this failure:", _clip(a.diagnostic, 400)]
            if a.diff:
                lines += ["The change that attempt made:", _clip(a.diff, 700)]
            blocks.append("\n".join(lines))
        text = "\n\n".join(blocks)
        if len(text) > max_chars:  # keep the most recent attempts, they are the most informative
            text = "(older attempts omitted)\n" + text[-max_chars:]
        return text


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 12] + " ...[clipped]"
