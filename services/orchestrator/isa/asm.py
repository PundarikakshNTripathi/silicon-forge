"""Assembler and disassembler generated from an `IsaSpec`.

Syntax: one statement per line; `;`, `//` or `#` start a comment; `label:` may precede a statement.
Directives:  `.org N`   set the address   `.equ NAME, expr`   define a constant   `.word expr`   emit a raw word
             `.bound N` the next branch is taken at most N times in a row (a loop bound for the timing checker)
             `.expect N` the next pin event must happen exactly N cycles after the previous one
Operands are integers (`10`, `0x1f`, `0b101`), enum names, `.equ` constants, labels, or expressions of those with
`+ - * << >> | & ^` and parentheses. Every error is reported with its line number; all errors are collected, not just the
first.
"""
from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field

from services.orchestrator.isa.spec import Instruction, IsaSpec

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.LShift: operator.lshift,
           ast.RShift: operator.rshift, ast.BitOr: operator.or_, ast.BitAnd: operator.and_, ast.BitXor: operator.xor}


class AsmError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass
class Placed:
    addr: int
    word: int
    instr: Instruction | None
    operands: dict[str, int]
    line: int
    text: str
    bound: int | None = None
    expect: int | None = None


@dataclass
class Program:
    spec: IsaSpec
    words: dict[int, int]
    placed: list[Placed]
    labels: dict[str, int]
    consts: dict[str, int] = field(default_factory=dict)

    def image(self, depth: int | None = None, fill: int = 0) -> list[int]:
        size = depth or (max(self.words, default=-1) + 1)
        return [self.words.get(a, fill) for a in range(size)]

    def listing(self) -> str:
        width = (self.spec.word_bits + 3) // 4
        lines = []
        by_addr: dict[int, list[str]] = {}
        for n, a in self.labels.items():
            by_addr.setdefault(a, []).append(n)
        for p in self.placed:
            for lab in by_addr.get(p.addr, []):
                lines.append(f"{'':>6}  {'':<{width}}  {lab}:")
            lines.append(f"{p.addr:06x}  {p.word:0{width}x}  {p.text}")
        return "\n".join(lines)

    def hex(self, depth: int | None = None) -> str:
        width = (self.spec.word_bits + 3) // 4
        return "\n".join(f"{w:0{width}x}" for w in self.image(depth)) + "\n"


def _eval(expr: str, symbols: dict[str, int]) -> int:
    def walk(n: ast.AST) -> int:
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in symbols:
                return symbols[n.id]
            raise KeyError(n.id)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd, ast.Invert)):
            v = walk(n.operand)
            return -v if isinstance(n.op, ast.USub) else (~v if isinstance(n.op, ast.Invert) else v)
        if isinstance(n, ast.BinOp) and type(n.op) in _BINOPS:
            return _BINOPS[type(n.op)](walk(n.left), walk(n.right))
        raise ValueError(f"unsupported expression '{expr}'")

    return walk(ast.parse(expr.strip(), mode="eval").body)


def _norm(text: str) -> str:
    """Single spaces, and exactly ', ' after a comma, so templates and source compare equal."""
    return re.sub(r"\s+", " ", re.sub(r"\s*,\s*", ", ", text.strip()))


def _template_regex(instr: Instruction) -> re.Pattern[str]:
    rx = ""
    for part in re.split(r"(\{\w+\})", _norm(instr.asm)):
        m = re.fullmatch(r"\{(\w+)\}", part)
        rx += rf"(?P<{m.group(1)}>.+?)" if m else re.escape(part).replace(r"\ ", " ")
    return re.compile(rf"^{rx}$", re.IGNORECASE)


def _strip(line: str) -> str:
    for marker in ("//", ";", "#"):
        line = line.split(marker, 1)[0]
    return line.strip()


