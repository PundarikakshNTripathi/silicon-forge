#!/usr/bin/env python3
"""Mutation testing for Verilog/SystemVerilog: how many planted bugs does your test suite actually catch?

Standard library only, so it runs wherever the EDA tools run (native, WSL, Docker) and can be vendored into a project's CI.

    sf_mutate.py --root . --file rtl/core.v --test "python3 run_tests.py" [--top core] [--max 80] [--seed 1] [--jobs 4]
                 [--equiv-cmd yosys] [--out mutation.json]

How it works
  1. The test command must pass on the original code (the baseline); otherwise nothing below means anything.
  2. Mutants are small single-point changes (operator swaps, constant +/-1, negated conditions, deleted non-blocking
     assignments), chosen deterministically from a seed. Declarations, widths, indices, parameters, for-headers, delays
     and strings are left alone.
  3. Each mutant that fails to parse is `invalid`. If Yosys is available, a mutant it can PROVE equivalent to the original
     is `equivalent` and excluded: a test suite cannot be blamed for missing a change that changes nothing. A mutant it
     cannot decide (sequential designs with unreachable states) stays in the pool, which can only lower the score.
  4. The test command runs on every remaining mutant: non-zero exit or timeout = `killed`, exit 0 = `survived`.
Score = killed / (killed + survived). Survivors are listed with file, line and the exact change, so each one is a concrete
hole in the tests to close, or a candidate dead-logic finding.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Mutant:
    id: int
    file: str
    line: int
    op: str
    original: str
    replacement: str
    start: int
    end: int


# ------------------------------------------------------------------------------------------------ scanning

def mask(text: str) -> str:
    """Same-length copy with comments and string contents blanked, so scanning never matches inside them."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j == -1 else j
            out[i:j] = " " * (j - i)
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out[i:j] = [" " if c != "\n" else "\n" for c in text[i:j]]
            i = j
        elif text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out[i + 1:j] = " " * (j - i - 1)
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _depths(masked: str) -> tuple[list[int], list[int]]:
    """Per-offset parenthesis and bracket nesting depth."""
    par, brk = [], []
    p = b = 0
    for ch in masked:
        if ch == "(":
            p += 1
        elif ch == ")":
            p = max(0, p - 1)
        elif ch == "[":
            b += 1
        elif ch == "]":
            b = max(0, b - 1)
        par.append(p)
        brk.append(b)
    return par, brk


_SKIP_LINE = re.compile(r"^\s*(parameter|localparam|input|output|inout|wire|reg|logic|integer|genvar|`|module|endmodule|"
                        r"function|task|initial|import|typedef|timeunit)\b")
_FOR = re.compile(r"\bfor\s*\(")
_BIN = [("+", "-"), ("-", "+"), ("&", "|"), ("|", "&"), ("^", "&"), ("<<", ">>"), (">>", "<<"), ("&&", "||"),
        ("||", "&&"), ("==", "!="), ("!=", "=="), (">=", "<"), ("<", ">="), (">", "<="), ("<=", ">")]
_OPERAND_END = re.compile(r"[\w)\]']")


def _line_of(text: str, off: int) -> int:
    return text.count("\n", 0, off) + 1


def _in_for_header(masked: str, off: int) -> bool:
    for m in _FOR.finditer(masked, max(0, off - 200), off):
        depth, i = 0, m.end() - 1
        while i < len(masked):
            depth += masked[i] == "("
            depth -= masked[i] == ")"
            if depth == 0:
                break
            i += 1
        if m.start() < off < i:
            return True
    return False


