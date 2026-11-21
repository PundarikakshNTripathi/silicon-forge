"""Golden model: an instruction-level reference machine derived from the ISA spec.

The model decodes real words with the same `decode()` the disassembler uses and runs each instruction's `exec` snippet
from the spec, so it cannot drift from the encoding. State writes are masked to their declared widths, exactly as
hardware registers behave. `step()` executes one instruction and reports how many clock cycles it takes; the lockstep
harness compares the RTL's architectural state against `snapshot()` at every retired instruction.

`exec` and `cycles` snippets come from the project's own `isa.yaml`; they are code, so only load specs you trust.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from services.orchestrator.isa.asm import Program, decode
from services.orchestrator.isa.spec import Instruction, IsaSpec


class IllegalInstruction(Exception):
    def __init__(self, addr: int, word: int) -> None:
        super().__init__(f"illegal instruction 0x{word:x} at address {addr}")
        self.addr, self.word = addr, word


class State:
    """Architectural state with hardware-style fixed widths."""

    def __init__(self, spec: IsaSpec) -> None:
        object.__setattr__(self, "_spec", {v.name: v for v in spec.state})
        object.__setattr__(self, "_stall", False)
        self.reset()

    def reset(self) -> None:
        for v in self._spec.values():
            object.__setattr__(self, v.name, v.reset & ((1 << v.width) - 1))
        object.__setattr__(self, "_stall", False)

    def __setattr__(self, name: str, value: int) -> None:
        var = self._spec.get(name)
        if var is None:
            raise AttributeError(f"'{name}' is not architectural state (declared: {', '.join(self._spec)})")
        object.__setattr__(self, name, int(value) & ((1 << var.width) - 1))

    def snapshot(self) -> dict[str, int]:
        return {n: getattr(self, n) for n in self._spec}


@dataclass
class StepResult:
    addr: int
    word: int
    instr: Instruction
    operands: dict[str, int]
    cycles: int
    stalled: bool


class Machine:
    def __init__(self, spec: IsaSpec, program: Program | list[int], illegal: str = "error") -> None:
        self.spec = spec
        self.imem = program.image() if isinstance(program, Program) else list(program)
        self.s = State(spec)
        self.inputs: dict[str, int] = {}
        self.illegal = illegal  # error | halt | nop
        self.cycles = 0
        self.retired = 0
        self._compiled: dict[str, tuple[Any, Any]] = {}
        self._pc_mask = (1 << next(v.width for v in spec.state if v.name == "pc")) - 1

    def reset(self) -> None:
        self.s.reset()
        self.cycles = self.retired = 0

    @property
    def halted(self) -> bool:
        return bool(getattr(self.s, "halted", 0))

    def _code(self, instr: Instruction) -> tuple[Any, Any]:
        if instr.name not in self._compiled:
            self._compiled[instr.name] = (
                compile(instr.exec or "pass", f"<{instr.name}.exec>", "exec"),
                compile(instr.cycles, f"<{instr.name}.cycles>", "eval"),
            )
        return self._compiled[instr.name]

    def step(self) -> StepResult:
        pc = self.s.pc
        word = self.imem[pc] if pc < len(self.imem) else 0
        hit = decode(self.spec, word)
        if hit is None:
            if self.illegal == "error":
                raise IllegalInstruction(pc, word)
            if self.illegal == "halt" and "halted" in self.s._spec:
                self.s.halted = 1
            self.s.pc = pc + 1
            self.cycles += 1
            return StepResult(pc, word, self.spec.instructions[0], {}, 1, False)
        instr, ops = hit
        code, cycles_code = self._code(instr)
        object.__setattr__(self.s, "_stall", False)
        env: dict[str, Any] = {
            **ops,
            "s": self.s,
            "inp": SimpleNamespace(**{k: self.inputs.get(k, 0) for k in self.inputs}, get=lambda k, d=0: self.inputs.get(k, d)),
            "stall": lambda: object.__setattr__(self.s, "_stall", True),
            "mask": self._pc_mask,
        }
        self.s.pc = pc + 1  # pc points at the next instruction before the snippet runs, so a jump just assigns s.pc
        exec(code, {"__builtins__": {}}, env)  # noqa: S102 - spec-defined semantics, see module docstring
        stalled = bool(self.s._stall)
        if stalled:
            self.s.pc = pc  # repeat this instruction next cycle
            n = 1
        else:
            n = int(eval(cycles_code, {"__builtins__": {}}, env))  # noqa: S307
        self.cycles += n
        self.retired += 0 if stalled else 1
        return StepResult(pc, word, instr, ops, n, stalled)

    def run(self, max_steps: int = 100_000) -> int:
        """Run until the program halts or the step budget is spent; returns instructions retired."""
        for _ in range(max_steps):
            if self.halted:
                break
            self.step()
        return self.retired
