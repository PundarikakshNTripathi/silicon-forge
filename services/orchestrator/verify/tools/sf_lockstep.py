"""Lockstep co-simulation and functional coverage helpers for cocotb testbenches. Standard library only.

Lockstep means the RTL and a Python golden model run the same program, and after every retired instruction their
architectural state is compared. The first difference stops the test with the instruction that caused it and the trace
leading up to it, instead of a wrong value somewhere downstream.

    from sf_lockstep import CoverGroup, lockstep

    cov = CoverGroup("pemu")
    cov.point("opcode", bins=range(10))                   # every opcode executed at least once
    cov.point("pin_mask", bins={"none": 0, "all": 255, "some": lambda v: 0 < v < 255})
    cov.cross("opcode_x_stall", "opcode", "stalled")

    async def step_rtl(result): ...                       # advance the RTL until one instruction retires
    def rtl_state(): return {"pc": int(dut.pc.value), ...}
    await lockstep(step_rtl, rtl_state, model, steps=2000, on_step=lambda r: cov.sample("opcode", r.instr_index))
    cov.save("coverage.json")                             # merges with earlier tests and seeds

`CoverGroup.save` merges into an existing file, so every test and every seed accumulates into one coverage database.
"""
from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


class LockstepMismatch(AssertionError):
    def __init__(self, step: int, diffs: dict[str, tuple[Any, Any]], history: list[str]) -> None:
        self.step, self.diffs, self.history = step, diffs, history
        lines = [f"RTL and model diverge after instruction #{step}:"]
        lines += [f"  {k}: rtl={rtl!r} model={mod!r}" for k, (rtl, mod) in diffs.items()]
        lines.append("recent instructions (oldest first):")
        lines += [f"  {h}" for h in history]
        super().__init__("\n".join(lines))


def diff_state(rtl: dict[str, int], model: dict[str, int], ignore: Iterable[str] = ()) -> dict[str, tuple[Any, Any]]:
    skip = set(ignore)
    out = {}
    for k in sorted(set(rtl) | set(model)):
        if k in skip:
            continue
        if k not in rtl or k not in model:
            out[k] = (rtl.get(k, "<missing>"), model.get(k, "<missing>"))
        elif rtl[k] != model[k]:
            out[k] = (rtl[k], model[k])
    return out


async def lockstep(step_rtl: Callable[[Any], Any], get_rtl_state: Callable[[], dict[str, int]], model: Any, steps: int,
                   ignore: Iterable[str] = (), on_step: Callable[[Any], None] | None = None, history: int = 12,
                   describe: Callable[[Any], str] | None = None,
                   before_step: Callable[[Any], None] | None = None) -> int:
    """Run `steps` instructions in lockstep (fewer if the model halts); returns how many were compared.

    `before_step(model)` runs before either side executes the instruction. Put external stimulus there (set
    `model.inputs` and drive the RTL's input pins from the same value), so the model and the RTL see identical inputs.
    The model runs first, so stimulus chosen inside `step_rtl` would reach the RTL but not the model."""
    trace: deque[str] = deque(maxlen=history)
    compared = 0
    for n in range(1, steps + 1):
        if getattr(model, "halted", False):
            break
        if before_step:
            before_step(model)
        result = model.step()
        text = describe(result) if describe else f"@{result.addr} {result.instr.name} {result.operands}"
        trace.append(f"#{n} {text}")
        await step_rtl(result)
        diffs = diff_state(get_rtl_state(), model.s.snapshot(), ignore)
        if diffs:
            raise LockstepMismatch(n, diffs, list(trace))
        if on_step:
            on_step(result)
        compared += 1
    return compared


# ----------------------------------------------------------------------------------------------- coverage

def _equals(expected: Any) -> Callable[[Any], bool]:
    return lambda v: bool(v == expected)


class CoverGroup:
    """Functional coverage: named points with bins, and crosses of two points."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.bins: dict[str, dict[str, Callable[[Any], bool]]] = {}
        self.hits: dict[str, dict[str, int]] = {}
        self.crosses: dict[str, tuple[str, str]] = {}
        self.cross_hits: dict[str, dict[str, int]] = {}
        self._last: dict[str, set[str]] = {}

    def point(self, name: str, bins: Any) -> None:
        """`bins`: an iterable of values, a {label: value | predicate} dict."""
        items = bins.items() if isinstance(bins, dict) else ((str(b), b) for b in bins)
        table: dict[str, Callable[[Any], bool]] = {}
        for label, spec in items:
            table[str(label)] = spec if callable(spec) else _equals(spec)
        self.bins[name] = table
        self.hits[name] = {label: 0 for label in table}

    def cross(self, name: str, a: str, b: str) -> None:
        self.crosses[name] = (a, b)
        self.cross_hits[name] = {f"{x}|{y}": 0 for x in self.bins[a] for y in self.bins[b]}

    def sample(self, point: str, value: Any) -> None:
        hit = {label for label, pred in self.bins[point].items() if pred(value)}
        for label in hit:
            self.hits[point][label] += 1
        self._last[point] = hit
        for cname, (a, b) in self.crosses.items():
            if point == b and a in self._last:  # crosses sample when their second point is sampled
                for x in self._last[a]:
                    for y in hit:
                        self.cross_hits[cname][f"{x}|{y}"] += 1

    def report(self) -> dict:
        def summarize(table: dict[str, dict[str, int]]) -> dict:
            return {n: {"covered": sum(1 for h in b.values() if h), "total": len(b),
                        "missing": [k for k, h in b.items() if not h], "hits": b} for n, b in table.items()}
        pts, crs = summarize(self.hits), summarize(self.cross_hits)
        total = sum(v["total"] for v in pts.values()) + sum(v["total"] for v in crs.values())
        cov = sum(v["covered"] for v in pts.values()) + sum(v["covered"] for v in crs.values())
        return {"group": self.name, "points": pts, "crosses": crs, "covered": cov, "total": total,
                "coverage": round(cov / total, 4) if total else None}

    def save(self, path: str | Path) -> dict:
        """Merge this run's hits into `path` (created if absent) and return the merged report."""
        p = Path(path)
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        for section, table in (("points", self.hits), ("crosses", self.cross_hits)):
            for n, bins in table.items():
                prior = old.get(section, {}).get(n, {}).get("hits", {})
                for label in bins:
                    bins[label] += int(prior.get(label, 0))
        rep = self.report()
        p.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        return rep