def enumerate_mutants(text: str, file: str = "") -> list[Mutant]:
    m = mask(text)
    par, brk = _depths(m)
    line_starts = [0] + [i + 1 for i, c in enumerate(m) if c == "\n"]
    skip_lines = {n for n, s in enumerate(line_starts, 1)
                  if _SKIP_LINE.match(m[s:m.find("\n", s) if m.find("\n", s) != -1 else len(m)])}
    found: list[Mutant] = []

    def add(op: str, start: int, end: int, repl: str) -> None:
        ln = _line_of(text, start)
        if ln in skip_lines or _in_for_header(m, start):
            return
        found.append(Mutant(0, file, ln, op, text[start:end], repl, start, end))

    # binary operators (longest match first), never inside [] or after a unary position
    for mo in re.finditer(r"<<<|>>>|===|!==|&&|\|\||<<|>>|==|!=|>=|<=|[-+&|^<>]", m):
        tok, s, e = mo.group(0), mo.start(), mo.end()
        if brk[s]:
            continue
        if tok in ("<<<", ">>>", "===", "!=="):
            continue
        prev = m[:s].rstrip()[-1:] if m[:s].strip() else ""
        if not _OPERAND_END.match(prev):
            continue  # unary (reduction, negative number) or start of expression
        if tok == "<=" and par[s] == 0:
            continue  # a non-blocking assignment, not a comparison
        if tok in ("<", ">") and m[e:e + 1] == "=":
            continue
        if tok in ("-", "+", "&", "|", "^") and m[e:e + 1] in ("=",):
            continue
        for old, new in _BIN:
            if old == tok:
                add(f"op {tok}->{new}", s, e, new)
                break
    # numeric constants: sized (4'd5) and plain decimals, not widths, indices, delays or declarations
    for mo in re.finditer(r"(?<![\w'.#])(\d+)?'([sS]?[bBdDhHoO])([0-9a-fA-F_]+)\b|(?<![\w'.#\[:])\b(\d+)\b(?![\w']|\s*:)", m):
        s, e = mo.start(), mo.end()
        if brk[s]:
            continue
        if mo.group(3):
            base = mo.group(2)[-1].lower()
            digits = mo.group(3).replace("_", "")
            if not re.fullmatch(r"[0-9a-fA-F]+", digits):
                continue
            radix = {"b": 2, "d": 10, "h": 16, "o": 8}[base]
            try:
                val = int(digits, radix)
            except ValueError:
                continue
            width = int(mo.group(1)) if mo.group(1) else None
            top = (1 << width) - 1 if width else None  # largest value that fits; None for an unsized literal
            new_val = val - 1 if (top is not None and val == top and val > 0) else val + 1
            if new_val < 0 or (top is not None and new_val > top):
                continue
            fmt = {"b": "b", "d": "d", "h": "x", "o": "o"}[base]
            pad = len(digits) if base in "bho" else 0
            add(f"const {val}->{new_val}", s, e,
                f"{mo.group(1) or ''}'{mo.group(2)}{format(new_val, fmt).zfill(pad)}")
        else:
            val = int(mo.group(4))
            add(f"const {val}->{val + 1}", s, e, str(val + 1))
    # negate a condition: if (c) -> if (!(c))
    for mo in re.finditer(r"\bif\s*\(", m):
        depth, i = 0, mo.end() - 1
        while i < len(m):
            depth += m[i] == "("
            depth -= m[i] == ")"
            if depth == 0:
                break
            i += 1
        else:
            continue
        add("negate condition", mo.end(), i, f"!({text[mo.end():i]})")
    # delete a non-blocking assignment statement
    for mo in re.finditer(r"(?m)^[ \t]*([A-Za-z_][\w.\[\]:+\-* ]*?)\s*<=\s*[^;]+;", m):
        if par[mo.start()] == 0:
            add("delete assignment", mo.start(), mo.end(), ";")
    found.sort(key=lambda x: (x.start, x.op))
    # drop exact duplicates (same span and replacement)
    seen, uniq = set(), []
    for x in found:
        key = (x.start, x.end, x.replacement)
        if key not in seen:
            seen.add(key)
            uniq.append(x)
    for i, x in enumerate(uniq):
        x.id = i
    return uniq


def apply_mutant(text: str, mu: Mutant) -> str:
    return text[:mu.start] + mu.replacement + text[mu.end:]


# ------------------------------------------------------------------------------------------------ equivalence

