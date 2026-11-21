"""Constrained-random program generation from an ISA spec, for lockstep regression.

Every word produced is a valid encoding (it is built from the spec, never from random bits), operands are biased
toward the values that break hardware (0, 1, the maximum, the maximum minus one), and branch targets stay inside the
program. Instruction weights let a test emphasise some instructions; `forbid` removes ones the test cannot run.
"""
from __future__ import annotations

import random

from services.orchestrator.isa.spec import Instruction, IsaSpec

_CORNER_BIAS = 0.3


def random_operand(op, rng: random.Random, length: int) -> int:
    if op.enum:
        return rng.choice(sorted(op.enum.values()))
    width = op.field.width
    top = (1 << width) - 1
    if op.kind == "label":
        return rng.randrange(max(1, min(length, top + 1)))
    if rng.random() < _CORNER_BIAS:
        return rng.choice([0, 1, top, max(0, top - 1)]) & top
    return rng.randrange(top + 1)


def random_word(instr: Instruction, rng: random.Random, length: int) -> int:
    word = instr.match
    for op in instr.operands:
        word |= random_operand(op, rng, length) << op.field.lsb
    return word


def random_program(spec: IsaSpec, rng: random.Random, length: int, weights: dict[str, float] | None = None,
                   forbid: frozenset[str] | set[str] = frozenset(), end_with: str | None = None) -> list[int]:
    """`length` valid instruction words; the last one is `end_with` (e.g. "halt") when given."""
    pool = [i for i in spec.instructions if i.name not in forbid]
    if not pool:
        raise ValueError("every instruction is forbidden")
    w = [float((weights or {}).get(i.name, 1.0)) for i in pool]
    words = [random_word(rng.choices(pool, w)[0], rng, length) for _ in range(length)]
    if end_with:
        words[-1] = random_word(spec.by_name()[end_with], rng, length)
    return words
