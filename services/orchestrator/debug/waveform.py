# SPDX-License-Identifier: AGPL-3.0-or-later
"""Waveform windows and trace comparison, working on `VcdTrace` (see eda/vcd.py) or its `to_wavejson` form.

A full VCD is far too large for a prompt and mostly irrelevant. MAGE (arXiv:2412.07822) found that what helps a debugger
is the earliest time the observed output departs from the expected one, plus a short window of signal values around it.
`first_divergence` finds that time; `waveform_window` renders the window as a compact table where an unchanged value is
shown as `.` so that the edges stand out.
"""
from __future__ import annotations

from typing import Any

from services.orchestrator.eda.vcd import VcdTrace, to_int


def from_wavejson(data: dict[str, Any]) -> VcdTrace:
    """Rebuild a `VcdTrace` from `VcdTrace.to_wavejson()` (the form stored on a `FormalWitness`)."""
    tr = VcdTrace(timescale=str(data.get("timescale", "")), end_time=int(data.get("end", 0)))
    for s in data.get("signals", []):
        name = str(s["name"])
        tr.signals[name] = int(s.get("width", 1))
        tr.changes[name] = [(int(t), str(v)) for t, v in s.get("changes", [])]
    return tr


def step_time(trace: VcdTrace, step: int) -> int:
    """Time of formal step `step` (from the `smt_step` signal that SymbiYosys traces carry), else the end of the trace."""
    for t, v in trace.changes.get("smt_step", []):
        if to_int(v) == step:
            return t
    return trace.end_time


def first_divergence(a: VcdTrace, b: VcdTrace, signals: list[str] | None = None) -> tuple[int, list[str]] | None:
    """Earliest time at which any common signal differs between two traces: `(time, [differing signals])`, or None.
    Values containing x/z in either trace are compared as text, so an unknown that the other side resolves counts."""
    names = [n for n in (signals or list(a.signals)) if n in a.signals and n in b.signals]
    times = sorted({t for n in names for t, _ in a.changes.get(n, [])} | {t for n in names for t, _ in b.changes.get(n, [])})
    for t in times:
        bad = [n for n in names if _norm(a.value_at(n, t)) != _norm(b.value_at(n, t))]
        if bad:
            return t, bad
    return None


def _norm(v: str | None) -> str | None:
    if v is None:
        return None
    return v.lstrip("0") or "0" if all(c in "01" for c in v) else v.lower()


def _fmt(value: str | None, width: int) -> str:
    if value is None:
        return "?"
    if width == 1:
        return value
    n = to_int(value)
    return "x" if n is None else (f"{n}" if width <= 4 else f"0x{n:x}")


def waveform_window(trace: VcdTrace, fail_time: int | None = None, before: int = 6, after: int = 1,
                    signals: list[str] | None = None, mark: str | None = None, max_signals: int = 12) -> str:
    """Render a window of `before` change-times up to `fail_time` and `after` change-times beyond it.

    `signals` selects and orders columns (default: the first `max_signals` of the trace, `mark` moved to the end and
    flagged with `>`). Rows are the distinct times at which a shown signal changes, so a quiet stretch costs nothing."""
    names = [n for n in (signals or [s for s in trace.signals if s != "smt_step"]) if n in trace.signals]
    if mark and mark in trace.signals and mark not in names:
        names.append(mark)
    names = names[:max_signals]
    if mark in names:
        names = [n for n in names if n != mark] + [mark]
    if not names:
        return "(no signals in trace)"
    times = sorted({t for n in names for t, _ in trace.changes.get(n, [])} | {0})
    ft = trace.end_time if fail_time is None else fail_time
    upto = [t for t in times if t <= ft]
    past = [t for t in times if t > ft][:after]
    rows = upto[-(before + 1):] + past
    head = ["time"] + [(">" + n if n == mark else n) for n in names]
    table: list[list[str]] = [head]
    prev: dict[str, str] = {}
    for t in rows:
        row = [("@" if t == ft else "") + str(t)]
        for n in names:
            cur = _fmt(trace.value_at(n, t), trace.signals[n])
            row.append("." if prev.get(n) == cur and t != rows[0] else cur)
            prev[n] = cur
        table.append(row)
    widths = [max(len(r[c]) for r in table) for c in range(len(head))]
    unit = f" ({trace.timescale})" if trace.timescale else ""
    lines = [f"Waveform window{unit}; '@' marks the failing time, '.' means unchanged"]
    lines += ["  ".join(cell.rjust(widths[c]) for c, cell in enumerate(r)) for r in table]
    return "\n".join(lines)
