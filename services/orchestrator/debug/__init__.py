# SPDX-License-Identifier: AGPL-3.0-or-later
"""Debug context: the evidence a repair agent actually needs, in a few hundred tokens.

Three pieces, each usable on its own:
  * `waveform`  - a text window of a trace around a failure, and the earliest cycle where two traces differ;
  * `memory`    - what was already tried and what the verifier said, so a repair does not repeat itself;
  * `context`   - builds the repair prompt section from a diagnostic, the RTL, a witness and the attempt memory.
"""
from services.orchestrator.debug.context import build_repair_context, error_lines
from services.orchestrator.debug.memory import Attempt, AttemptMemory
from services.orchestrator.debug.waveform import (
    first_divergence,
    from_wavejson,
    step_time,
    waveform_window,
)

__all__ = ["Attempt", "AttemptMemory", "build_repair_context", "error_lines", "first_divergence", "from_wavejson",
           "step_time", "waveform_window"]
