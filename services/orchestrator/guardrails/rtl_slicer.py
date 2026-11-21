# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backward cone-of-influence (COI) slicer for minimal-diff RTL repair.

Inspired by Veri-Sure (arXiv:2601.19747): given a failing signal, keep only the statements that can influence it, so the
repair agent patches the affected logic instead of rewriting the module.

This is a statement-level analysis of synthesizable Verilog/SystemVerilog, not a full elaborator. What it models:
  * `assign` and net/variable declarations with initializers;
  * `always*` blocks, with the clock/reset edges of edge-triggered blocks counted as control dependencies;
  * `if`/`else`, `case/casez/casex`, `for/while`: the conditions guarding an assignment are dependencies of its target,
    so an enable or a mux select is in the cone of the register it gates;
  * multi-line statements, bit/part selects and concatenations on the left-hand side, sized literals (`4'd5`), comments;
  * module instances: every signal connected to one instance is treated as depending on the others (direction is not
    known here), a sound over-approximation that keeps the cone from stopping at a hierarchy boundary.
Not modelled: generate-time elaboration, parameters as signals, `function`/`task` bodies (skipped), hierarchical names.
Within those limits the result is a superset of the true cone, never a subset.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

_KEYWORDS = {
    "module", "endmodule", "macromodule", "input", "output", "inout", "logic", "wire", "reg", "tri", "supply0", "supply1",
    "integer", "genvar", "parameter", "localparam", "signed", "unsigned", "always", "always_ff", "always_comb",
    "always_latch", "initial", "final", "posedge", "negedge", "begin", "end", "if", "else", "case", "casez", "casex",
    "endcase", "default", "assign", "for", "while", "repeat", "forever", "foreach", "generate", "endgenerate", "function",
    "endfunction", "task", "endtask", "unique", "priority", "or", "and", "not", "typedef", "enum", "struct", "packed",
    "var", "int", "bit", "byte", "automatic", "static", "return", "disable", "fork", "join", "interface", "endinterface",
    "package", "endpackage", "import", "export", "bind", "specify", "endspecify", "defparam",
}
_DECL = {"wire", "logic", "reg", "integer", "int", "bit", "byte", "tri", "supply0", "supply1", "var", "input", "output",
         "inout"}
_SKIP_DECL = {"parameter", "localparam", "genvar", "typedef", "import", "export", "defparam"}
_ASSIGN_OPS = {"=", "<=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=", "<<<=", ">>>="}
_OPEN, _CLOSE = {"(", "[", "{"}, {")", "]", "}"}
_DIRECTIVES = re.compile(
    r"^[ \t]*`(?:define|undef|ifdef|ifndef|elsif|else|endif|include|timescale|default_nettype|resetall|celldefine|"
    r"endcelldefine)[^\n]*", re.M)
_TOKEN = re.compile(
    r"""(?P<num>\d+\s*'[sS]?[bBoOdDhH]\s*[0-9a-fA-F_xXzZ?]+|'[01xXzZ]|\d[\d_]*(?:\.\d+)?)
      |(?P<id>[A-Za-z_][A-Za-z0-9_$]*)
      |(?P<sys>\$[A-Za-z_][A-Za-z0-9_]*)
      |(?P<op><<<=|>>>=|<<=|>>=|<<<|>>>|===|!==|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<=|>=|==|!=|<<|>>|&&|\|\||\+:|-:|::|[-+*/%&|^~!<>=?:;,.()\[\]{}@#])""",
    re.X)

Tok = tuple[str, str, int]  # (kind, text, line)


@dataclass
class ModuleDeps:
    name: str
    deps: dict[str, set[str]] = field(default_factory=dict)  # signal -> signals it depends on (data and control)
    stmt_lines: dict[str, set[int]] = field(default_factory=dict)  # signal -> lines of the statements that assign it
    decl_lines: dict[str, set[int]] = field(default_factory=dict)  # signal -> lines that declare it


@dataclass
class Slice:
    target: str
    module: str | None
    signals: set[str]
    lines: list[int]
    found: bool  # False when the target is never assigned (an input, a typo): nothing to slice