class Assembler:
    def __init__(self, spec: IsaSpec) -> None:
        self.spec = spec
        self.templates = [(i, _template_regex(i)) for i in spec.instructions]

    def assemble(self, source: str) -> Program:
        errors: list[str] = []
        consts: dict[str, int] = {}
        labels: dict[str, int] = {}
        stmts: list[tuple[int, int, str, str, int | None, int | None]] = []  # addr, line, kind, text, bound, expect
        addr, bound, expect = 0, None, None
        # ---- pass 1: addresses, labels, constants
        for n, raw in enumerate(source.splitlines(), 1):
            line = _strip(raw)
            while True:
                m = re.match(r"^([A-Za-z_]\w*)\s*:\s*(.*)$", line)
                if not m:
                    break
                if m.group(1) in labels:
                    errors.append(f"line {n}: label '{m.group(1)}' defined twice")
                labels[m.group(1)] = addr
                line = m.group(2).strip()
            if not line:
                continue
            if line.startswith("."):
                parts = line.split(None, 1)
                d, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
                try:
                    if d == ".org":
                        addr = _eval(arg, {**consts, **labels})
                    elif d == ".equ":
                        name, _, e = arg.partition(",")
                        consts[name.strip()] = _eval(e, {**consts, **labels})
                    elif d == ".bound":
                        bound = _eval(arg, consts)
                    elif d == ".expect":
                        expect = _eval(arg, consts)
                    elif d == ".word":
                        stmts.append((addr, n, "word", arg, None, None))
                        addr += 1
                    else:
                        errors.append(f"line {n}: unknown directive '{d}'")
                except (KeyError, ValueError, SyntaxError) as exc:
                    errors.append(f"line {n}: bad directive argument: {exc}")
                continue
            stmts.append((addr, n, "instr", line, bound, expect))
            bound, expect = None, None
            addr += 1
        symbols = {**consts, **labels}
        placed: list[Placed] = []
        words: dict[int, int] = {}
        # ---- pass 2: encode
        for a, n, kind, text, bnd, exp in stmts:
            try:
                p = self._encode_word(a, n, text, symbols) if kind == "word" else self._encode_instr(a, n, text, symbols)
                p.bound, p.expect = bnd, exp
            except (ValueError, KeyError) as exc:
                errors.append(f"line {n}: {exc.args[0] if isinstance(exc, KeyError) else exc}")
                continue
            if a > self.spec.max_addr:
                errors.append(f"line {n}: address {a} does not fit in the program counter")
            if a in words:
                errors.append(f"line {n}: address {a} is already used")
            words[a] = p.word
            placed.append(p)
        if errors:
            raise AsmError(errors)
        return Program(self.spec, words, placed, labels, consts)

    def _encode_word(self, addr: int, line: int, text: str, symbols: dict[str, int]) -> Placed:
        v = _eval(text, symbols)
        if not 0 <= v < 1 << self.spec.word_bits:
            raise ValueError(f".word value {v} does not fit in {self.spec.word_bits} bits")
        return Placed(addr, v, None, {}, line, f".word {text}")

    def _encode_instr(self, addr: int, line: int, text: str, symbols: dict[str, int]) -> Placed:
        text = _norm(text)
        matches = [(i, m) for i, rx in self.templates if (m := rx.match(text))]
        if not matches:
            known = sorted({i.asm.split()[0] for i in self.spec.instructions})
            raise ValueError(f"cannot parse '{text}' (instructions: {', '.join(known)})")
        if len(matches) > 1:
            raise ValueError(f"'{text}' is ambiguous: matches {', '.join(i.name for i, _ in matches)}")
        instr, m = matches[0]
        word = instr.match
        values: dict[str, int] = {}
        for op in instr.operands:
            raw = m.group(op.name).strip()
            if op.kind == "enum":
                assert op.enum is not None
                key = next((k for k in op.enum if k.lower() == raw.lower()), None)
                if key is None:
                    raise ValueError(f"'{raw}' is not valid for {op.name}; choose one of {', '.join(op.enum)}")
                v = op.enum[key]
            else:
                try:
                    v = _eval(raw, symbols)
                except KeyError as exc:
                    raise ValueError(f"unknown symbol {exc}") from exc
                except (ValueError, SyntaxError) as exc:
                    raise ValueError(f"bad value '{raw}' for {op.name}: {exc}") from exc
                if op.kind == "int":
                    lo, hi = -(1 << (op.field.width - 1)), (1 << (op.field.width - 1)) - 1
                    if not lo <= v <= hi:
                        raise ValueError(f"{op.name}={v} is outside {lo}..{hi}")
                    v &= (1 << op.field.width) - 1
                elif not 0 <= v < 1 << op.field.width:
                    raise ValueError(f"{op.name}={v} does not fit in {op.field.width} bits (0..{(1 << op.field.width) - 1})")
            values[op.name] = v
            word |= v << op.field.lsb
        return Placed(addr, word, instr, values, line, text)


# ------------------------------------------------------------------------------------------------ disassembly

def decode(spec: IsaSpec, word: int) -> tuple[Instruction, dict[str, int]] | None:
    """The one place that turns a word into (instruction, operand values); the model and tools all use it."""
    for instr in spec.instructions:
        if word & instr.mask == instr.match:
            vals = {}
            for op in instr.operands:
                raw = (word >> op.field.lsb) & ((1 << op.field.width) - 1)
                if op.kind == "int" and raw >> (op.field.width - 1):
                    raw -= 1 << op.field.width
                vals[op.name] = raw
            return instr, vals
    return None


def disassemble_word(spec: IsaSpec, word: int, labels: dict[int, str] | None = None) -> str:
    hit = decode(spec, word)
    if hit is None:
        return f".word 0x{word:0{(spec.word_bits + 3) // 4}x}"
    instr, vals = hit
    shown = {}
    for op in instr.operands:
        v = vals[op.name]
        if op.kind == "enum":
            assert op.enum is not None
            shown[op.name] = next((k for k, x in op.enum.items() if x == v), str(v))
        elif op.kind == "label" and labels and v in labels:
            shown[op.name] = labels[v]
        else:
            shown[op.name] = str(v)
    return instr.asm.format(**shown)


def disassemble(spec: IsaSpec, words: list[int]) -> str:
    targets: dict[int, str] = {}
    for w in words:
        hit = decode(spec, w)
        if hit:
            for op in hit[0].operands:
                if op.kind == "label":
                    targets.setdefault(hit[1][op.name], f"L{hit[1][op.name]}")
    lines = []
    for a, w in enumerate(words):
        if a in targets:
            lines.append(f"{targets[a]}:")
        lines.append(f"    {disassemble_word(spec, w, targets)}")
    return "\n".join(lines) + "\n"