def equivalent(orig: Path, mutated: Path, top: str, yosys: str, timeout: float = 60.0) -> bool | None:
    """True = proven equivalent, False = a difference is possible/proven, None = could not be determined."""
    script = (
        f"read_verilog -sv {orig}; prep -top {top}; rename {top} gold; design -stash gold; "
        f"read_verilog -sv {mutated}; prep -top {top}; rename {top} gate; design -stash gate; "
        "design -copy-from gold -as gold gold; design -copy-from gate -as gate gate; "
        "async2sync; equiv_make gold gate eq; hierarchy -top eq; equiv_simple; equiv_induct -seq 5; equiv_status -assert"
    )
    try:
        p = subprocess.run([yosys, "-q", "-p", script], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode == 0:
        return True
    low = (p.stdout + p.stderr).lower()
    if "syntax error" in low or "error:" in low and "equiv" not in low:
        return None
    return False


def parses(path: Path, yosys: str, timeout: float = 30.0) -> bool:
    """Does Yosys accept the file as SystemVerilog? Used to separate invalid mutants from ones the tests killed."""
    try:
        p = subprocess.run([yosys, "-q", "-p", f"read_verilog -sv -I{path.parent} {path}"], capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return True  # cannot tell; do not discard the mutant
    return p.returncode == 0


# ----------------------------------------------------------------------------------------------------- runner

def run_test(cmd: str, cwd: Path, timeout: float) -> tuple[bool, str]:
    """(passed, tail of output). A timeout counts as a failure: a mutant that hangs the tests is detected."""
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout + p.stderr)[-300:]
    except subprocess.TimeoutExpired:
        return False, "timeout"


def copy_tree(src: Path, dst: Path) -> None:
    ignore = shutil.ignore_patterns(".git", ".siliconforge", "sim_build", "__pycache__", "*.fst", "*.vcd", "runs", "node_modules")
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="project root (copied for each worker; never modified)")
    ap.add_argument("--file", action="append", required=True, help="source file(s) to mutate, relative to --root")
    ap.add_argument("--test", required=True, help="shell command, run in the project root; exit 0 = tests pass")
    ap.add_argument("--top", default=None, help="top module for the equivalence check (default: skip the check)")
    ap.add_argument("--yosys", default="yosys")
    ap.add_argument("--max", type=int, default=60, help="mutants to evaluate (sampled with --seed)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 2))
    ap.add_argument("--timeout", type=float, default=0, help="seconds per mutant (default: 6x baseline, at least 20)")
    ap.add_argument("--out", default="mutation.json")
    ap.add_argument("--list", action="store_true", help="only list the mutants and exit")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()

    all_mutants: list[Mutant] = []
    for f in a.file:
        all_mutants += enumerate_mutants((root / f).read_text(encoding="utf-8"), f)
    for i, x in enumerate(all_mutants):
        x.id = i
    rng = random.Random(a.seed)
    chosen = all_mutants if len(all_mutants) <= a.max else sorted(rng.sample(all_mutants, a.max), key=lambda x: x.id)
    if a.list:
        for x in chosen:
            print(f"{x.id:4} {x.file}:{x.line}  {x.op}  `{x.original.strip()[:40]}` -> `{x.replacement.strip()[:40]}`")
        return 0

    with tempfile.TemporaryDirectory(prefix="sf_mut_") as tmp:
        tmp_path = Path(tmp)
        base_dir = tmp_path / "baseline"
        copy_tree(root, base_dir)
        t0 = time.time()
        ok, tail = run_test(a.test, base_dir, 3600)
        base_s = time.time() - t0
        if not ok:
            report = {"error": "the test command fails on the unmodified code, so mutation results would be meaningless",
                      "baseline_tail": tail}
            Path(a.out).write_text(json.dumps(report, indent=2))
            print(report["error"], file=sys.stderr)
            return 2
        timeout = a.timeout or max(20.0, base_s * 6)
        have_yosys = shutil.which(a.yosys) is not None
        eq_enabled = bool(a.top) and have_yosys
        # the parse check is only trustworthy if Yosys parses every original file (macros/includes can defeat it)
        syntax_check = have_yosys and all(parses(root / f, a.yosys) for f in a.file)

        def evaluate(job: tuple[int, Mutant]) -> dict:
            idx, mu = job
            work = tmp_path / f"w{idx % a.jobs}"
            lock_dir = work
            if not lock_dir.exists():
                copy_tree(root, work)
            target = work / mu.file
            original = (root / mu.file).read_text(encoding="utf-8")
            mutated_text = apply_mutant(original, mu)
            row = {**asdict(mu), "status": "", "detail": ""}
            row.pop("start")
            row.pop("end")
            target.write_text(mutated_text, encoding="utf-8", newline="\n")
            try:
                if syntax_check and not parses(target, a.yosys):
                    row["status"] = "invalid"
                    return row
                if eq_enabled:
                    verdict = equivalent(root / mu.file, target, a.top, a.yosys)
                    if verdict is True:
                        row["status"] = "equivalent"
                        return row
                passed, tail = run_test(a.test, work, timeout)
                row["status"] = "survived" if passed else "killed"
                row["detail"] = "" if passed else tail.strip().splitlines()[-1][:160] if tail.strip() else ""
            finally:
                target.write_text(original, encoding="utf-8", newline="\n")
            return row

        # one worker per directory: partition mutants by worker slot so two jobs never share a directory
        rows: list[dict] = []
        slots: dict[int, list[tuple[int, Mutant]]] = {}
        for i, mu in enumerate(chosen):
            slots.setdefault(i % a.jobs, []).append((i, mu))

        def run_slot(jobs: list[tuple[int, Mutant]]) -> list[dict]:
            return [evaluate(j) for j in jobs]

        with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for part in pool.map(run_slot, slots.values()):
                rows += part
    rows.sort(key=lambda r: r["id"])
    killed = sum(r["status"] == "killed" for r in rows)
    survived = [r for r in rows if r["status"] == "survived"]
    equiv = sum(r["status"] == "equivalent" for r in rows)
    invalid = sum(r["status"] == "invalid" for r in rows)
    report = {
        "enumerated": len(all_mutants), "evaluated": len(rows), "killed": killed, "survived": len(survived),
        "equivalent": equiv, "invalid": invalid, "equivalence_check": "yosys" if eq_enabled else "skipped",
        "syntax_check": "yosys" if syntax_check else "skipped (invalid mutants may be counted as killed)",
        "score": round(killed / (killed + len(survived)), 4) if killed + len(survived) else None,
        "baseline_seconds": round(base_s, 2), "seed": a.seed, "timeout_s": round(timeout, 1),
        "survivors": [{k: r[k] for k in ("id", "file", "line", "op", "original", "replacement")} for r in survived],
        "mutants": rows,
    }
    Path(a.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    pct = "n/a" if report["score"] is None else f"{report['score'] * 100:.1f}%"
    print(f"mutants: {len(rows)} evaluated, {killed} killed, {len(survived)} survived, {equiv} proven equivalent, "
          f"{invalid} invalid  "
          f"score {pct}")
    for r in survived[:15]:
        print(f"  survived {r['file']}:{r['line']}  {r['op']}  `{r['original'].strip()[:30]}` -> `{r['replacement'].strip()[:30]}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
