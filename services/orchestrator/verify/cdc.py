# SPDX-License-Identifier: AGPL-3.0-or-later
"""A structural clock-domain-crossing check on RTL text. A HEURISTIC, not a CDC sign-off tool.

What it does: finds the clock of every `always_ff` block, then every register that is written in one clock domain and read in
another, and classifies the crossing:

* `synchronized`   the receiving domain captures it in a flop whose only use is the next flop of a chain (a two-flop synchronizer)
* `single_stage`   captured by one flop that is then used for logic: the classic metastability risk
* `unsynchronized` read directly in logic in the other domain

A synchronized crossing of a multi-bit signal gets a note, because a two-flop synchronizer does not keep bits together (use Gray
code, a handshake or an asynchronous FIFO).

What it does not do: parse SystemVerilog (it reads statements with regular expressions, one module at a time), follow
signals through submodule ports or `assign` statements, find asynchronous inputs, check reset-deassertion synchronizers, reconvergence
or handshake protocols, or understand generate blocks. A clean report is therefore not a proof; a finding is worth reading.
It reports what it could not see (`unparsed`) instead of staying quiet about it.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

KEYWORDS = {"if", "else", "begin", "end", "case", "endcase", "default", "for", "while", "posedge", "negedge", "or", "and", "not", "assign",
            "logic", "reg", "wire", "input", "output", "inout", "always_ff", "always_comb", "always", "integer", "int", "unique", "priority"}
ID = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


@dataclass
class Crossing:
    signal: str
    from_clock: str
    to_clock: str
    status: str  # synchronized | single_stage | unsynchronized
    width: int = 1
    note: str = ""


@dataclass
class CdcReport:
    clocks: list[str] = field(default_factory=list)
    crossings: list[Crossing] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[Crossing]:
        return [c for c in self.crossings if c.status != "synchronized"]

    def to_dict(self) -> dict:
        return {"heuristic": True, "clocks": self.clocks, "crossings": [asdict(c) for c in self.crossings], "problems": len(self.problems),
                "unparsed": self.unparsed}


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def _widths(text: str) -> dict[str, int]:
    """Declared widths of simple `logic [H:L] a, b;` style declarations (numeric bounds only; anything else counts as 1)."""
    out: dict[str, int] = {}
    for m in re.finditer(r"\b(?:logic|reg|wire)\s*(?:\[\s*(\d+)\s*:\s*(\d+)\s*\])?\s*([A-Za-z_][\w\s,]*?)\s*[;=,)]", text):
        w = abs(int(m.group(1)) - int(m.group(2))) + 1 if m.group(1) is not None else 1
        for name in re.findall(r"[A-Za-z_]\w*", m.group(3)):
            out.setdefault(name, w)
    for m in re.finditer(r"\b(?:input|output)\s+(?:logic|reg|wire)?\s*(?:\[\s*(\d+)\s*:\s*(\d+)\s*\])?\s*([A-Za-z_]\w*)", text):
        out.setdefault(m.group(3), abs(int(m.group(1)) - int(m.group(2))) + 1 if m.group(1) is not None else 1)
    return out


def _blocks(text: str) -> list[tuple[str, str]]:
    """(clock, body) for every `always_ff @(posedge|negedge clk ...)` block. Bodies are cut by balancing begin/end."""
    found: list[tuple[str, str]] = []
    for m in re.finditer(r"always_ff\s*@\s*\(\s*(?:posedge|negedge)\s+([A-Za-z_]\w*)[^)]*\)\s*", text):
        i = m.end()
        if text.startswith("begin", i):
            depth, j = 0, i
            for t in re.finditer(r"\b(begin|end)\b", text[i:]):
                depth += 1 if t.group(1) == "begin" else -1
                if depth == 0:
                    j = i + t.end()
                    break
            found.append((m.group(1), text[i:j]))
        else:
            j = text.find(";", i)
            found.append((m.group(1), text[i:j + 1 if j != -1 else len(text)]))
    return found


def _assigned(body: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*<=", body)}


def _statements(body: str) -> list[str]:
    return [s.strip() for s in re.split(r";", body) if s.strip()]


def _core(stmt: str) -> str:
    """A statement without its `begin`, `else` and `if (...)` prefixes, so `else if (en) q <= d` becomes `q <= d`."""
    s = stmt.split("begin")[-1].strip()
    while True:
        s = re.sub(r"^else\b\s*", "", s)
        if not re.match(r"if\b", s):
            return s
        i = s.find("(")
        depth = 0
        for j in range(i, len(s)):
            depth += (s[j] == "(") - (s[j] == ")")
            if depth == 0:
                s = s[j + 1:].strip()
                break
        else:
            return s


def analyze(source: str) -> CdcReport:
    text = _strip_comments(source)
    rep = CdcReport()
    blocks = _blocks(text)
    if not blocks:
        rep.unparsed.append("no always_ff blocks found")
        return rep
    width = _widths(text)
    rep.clocks = sorted({clk for clk, _ in blocks})
    if len(rep.clocks) < 2:
        return rep  # one clock domain: nothing can cross
    driver: dict[str, str] = {}  # register -> the clock of the block that writes it
    multi: set[str] = set()
    for clk, body in blocks:
        for sig in _assigned(body):
            if driver.setdefault(sig, clk) != clk and sig not in multi:
                multi.add(sig)
                rep.unparsed.append(f"{sig} is written in two clock domains ({driver[sig]} and {clk}): not analysed")
    for sig in multi:
        driver.pop(sig, None)
    seen: set[tuple[str, str]] = set()
    for clk, body in blocks:
        stmts = _statements(body)
        for sig, src_clk in driver.items():
            if src_clk == clk or (sig, clk) in seen:
                continue
            uses = [s for s in stmts if re.search(rf"(?<![\w$]){re.escape(sig)}(?![\w$])", s)]
            if not uses:
                continue
            seen.add((sig, clk))
            captures: list[str] = []  # registers that just copy `sig`
            direct = False
            for s in uses:
                m = re.fullmatch(rf"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*<=\s*{re.escape(sig)}(?:\s*\[[^\]]*\])?", _core(s))
                if m and m.group(1) in _assigned(body):
                    captures.append(m.group(1))
                else:
                    direct = True
            w = width.get(sig, 1)
            if direct and not captures:
                rep.crossings.append(Crossing(sig, src_clk, clk, "unsynchronized", w, "read directly in logic of another clock domain"))
                continue
            status, note = "single_stage", "captured by one flop that is used for logic: add a second flop"
            for first in captures:
                follow = [s for _, b in blocks if _ == clk for s in _statements(b) if re.search(rf"(?<![\w$]){re.escape(first)}(?![\w$])", s)]
                chain = [s for s in follow if re.fullmatch(r"[A-Za-z_]\w*\s*(?:\[[^\]]*\])?\s*<=\s*" + re.escape(first), _core(s))]
                others = [s for s in follow if s not in chain and not re.match(rf"\s*{re.escape(first)}\s*(?:\[[^\]]*\])?\s*<=", _core(s))]
                if chain and not others:
                    status, note = "synchronized", "two-flop synchronizer"
                    break
            if direct and status == "synchronized":
                status, note = "unsynchronized", "also read directly in logic, beside the synchronizer"
            elif status == "synchronized" and w > 1:
                note = f"two-flop synchronizer on a {w}-bit signal: bits can arrive in different cycles; use Gray code, a handshake or an async FIFO"
            rep.crossings.append(Crossing(sig, src_clk, clk, status, w, note))
    return rep
