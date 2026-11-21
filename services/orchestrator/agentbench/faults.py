# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic fault injection: make a tool fail in a controlled way so recovery can be measured, not just hoped for.

A `Fault` says "the first N calls to this tool fail with this message". The same faults are applied at the one tool seam every
arm uses, so a framework and our own loop face identical failures at identical moments.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Fault:
    tool: str
    fail_first: int = 1
    message: str = "temporary I/O error: the file system is busy, try again"


class FaultInjector:
    def __init__(self, faults: tuple[Fault, ...] = ()) -> None:
        self._faults = {f.tool: f for f in faults}
        self._calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def should_fail(self, tool: str) -> str | None:
        """The error message to raise for this call, or None. Counts every call to a faulty tool, successful or not."""
        fault = self._faults.get(tool)
        if fault is None:
            return None
        with self._lock:
            n = self._calls.get(tool, 0)
            self._calls[tool] = n + 1
        return fault.message if n < fault.fail_first else None

    def injected(self) -> int:
        """How many calls were made to faulty tools (a lower bound on how many were failed)."""
        return sum(self._calls.values())