class RtlDependencySlicer:
    """Parse once per source with `analyze`; cone and render with `extract_coi_slice` (kept for existing callers)."""

    def __init__(self) -> None:
        self._cache: tuple[str, dict[str, ModuleDeps]] | None = None

    def analyze(self, rtl_source: str) -> dict[str, ModuleDeps]:
        if self._cache and self._cache[0] == rtl_source:
            return self._cache[1]
        mods = _Parser(rtl_source).parse()
        self._cache = (rtl_source, mods)
        return mods

    def build_dependency_graph(self, rtl_source: str) -> tuple[dict[str, set[str]], dict[str, list[int]]]:
        """Flat view over all modules (signal -> dependencies, signal -> assigning lines). Kept for compatibility."""
        deps: dict[str, set[str]] = {}
        lines: dict[str, list[int]] = {}
        for m in self.analyze(rtl_source).values():
            for k, v in m.deps.items():
                deps.setdefault(k, set()).update(v)
            for k, v in m.stmt_lines.items():
                lines.setdefault(k, []).extend(sorted(v))
        return deps, lines

    def cone(self, rtl_source: str, target_signal: str, module: str | None = None) -> Slice:
        target = _base_name(target_signal)
        mods = self.analyze(rtl_source)
        candidates = [mods[module]] if module in mods else list(mods.values())
        hit = next((m for m in candidates if target in m.stmt_lines), None)
        if hit is None:
            return Slice(target, module, set(), [], False)
        seen: set[str] = set()
        stack = [target]
        while stack:
            s = stack.pop()
            if s in seen:
                continue
            seen.add(s)
            stack.extend(p for p in hit.deps.get(s, ()) if p not in seen)
        keep: set[int] = set()
        for s in seen:
            keep |= hit.stmt_lines.get(s, set())
            keep |= hit.decl_lines.get(s, set())
        return Slice(target, hit.name, seen, sorted(keep), True)

    def extract_coi_slice(self, rtl_source: str, target_signal: str, context_lines: int = 2,
                          module: str | None = None) -> str:
        sl = self.cone(rtl_source, target_signal, module)
        if not sl.found or not sl.lines:
            return rtl_source
        src = rtl_source.splitlines()
        want: set[int] = set()
        for ln in sl.lines:
            want.update(range(max(1, ln - context_lines), min(len(src), ln + context_lines) + 1))
        out = [f"// Backward cone-of-influence of '{sl.target}' in module '{sl.module}': {len(sl.signals)} signals "
               f"({', '.join(sorted(sl.signals))}); {len(want)} of {len(src)} lines kept"]
        prev = 0
        for ln in sorted(want):
            if prev and ln != prev + 1:
                out.append("     | ...")
            out.append(f"{ln:4d} | {src[ln - 1]}")
            prev = ln
        return "\n".join(out)


# ---------------------------------------------------------------------------------------------- parser

def _base_name(sig: str) -> str:
    return re.sub(r"\[.*?\]", "", sig.strip()).split(".")[-1]


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    src = re.sub(r'"(?:\\.|[^"\\])*"', '""', src)
    src = _DIRECTIVES.sub("", src)
    return re.sub(r"`\w+", " ", src)  # macro uses are not signals


