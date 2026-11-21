#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Toggle coverage from a waveform dump: which signal bits never moved in both directions?

    sf_toggle.py DUMP.vcd|DUMP.fst [--scope tb.dut] [--exclude clk --exclude rst_n] [--out toggle.json]

A bit is covered when the simulation saw it go 0->1 and 1->0. Transitions to or from x/z do not count, so an initial
x->0 never inflates the result. A bit that never toggles is either logic the tests never exercise or logic that cannot
move (a constant), and the report lists both kinds by name. FST dumps are streamed through `fst2vcd`. Standard library only.

This is *toggle* coverage. It does not measure line or branch coverage, and the report says so.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import subprocess
import sys
from pathlib import Path


def iter_lines(path: Path):
    if path.suffix == ".fst":
        exe = shutil.which("fst2vcd")
        if exe is None:
            raise SystemExit("fst2vcd is needed to read FST dumps; dump VCD instead or install gtkwave's tools")
        proc = subprocess.Popen([exe, str(path)], stdout=subprocess.PIPE, text=True, errors="replace")
        assert proc.stdout is not None
        yield from proc.stdout
        proc.wait()
    else:
        with path.open(encoding="utf-8", errors="replace") as f:
            yield from f


def analyze(lines, scope: str = "", excludes: tuple[str, ...] = ()) -> dict:
    stack: list[str] = []
    ids: dict[str, dict] = {}  # id -> {name, width}
    rise: dict[str, int] = {}
    fall: dict[str, int] = {}
    last: dict[str, str] = {}
    in_defs = True
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if in_defs:
            if line.startswith("$scope"):
                stack.append(line.split()[2])
            elif line.startswith("$upscope"):
                if stack:
                    stack.pop()
            elif line.startswith("$var"):
                p = line.split()
                width, ident, name = int(p[2]), p[3], p[4]
                full = ".".join(stack + [name])
                if ident in ids:
                    continue  # an alias of a signal already counted
                if scope and not (full == scope or full.startswith(scope + ".")):
                    continue
                if any(fnmatch.fnmatch(name, x) or fnmatch.fnmatch(full, x) for x in excludes):
                    continue
                ids[ident] = {"name": full, "width": width}
                rise[ident] = fall[ident] = 0
            elif line.startswith("$enddefinitions"):
                in_defs = False
            continue
        c = line[0]
        if c in "#$":
            continue
        if c in "bB":
            val, _, ident = line[1:].partition(" ")
            ident = ident.strip()
        elif c in "01xXzZ":
            val, ident = c, line[1:]
        else:
            continue
        if ident not in ids:
            continue
        width = ids[ident]["width"]
        val = val.lower()
        if len(val) < width and val[0] in "01":
            val = val.rjust(width, "0")  # VCD drops leading zeros of vectors
        prev = last.get(ident)
        last[ident] = val
        if prev is None or len(prev) != len(val):
            continue
        for bit in range(width):
            a, b = prev[width - 1 - bit], val[width - 1 - bit]
            if a == "0" and b == "1":
                rise[ident] |= 1 << bit
            elif a == "1" and b == "0":
                fall[ident] |= 1 << bit
    signals, total, covered = [], 0, 0
    never = []
    for ident, meta in ids.items():
        w = meta["width"]
        both = rise[ident] & fall[ident]
        n_cov = bin(both).count("1")
        total += w
        covered += n_cov
        signals.append({"name": meta["name"], "width": w, "covered_bits": n_cov,
                        "rise_only": bin(rise[ident] & ~fall[ident]).count("1"),
                        "fall_only": bin(fall[ident] & ~rise[ident]).count("1")})
        for bit in range(w):
            if not (both >> bit) & 1:
                never.append(f"{meta['name']}[{bit}]" if w > 1 else meta["name"])
    return {
        "kind": "toggle (0->1 and 1->0 per bit); not line or branch coverage",
        "signals": len(signals), "bits": total, "covered_bits": covered,
        "coverage": round(covered / total, 4) if total else None,
        "not_toggled": never[:400], "not_toggled_count": len(never), "per_signal": signals,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump")
    ap.add_argument("--scope", default="", help="hierarchical prefix to analyse, e.g. tb.user_project")
    ap.add_argument("--exclude", action="append", default=[], help="glob of signal names to skip")
    ap.add_argument("--out", default="toggle.json")
    a = ap.parse_args(argv)
    rep = analyze(iter_lines(Path(a.dump)), a.scope, tuple(a.exclude))
    Path(a.out).write_text(json.dumps(rep, indent=2), encoding="utf-8")
    pct = "n/a" if rep["coverage"] is None else f"{rep['coverage'] * 100:.1f}%"
    print(f"toggle coverage: {rep['covered_bits']}/{rep['bits']} bits ({pct}) over {rep['signals']} signals")
    for n in rep["not_toggled"][:12]:
        print(f"  never toggled both ways: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
