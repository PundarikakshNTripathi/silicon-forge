"""ISA specification: one `isa.yaml` is the single source of truth for encoding, assembly syntax, timing and semantics.

Encoding is a pattern string of `word_bits` characters (whitespace ignored), MSB first:
    '0' / '1'   fixed bit that the decoder matches
    '-'         unused bit: the assembler emits 0, the decoder ignores it
    letter      an operand field; every occurrence of one letter is one contiguous field

Everything downstream (assembler, disassembler, Verilog decoder, golden model, documentation, timing checker) is derived
from this file, and the loader rejects ambiguous specs: two instructions whose encodings can match the same word are an
error, as is an operand that is not in the encoding, so the pieces cannot silently disagree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class IsaError(ValueError):
    pass


@dataclass(frozen=True)
class Field_:
    letter: str
    lsb: int
    width: int

    @property
    def msb(self) -> int:
        return self.lsb + self.width - 1

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lsb


@dataclass
class Operand:
    letter: str
    name: str
    kind: str  # uint | int | enum | label
    field: Field_
    enum: dict[str, int] | None = None
    doc: str = ""


@dataclass
class Instruction:
    name: str
    pattern: str
    mask: int  # bits the decoder checks
    match: int  # their required values
    operands: list[Operand]
    asm: str
    cycles: str  # expression over operand names, evaluated per execution
    flow: dict[str, Any]
    exec: str
    pin_event: bool = False
    doc: str = ""

    def operand(self, letter_or_name: str) -> Operand:
        for o in self.operands:
            if letter_or_name in (o.letter, o.name):
                return o
        raise KeyError(letter_or_name)


@dataclass
class StateVar:
    name: str
    width: int
    reset: int = 0


@dataclass
class IsaSpec:
    name: str
    word_bits: int
    state: list[StateVar]
    instructions: list[Instruction]
    enums: dict[str, dict[str, int]] = field(default_factory=dict)
    title: str = ""
    description: str = ""

    @property
    def max_addr(self) -> int:
        pc = next((s for s in self.state if s.name == "pc"), None)
        return (1 << pc.width) - 1 if pc else (1 << 16) - 1

    def by_name(self) -> dict[str, Instruction]:
        return {i.name: i for i in self.instructions}


def _parse_pattern(name: str, pattern: str, word_bits: int) -> tuple[int, int, dict[str, Field_]]:
    bits = re.sub(r"\s+", "", pattern)
    if len(bits) != word_bits:
        raise IsaError(f"{name}: encoding has {len(bits)} bits, expected {word_bits}")
    mask = match = 0
    fields: dict[str, list[int]] = {}
    for i, ch in enumerate(bits):
        pos = word_bits - 1 - i
        if ch in "01":
            mask |= 1 << pos
            match |= int(ch) << pos
        elif ch == "-":
            continue
        elif ch.isalpha():
            fields.setdefault(ch, []).append(pos)
        else:
            raise IsaError(f"{name}: invalid character '{ch}' in encoding")
    out = {}
    for letter, positions in fields.items():
        positions.sort()
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise IsaError(f"{name}: operand field '{letter}' is not contiguous")
        out[letter] = Field_(letter, positions[0], len(positions))
    return mask, match, out


def parse_spec(raw: dict[str, Any]) -> IsaSpec:
    if not isinstance(raw, dict):
        raise IsaError("isa.yaml must be a mapping")
    for key in ("name", "word_bits", "instructions"):
        if key not in raw:
            raise IsaError(f"missing '{key}'")
    word_bits = int(raw["word_bits"])
    enums = {k: {str(n): int(v) for n, v in vals.items()} for k, vals in (raw.get("enums") or {}).items()}
    state = [StateVar(str(n), int(w if not isinstance(w, dict) else w["width"]),
                      int(0 if not isinstance(w, dict) else w.get("reset", 0)))
             for n, w in (raw.get("state") or {}).items()]
    instrs: list[Instruction] = []
    seen: set[str] = set()
    for item in raw["instructions"]:
        name = str(item["name"]).lower()
        if name in seen:
            raise IsaError(f"duplicate instruction '{name}'")
        seen.add(name)
        mask, match, fields = _parse_pattern(name, item["encoding"], word_bits)
        declared = item.get("operands") or {}
        for letter in declared:
            if letter not in fields:
                raise IsaError(f"{name}: operand '{letter}' is declared but not in the encoding")
        operands = []
        for letter, fld in sorted(fields.items(), key=lambda kv: -kv[1].lsb):
            d = declared.get(letter)
            if d is None:
                raise IsaError(f"{name}: encoding field '{letter}' has no operand definition")
            kind = d.get("kind", "enum" if "enum" in d else "uint")
            enum = None
            if kind == "enum":
                ref = d.get("enum")
                enum = enums.get(ref) if isinstance(ref, str) else {str(k): int(v) for k, v in (ref or {}).items()}
                if not enum:
                    raise IsaError(f"{name}: enum for operand '{letter}' is empty or unknown")
                if max(enum.values()) >= 1 << fld.width:
                    raise IsaError(f"{name}: enum value does not fit in operand '{letter}' ({fld.width} bits)")
            elif kind not in ("uint", "int", "label"):
                raise IsaError(f"{name}: unknown operand kind '{kind}'")
            operands.append(Operand(letter, str(d.get("name", letter)), kind, fld, enum, str(d.get("doc", ""))))
        asm = str(item.get("asm", name))
        placeholders = set(re.findall(r"\{(\w+)\}", asm))
        names = {o.name for o in operands}
        if placeholders != names:
            raise IsaError(f"{name}: asm template uses {sorted(placeholders)} but operands are {sorted(names)}")
        flow = dict(item.get("flow") or {"kind": "seq"})
        if flow["kind"] not in ("seq", "jump", "cond", "halt", "stall"):
            raise IsaError(f"{name}: unknown flow kind '{flow['kind']}'")
        if flow["kind"] in ("jump", "cond") and flow.get("target") not in names:
            raise IsaError(f"{name}: flow target must name a label operand")
        instrs.append(Instruction(
            name=name, pattern=item["encoding"], mask=mask, match=match, operands=operands, asm=asm,
            cycles=str(item.get("cycles", 1)), flow=flow, exec=str(item.get("exec", "")),
            pin_event=bool(item.get("pin_event", False)), doc=str(item.get("doc", "")).strip(),
        ))
    # ambiguity: two encodings overlap when they agree on every bit both of them fix
    for i, a in enumerate(instrs):
        for b in instrs[i + 1:]:
            common = a.mask & b.mask
            if (a.match ^ b.match) & common == 0:
                raise IsaError(f"encodings of '{a.name}' and '{b.name}' overlap: some words decode as both")
    return IsaSpec(name=str(raw["name"]), word_bits=word_bits, state=state, instructions=instrs, enums=enums,
                   title=str(raw.get("title", raw["name"])), description=str(raw.get("description", "")).strip())


def load_spec(path: str | Path) -> IsaSpec:
    return parse_spec(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