class _Parser:
    def __init__(self, source: str) -> None:
        text = _strip(source)
        starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.toks: list[Tok] = [(m.lastgroup or "", m.group(), bisect.bisect_right(starts, m.start()))
                                for m in _TOKEN.finditer(text)]
        self.i = 0
        self.mods: dict[str, ModuleDeps] = {}
        self.cur: ModuleDeps | None = None

    # -- token helpers
    def peek(self, k: int = 0) -> Tok:
        j = self.i + k
        if j < len(self.toks):
            return self.toks[j]
        return ("eof", "", self.toks[-1][2] if self.toks else 1)

    def next(self) -> Tok:
        t = self.peek()
        self.i += 1
        return t

    def at(self, text: str) -> bool:
        return self.peek()[1] == text

    def balanced(self, open_: str, close: str) -> list[Tok]:
        """Consume up to the matching `close` (the opener is already consumed); return the inner tokens."""
        depth, out = 1, []
        while self.peek()[0] != "eof":
            t = self.next()
            if t[1] == open_:
                depth += 1
            elif t[1] == close:
                depth -= 1
                if depth == 0:
                    break
            out.append(t)
        return out

    @staticmethod
    def names(toks: list[Tok]) -> set[str]:
        """Signal-like identifiers in a token run: keywords, numbers and `.member` names are excluded."""
        return {text for k, (kind, text, _) in enumerate(toks)
                if kind == "id" and text not in _KEYWORDS and not (k and toks[k - 1][1] == ".")}

    def finish(self) -> None:
        """Consume through the next `;` at nesting depth 0."""
        depth = 0
        while self.peek()[0] != "eof":
            t = self.next()[1]
            depth += t in _OPEN
            depth -= t in _CLOSE
            if t == ";" and depth <= 0:
                return

    # -- recording
    def record(self, targets: set[str], reads: set[str], lines: set[int]) -> None:
        if self.cur is None:
            return
        for t in targets:
            self.cur.deps.setdefault(t, set()).update(reads)
            self.cur.stmt_lines.setdefault(t, set()).update(lines)

    def declare(self, name: str, line: int) -> None:
        if self.cur is not None:
            self.cur.decl_lines.setdefault(name, set()).add(line)

    # -- grammar
    def parse(self) -> dict[str, ModuleDeps]:
        while self.peek()[0] != "eof":
            self.item(frozenset(), ())
        return self.mods

    def item(self, control: frozenset[str], ctrl_lines: tuple[int, ...]) -> None:
        kind, text, _ = self.peek()
        if text in ("module", "macromodule"):
            return self.module_header()
        if text == "endmodule":
            self.next()
            self.cur = None
            return
        if text in ("function", "task"):
            end = "end" + text
            while self.peek()[0] != "eof" and not self.at(end):
                self.next()
            self.next()
            return
        if text == "assign":
            return self.assign_stmt(control, ctrl_lines)
        if text in ("always", "always_ff", "always_comb", "always_latch", "initial", "final"):
            return self.always(control, ctrl_lines)
        if text in ("generate", "endgenerate", ";"):
            self.next()
            return
        if text in _SKIP_DECL:
            return self.finish()
        if text in _DECL:
            return self.declaration(control, ctrl_lines)
        if kind == "id" and text not in _KEYWORDS and self.looks_like_instance():
            return self.instance()
        self.statement(control, ctrl_lines)

    def module_header(self) -> None:
        self.next()
        name = self.next()[1]
        self.cur = self.mods.setdefault(name, ModuleDeps(name))
        if self.at("#"):
            self.next()
            if self.at("("):
                self.next()
                self.balanced("(", ")")
        if self.at("("):
            self.next()
            inner = self.balanced("(", ")")
            depth = 0
            for k, (kind, text, line) in enumerate(inner):
                depth += text in _OPEN
                depth -= text in _CLOSE
                nxt = inner[k + 1][1] if k + 1 < len(inner) else ","
                if depth == 0 and kind == "id" and text not in _KEYWORDS and nxt in (",", "=", "["):
                    self.declare(text, line)
        while self.peek()[0] != "eof" and not self.at(";"):
            self.next()
        self.next()

    def declaration(self, control: frozenset[str], ctrl_lines: tuple[int, ...]) -> None:
        start_line = self.peek()[2]
        parts: list[list[Tok]] = [[]]
        depth = 0
        while self.peek()[0] != "eof":
            t = self.next()
            depth += t[1] in _OPEN
            depth -= t[1] in _CLOSE
            if t[1] == ";" and depth <= 0:
                break
            if t[1] == "," and depth == 0:
                parts.append([])
            else:
                parts[-1].append(t)
        for part in parts:
            eq = next((k for k, t in enumerate(part) if t[1] == "="), None)
            head = part if eq is None else part[:eq]
            ids = [t for t in head if t[0] == "id" and t[1] not in _KEYWORDS]
            if not ids:
                continue
            name = ids[-1][1]
            self.declare(name, ids[-1][2])
            if eq is not None:  # `wire x = a & b;` is a continuous assignment
                lines = {start_line, *(t[2] for t in part), *ctrl_lines}
                self.record({name}, self.names(part[eq + 1:]) | set(control), lines)

    def looks_like_instance(self) -> bool:
        a, b = self.peek(1), self.peek(2)
        return a[1] == "#" or (a[0] == "id" and a[1] not in _KEYWORDS and b[1] in ("(", "["))

    def instance(self) -> None:
        self.next()  # module name
        if self.at("#"):
            self.next()
            if self.at("("):
                self.next()
                self.balanced("(", ")")
        self.next()  # instance name
        while self.at("["):  # arrays of instances
            self.next()
            self.balanced("[", "]")
        if self.at("("):
            self.next()
            inner = self.balanced("(", ")")
            sigs = self.names(inner)
            # port direction is unknown here: connected signals may influence one another
            self.record(sigs, sigs, {t[2] for t in inner})
        self.finish()

    def assign_stmt(self, control: frozenset[str], ctrl_lines: tuple[int, ...]) -> None:
        self.next()
        while self.peek()[0] != "eof":
            if self.at("#"):  # delay
                self.next()
                if self.at("("):
                    self.next()
                    self.balanced("(", ")")
                else:
                    self.next()
            if self.at("("):  # drive strength
                self.next()
                self.balanced("(", ")")
            self.assignment(control, ctrl_lines)
            if not self.at(","):
                break
            self.next()
        if self.at(";"):
            self.next()

    def always(self, control: frozenset[str], ctrl_lines: tuple[int, ...]) -> None:
        kw = self.next()[1]
        edges: set[str] = set()
        if self.at("@"):
            self.next()
            if self.at("("):
                self.next()
                inner = self.balanced("(", ")")
                if any(t[1] in ("posedge", "negedge") for t in inner):
                    edges = self.names(inner)
            elif self.at("*"):
                self.next()
        if kw in ("initial", "final"):  # not synthesized logic: parse to stay in sync, record nothing about it
            saved, self.cur = self.cur, None
            self.statement(frozenset(), ())
            self.cur = saved
            return
        self.statement(control | frozenset(edges), ctrl_lines)

    def statement(self, control: frozenset[str], ctrl_lines: tuple[int, ...]) -> None:
        kind, text, line = self.peek()
        if kind == "eof":
            return
        if text == "begin":
            self.next()
            if self.at(":"):
                self.next()
                self.next()
            while self.peek()[0] != "eof" and not self.at("end"):
                self.item(control, ctrl_lines)
            self.next()
            if self.at(":"):
                self.next()
                self.next()
            return
        if text in ("unique", "priority"):
            self.next()
            return self.statement(control, ctrl_lines)
        if text == "if":
            self.next()
            self.next()  # (
            cond = self.balanced("(", ")")
            ids = self.names(cond)
            lines = ctrl_lines + tuple(t[2] for t in cond) + (line,)
            self.statement(control | ids, lines)
            if self.at("else"):
                self.next()
                self.statement(control | ids, lines)
            return
        if text in ("case", "casez", "casex"):
            return self.case_stmt(control, ctrl_lines)
        if text in ("for", "while", "repeat", "foreach"):
            self.next()
            self.next()
            head = self.balanced("(", ")")
            return self.statement(control | self.names(head), ctrl_lines + tuple(t[2] for t in head) + (line,))
        if text == "forever":
            self.next()
            return self.statement(control, ctrl_lines)
        if text in ("assign", "always", "always_ff", "always_comb", "always_latch") or text in _DECL or text in _SKIP_DECL:
            return self.item(control, ctrl_lines)
        if kind == "sys" or text in ("return", "disable"):
            return self.finish()
        if text == ";":
            self.next()
            return
        if kind == "id" and text in _KEYWORDS:  # a stray `else`, `end`, `endcase`, ...: skip it
            self.next()
            return
        if kind == "id" and self.looks_like_instance():
            return self.instance()
        if kind == "id" or text in ("{", "("):
            self.assignment(control, ctrl_lines)
            if self.at(";"):
                self.next()
            return
        self.next()

    def case_stmt(self, control: frozenset[str], ctrl_lines: tuple[int, ...]) -> None:
        line = self.next()[2]
        self.next()  # (
        head = self.balanced("(", ")")
        sel = self.names(head)
        base = ctrl_lines + tuple(t[2] for t in head) + (line,)
        while self.peek()[0] != "eof" and not self.at("endcase"):
            label: list[Tok] = []
            depth = 0
            while self.peek()[0] != "eof" and not self.at("endcase") and not (self.at(":") and depth == 0):
                t = self.next()
                depth += t[1] in _OPEN
                depth -= t[1] in _CLOSE
                label.append(t)
            if self.at("endcase") or self.peek()[0] == "eof":
                break
            self.next()  # ':'
            self.statement(control | sel | self.names(label), base + tuple(t[2] for t in label))
        self.next()

    def assignment(self, control: frozenset[str], ctrl_lines: tuple[int, ...]) -> None:
        lhs: list[Tok] = []
        depth = 0
        while self.peek()[0] != "eof":
            t = self.peek()
            if depth == 0 and t[1] in _ASSIGN_OPS:
                break
            if depth == 0 and t[1] == ";":
                return  # a call or an unsupported construct, not an assignment
            self.next()
            depth += t[1] in _OPEN
            depth -= t[1] in _CLOSE
            lhs.append(t)
        if self.peek()[0] == "eof":
            return
        op = self.next()[1]
        if self.at("#"):  # intra-assignment delay
            self.next()
            self.next()
        rhs: list[Tok] = []
        depth = 0
        while self.peek()[0] != "eof":
            t = self.peek()
            if depth == 0 and t[1] in (";", ","):
                break
            self.next()
            depth += t[1] in _OPEN
            depth -= t[1] in _CLOSE
            rhs.append(t)
        targets: set[str] = set()
        index_reads: set[str] = set()
        bracket = 0
        for k, (kind, text, _) in enumerate(lhs):
            if text == "[":
                bracket += 1
            elif text == "]":
                bracket -= 1
            elif kind == "id" and text not in _KEYWORDS and not (k and lhs[k - 1][1] == "."):
                (index_reads if bracket else targets).add(text)
        reads = self.names(rhs) | index_reads | set(control)
        if op not in ("=", "<="):
            reads |= targets  # a compound assignment reads its own target
        self.record(targets, reads, {t[2] for t in lhs} | {t[2] for t in rhs} | set(ctrl_lines))
