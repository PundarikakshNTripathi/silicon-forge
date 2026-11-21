# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exceptions for the EDA execution layer."""
from __future__ import annotations


class EDAError(RuntimeError):
    """Base class for EDA execution failures."""


class ToolMissingError(EDAError):
    """A required EDA tool is not available in the selected backend (never silently mocked)."""

    def __init__(self, backend: str, tools: list[str], hint: str = "") -> None:
        self.backend = backend
        self.tools = tools
        msg = f"EDA backend '{backend}' is missing required tools: {', '.join(tools)}."
        if hint:
            msg += f" {hint}"
        super().__init__(msg)


class ToolRunError(EDAError):
    """A tool crashed or timed out: distinct from a verification FAIL verdict."""
