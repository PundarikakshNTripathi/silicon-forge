# SPDX-License-Identifier: AGPL-3.0-or-later
"""Assemble the evidence section of a repair prompt."""
from __future__ import annotations

import re
from typing import Any

from services.orchestrator.debug.memory import AttemptMemory
from services.orchestrator.debug.waveform import from_wavejson, step_time, waveform_window
from services.orchestrator.guardrails.rtl_slicer import RtlDependencySlicer

_LOC = re.compile(r"[\w./\\:-]*?\.(?:sv|svh|v|vh):(\d+)(?::\d+)?")


def error_lines(diagnostic: str, rtl: str, context: int = 1, limit: int = 8) -> str:
    """The RTL lines that a lint or compile diagnostic points at (`file.sv:12:5: ...`), numbered and flagged with `>>`.
    Empty when the diagnostic carries no source locations."""
    hits: list[int] = []
    for m in _LOC.finditer(diagnostic):
        n = int(m.group(1))
        if n not in hits:
            hits.append(n)
    src = rtl.splitlines()
    hits = [n for n in hits if 1 <= n <= len(src)][:limit]
    if not hits:
        return ""
    want = sorted({k for n in hits for k in range(max(1, n - context), min(len(src), n + context) + 1)})
    out, prev = [], 0
    for k in want:
        if prev and k != prev + 1:
            out.append("     | ...")
        out.append(f"{'>>' if k in hits else '  '}{k:4d} | {src[k - 1]}")
        prev = k
    return "\n".join(out)


def build_repair_context(rtl: str, diagnostic: str, failing_signal: str | None = None, witness: Any | None = None,
                         memory: AttemptMemory | None = None, slicer: RtlDependencySlicer | None = None) -> str:
    """Evidence for a repair, most specific first. Sections that have nothing to say are left out.

    `witness` is a `state.FormalWitness`-like object (`trace`, `cycle`, `property_id`, `stimulus`)."""
    parts: list[str] = []
    lines = error_lines(diagnostic, rtl)
    if lines:
        parts.append("Lines the tool complained about (>> marks them):\n" + lines)
    trace = getattr(witness, "trace", None)
    if trace:
        tr = from_wavejson(trace)
        cycle = int(getattr(witness, "cycle", 0))
        parts.append(f"Counterexample for property {getattr(witness, 'property_id', '?')}, fails at step {cycle}.\n"
                     + waveform_window(tr, step_time(tr, max(cycle - 1, 0)), mark=failing_signal))
    if failing_signal:
        cone = (slicer or RtlDependencySlicer()).cone(rtl, failing_signal)
        if cone.found:
            parts.append(f"Signals that can influence '{cone.target}': {', '.join(sorted(cone.signals))}")
    if memory is not None and memory.attempts:
        parts.append("Do not repeat these.\n" + memory.render())
        if memory.stuck():
            parts.append("The last two attempts ended with the same failure. Change approach instead of adjusting the same lines.")
    return "\n\n".join(parts)
