# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signoff gates read from a LibreLane `metrics.json`.

Each gate is one of three things: **pass**, **fail**, or **not reported** (the metric is absent, because the step that
produces it did not run or this version does not emit it). Absence is never counted as a pass. A gate marked `required`
that is not reported makes the overall verdict `incomplete`, not `clean`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

PASS, FAIL, MISSING = "pass", "fail", "not reported"


@dataclass
class Check:
    id: str
    name: str
    status: str
    required: bool
    value: Any = None
    limit: str = ""
    detail: str = ""


@dataclass
class Verdict:
    status: str  # clean | failed | incomplete
    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def missing_required(self) -> list[Check]:
        return [c for c in self.checks if c.status == MISSING and c.required]


def _num(m: dict[str, Any], key: str) -> float | None:
    v = m.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _first(m: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, float] | None:
    for k in keys:
        v = _num(m, k)
        if v is not None:
            return k, v
    return None


def _count_zero(m, cid, name, keys, required, detail="") -> Check:
    """A violation counter that must be zero. With several candidate keys (different tools), all that are present count."""
    present = [(k, v) for k in keys if (v := _num(m, k)) is not None]
    if not present:
        return Check(cid, name, MISSING, required, None, "0", detail or "no metric emitted by this run")
    total = sum(v for _, v in present)
    return Check(cid, name, PASS if total == 0 else FAIL, required, int(total), "0", ", ".join(f"{k}={int(v)}" for k, v in present))


def evaluate(metrics: dict[str, Any], waive: set[str] | None = None) -> Verdict:
    """Gates for one run. `waive` lists check ids a project has explicitly accepted; a waived failure is shown as such
    in the detail and does not fail the verdict, so a waiver is visible rather than hidden."""
    waive = waive or set()
    checks: list[Check] = []

    # --- timing, every corner (worst slack across corners is what the flow reports first)
    setup = _first(metrics, ("timing__setup__ws",))
    if setup is None:
        checks.append(Check("setup_ws", "Setup slack, worst corner", MISSING, True, None, ">= 0 ns"))
    else:
        ok = setup[1] >= 0 and not math.isnan(setup[1])
        checks.append(Check("setup_ws", "Setup slack, worst corner", PASS if ok else FAIL, True, round(setup[1], 4), ">= 0 ns"))
    checks.append(_count_zero(metrics, "setup_vio", "Setup violations", ("timing__setup_vio__count",), True))
    hold = _num(metrics, "timing__hold__ws")
    if hold is None:
        checks.append(Check("hold_ws", "Hold slack, worst corner", MISSING, True, None, ">= 0 ns"))
    else:
        checks.append(Check("hold_ws", "Hold slack, worst corner", PASS if hold >= 0 else FAIL, True, round(hold, 4), ">= 0 ns"))
    checks.append(_count_zero(metrics, "hold_vio", "Hold violations", ("timing__hold_vio__count",), True))

    # --- physical verification
    checks.append(_count_zero(metrics, "drc", "Design rule check (Magic, KLayout, router)",
                              ("magic__drc_error__count", "klayout__drc_error__count", "route__drc_errors"), True))
    checks.append(_count_zero(metrics, "lvs", "Layout versus schematic", ("design__lvs_error__count",), True))
    checks.append(_count_zero(metrics, "antenna", "Antenna violations",
                              ("route__antenna_violation__count", "antenna__violating__nets"), False))
    checks.append(_count_zero(metrics, "xor", "Layout versus stream-out (XOR)", ("design__xor_difference__count",), False))
    checks.append(_count_zero(metrics, "lint", "Linter errors", ("design__lint_error__count",), False))

    # --- electrical limits
    checks.append(_count_zero(metrics, "slew", "Max slew violations", ("design__max_slew_violation__count",), False))
    checks.append(_count_zero(metrics, "cap", "Max capacitance violations", ("design__max_cap_violation__count",), False))
    checks.append(_count_zero(metrics, "fanout", "Max fanout violations", ("design__max_fanout_violation__count",), False))
    irdrop = _first(metrics, ("ir__voltage__worst",))
    if irdrop is not None:
        checks.append(Check("ir", "IR drop, worst voltage", PASS, False, round(irdrop[1], 4), "informational",
                            "reported, no limit applied"))

    for c in checks:
        if c.id in waive and c.status == FAIL:
            c.status, c.detail = PASS, f"WAIVED by the project ({c.detail or c.value})"
    if any(c.status == FAIL for c in checks):
        status = "failed"
    elif any(c.status == MISSING and c.required for c in checks):
        status = "incomplete"
    else:
        status = "clean"
    return Verdict(status, checks)


def corner_table(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-corner setup and hold slack, sorted worst first."""
    sp, hp = "timing__setup__ws__corner:", "timing__hold__ws__corner:"
    corners = sorted({k[len(sp):] for k in metrics if k.startswith(sp)} | {k[len(hp):] for k in metrics if k.startswith(hp)})
    rows = [{"corner": c, "setup_ns": _num(metrics, sp + c), "hold_ns": _num(metrics, hp + c)} for c in corners]
    return sorted(rows, key=lambda r: (r["setup_ns"] if r["setup_ns"] is not None else 1e9))
