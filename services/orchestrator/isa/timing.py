# SPDX-License-Identifier: AGPL-3.0-or-later
"""Timing analysis for protocol programs: how many clock cycles separate pin events?

Bit-banged protocols live or die on exact timing. Two complementary views, both derived from the ISA spec:

* **Static** (`static_gaps`): walk the control-flow graph from every pin-event instruction to the next pin events it can
  reach and report the [min, max] cycles. Loops need a `.bound N` pragma on their branch, otherwise the maximum is
  unbounded (reported as None). A static range with min == max proves the program cannot jitter, whatever the data.
* **Dynamic** (`trace_gaps`): run the golden model and measure the actual gaps between pin events for given inputs.

`.expect N` on a pin-event line asserts that this event happens exactly N cycles after the previous one. It is checked on
both views, and the report says which of them established it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.orchestrator.isa.asm import Program, decode
from services.orchestrator.isa.model import Machine

START = -1  # virtual source: the first pin event after reset


@dataclass
class Gap:
    src: int  # address of the earlier pin event (START for program entry)
    dst: int  # address of the later pin event
    min_cycles: int
    max_cycles: int | None  # None = unbounded

    @property
    def exact(self) -> bool:
        return self.max_cycles is not None and self.min_cycles == self.max_cycles


@dataclass
class TimingReport:
    static: list[Gap] = field(default_factory=list)
    trace: list[tuple[int, int, int]] = field(default_factory=list)  # (src addr, dst addr, measured cycles)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def text(self) -> str:
        lines = []
        for g in self.static:
            src = "start" if g.src == START else f"@{g.src}"
            hi = "unbounded" if g.max_cycles is None else str(g.max_cycles)
            lines.append(f"static  {src:>6} -> @{g.dst}: {g.min_cycles}..{hi} cycles{'  (exact)' if g.exact else ''}")
        for s, d, c in self.trace[:20]:
            lines.append(f"trace   {'start' if s == START else f'@{s}':>6} -> @{d}: {c} cycles")
        lines += [f"note: {n}" for n in self.notes] + [f"PROBLEM: {p}" for p in self.problems]
        return "\n".join(lines)


def _cost(instr, ops) -> int | None:
    try:
        return int(eval(compile(instr.cycles, "<cycles>", "eval"), {"__builtins__": {}}, dict(ops)))  # noqa: S307
    except Exception:  # noqa: BLE001 - depends on run-time state, so it is not a static constant
        return None


def static_gaps(program: Program) -> TimingReport:
    spec = program.spec
    rep = TimingReport()
    nodes: dict[int, dict] = {}
    for p in program.placed:
        if p.instr is None:
            continue
        hit = decode(spec, p.word)
        assert hit is not None
        instr, ops = hit
        cost = _cost(instr, ops)
        if cost is None:
            rep.notes.append(f"@{p.addr} {instr.name}: cycle count depends on run-time state, so it is excluded from "
                             "static bounds (use the trace check)")
            cost = 1
        kind = instr.flow["kind"]
        succ: list[int] = []
        if kind in ("seq", "stall"):
            succ = [p.addr + 1]
        elif kind == "jump":
            succ = [ops[instr.flow["target"]]]
        elif kind == "cond":
            succ = [ops[instr.flow["target"]], p.addr + 1]
        nodes[p.addr] = {"cost": cost, "succ": succ, "pin": instr.pin_event, "bound": p.bound, "kind": kind,
                         "name": instr.name}
    sources = [START] + [a for a, n in nodes.items() if n["pin"]]
    entry = min(nodes, default=0)
    for src in sources:
        first = [entry] if src == START else nodes[src]["succ"]
        start_cost = 0 if src == START else nodes[src]["cost"]
        found, unbounded = _explore(nodes, first, start_cost)
        for dst, (lo, hi) in sorted(found.items()):
            rep.static.append(Gap(src, dst, int(lo), None if unbounded else int(hi)))
        if unbounded:
            rep.notes.append(f"{'start' if src == START else f'@{src}'}: a loop is not bounded, so the maximum gap is "
                             "open (add `.bound N` before the branch that closes the loop)")
    return rep


def _explore(nodes: dict[int, dict], first: list[int], start_cost: int) -> tuple[dict[int, list[int]], bool]:
    """All paths from `first` to the next pin events: {dst: [min, max]} and whether some loop was left unbounded."""
    found: dict[int, list[int]] = {}
    unbounded = False
    budget = 200_000
    stack = [(f, start_cost, 0, {}, {}) for f in first]  # addr, cycles so far, bounded takes so far, seen, takes per node
    while stack:
        addr, acc, total_takes, seen, takes = stack.pop()
        budget -= 1
        if budget < 0:
            return found, True
        n = nodes.get(addr)
        if n is None:
            continue  # ran off the end of the program
        if n["pin"]:
            lo_hi = found.setdefault(addr, [acc, acc])
            lo_hi[0], lo_hi[1] = min(lo_hi[0], acc), max(lo_hi[1], acc)
            continue
        if seen.get(addr) == total_takes:
            unbounded = True  # back here with no bounded branch taken in between: an open loop
            continue
        seen = {**seen, addr: total_takes}
        for i, nxt in enumerate(n["succ"]):
            if n["kind"] == "cond" and i == 0 and n["bound"] is not None:
                if takes.get(addr, 0) >= n["bound"]:
                    continue  # the pragma says this branch cannot be taken again
                stack.append((nxt, acc + n["cost"], total_takes + 1, seen, {**takes, addr: takes.get(addr, 0) + 1}))
            else:
                stack.append((nxt, acc + n["cost"], total_takes, seen, takes))
    return found, unbounded


def trace_gaps(program: Program, inputs: dict[str, int] | None = None, max_steps: int = 20_000,
               max_events: int = 64) -> list[tuple[int, int, int]]:
    """Measure real cycle gaps between pin events by running the golden model."""
    m = Machine(program.spec, program, illegal="halt")
    m.inputs = dict(inputs or {})
    out: list[tuple[int, int, int]] = []
    last_t, last_a = 0, START
    pins = {a for a, p in ((p.addr, p.instr) for p in program.placed) if p and p.pin_event}
    for _ in range(max_steps):
        if m.halted or len(out) >= max_events:
            break
        t = m.cycles
        pc = m.s.pc
        r = m.step()
        if pc in pins and not r.stalled:
            out.append((last_a, pc, t - last_t))
            last_t, last_a = t, pc
    return out


def check_timing(program: Program, inputs: dict[str, int] | None = None) -> TimingReport:
    rep = static_gaps(program)
    rep.trace = trace_gaps(program, inputs)
    expects = {p.addr: p.expect for p in program.placed if p.expect is not None}
    for dst, want in expects.items():
        measured = [c for _, d, c in rep.trace if d == dst]
        if not measured:
            rep.problems.append(f"@{dst}: .expect {want} but this pin event never ran in the trace")
        bad = [c for c in measured if c != want]
        if bad:
            rep.problems.append(f"@{dst}: expected {want} cycles after the previous pin event, measured {sorted(set(bad))}")
        static = [g for g in rep.static if g.dst == dst]
        if static and all(g.exact and g.min_cycles == want for g in static):
            rep.notes.append(f"@{dst}: {want} cycles is proven by the control-flow graph, not only observed")
        elif static and any(g.min_cycles > want or (g.max_cycles is not None and g.max_cycles < want) for g in static):
            rep.problems.append(f"@{dst}: .expect {want} is outside the static range "
                                + ", ".join(f"{g.min_cycles}..{g.max_cycles}" for g in static))
    return rep
