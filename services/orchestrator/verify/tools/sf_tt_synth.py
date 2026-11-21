#!/usr/bin/env python3
"""Synthesize a design with Yosys against a real PDK liberty file and report cells and area. Standard library only.

    sf_tt_synth.py --sources rtl/top.v rtl/core.v --top tt_um_example [--include src] [--pdk-root ~/pdk]
                   [--pdk ihp-sg13cmos5l] [--corner typ_1p20V_25C] [--out .siliconforge/tt]

Writes `<out>/synth.json` (cells, area, flops, latches, per-cell counts, warnings), `<out>/synth.log` and the mapped netlist
`<out>/netlist.v`, which gate-level simulation can use. This is *post-synthesis* area, before placement, clock tree,
hold buffers and routing: use it to size a design against the tile budget, not as a signoff number.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CELL_ROW = re.compile(r"^\s*(\d+)\s+([0-9.]+(?:[Ee][+-]?\d+)?)\s+(\S+)\s*$")
FLOP = re.compile(r"_(s?df\w*)_\d+$")
LATCH = re.compile(r"_(dl[hl]\w*)_\d+$")


def find_liberty(pdk_root: Path, pdk: str, corner: str) -> Path:
    family = pdk.replace("ihp-", "")
    lib = pdk_root / pdk / "libs.ref" / f"{family}_stdcell" / "lib" / f"{family}_stdcell_{corner}.lib"
    if not lib.exists():
        raise SystemExit(f"liberty not found: {lib} (install the PDK with ciel, or pass --pdk-root / --corner)")
    return lib


def parse_stat(text: str) -> dict:
    cells: dict[str, dict] = {}
    area = None
    for line in text.splitlines():
        m = CELL_ROW.match(line)
        if m and not line.strip().startswith(("wires", "ports")) and "_" in m.group(3):
            cells[m.group(3)] = {"count": int(m.group(1)), "area": float(m.group(2))}
        a = re.search(r"Chip area for (?:top )?module '\\?([^']+)': ([0-9.]+)", line)
        if a:
            area = float(a.group(2))
    total = sum(c["count"] for c in cells.values())
    flops = sum(c["count"] for n, c in cells.items() if FLOP.search(n))
    latches = sum(c["count"] for n, c in cells.items() if LATCH.search(n))
    seq_area = sum(c["area"] for n, c in cells.items() if FLOP.search(n) or LATCH.search(n))
    if area is None and total == 0:
        area = 0.0  # Yosys prints no area line for a design with no cells (e.g. outputs wired straight to inputs)
    return {"cells": total, "area_um2": area, "flops": flops, "latches": latches,
            "sequential_area_um2": round(seq_area, 2), "by_cell": cells}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", nargs="+", required=True)
    ap.add_argument("--top", required=True)
    ap.add_argument("--include", action="append", default=[])
    ap.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT") or str(Path.home() / "pdk"))
    ap.add_argument("--pdk", default="ihp-sg13cmos5l")
    ap.add_argument("--corner", default="typ_1p20V_25C")
    ap.add_argument("--yosys", default="yosys")
    ap.add_argument("--out", default=".siliconforge/tt")
    a = ap.parse_args(argv)

    lib = find_liberty(Path(os.path.expanduser(a.pdk_root)), a.pdk, a.corner)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    incs = " ".join(f"-I{i}" for i in a.include)
    script = "\n".join([
        f"read_verilog -sv {incs} {' '.join(a.sources)}",
        f"synth -top {a.top} -flatten",
        f"dfflibmap -liberty {lib}",
        f"abc -liberty {lib}",
        "opt_clean -purge",
        f"tee -o {out / 'stat.txt'} stat -liberty {lib}",
        f"write_verilog -noattr -noexpr {out / 'netlist.v'}",
    ])
    (out / "synth.ys").write_text(script + "\n", encoding="utf-8")
    t0 = time.time()
    proc = subprocess.run([a.yosys, "-s", str(out / "synth.ys")], capture_output=True, text=True)
    log = proc.stdout + proc.stderr
    (out / "synth.log").write_text(log, encoding="utf-8")
    if proc.returncode != 0:
        tail = [ln for ln in log.splitlines() if "ERROR" in ln][:5] or log.splitlines()[-5:]
        report = {"ok": False, "error": "yosys failed", "detail": tail}
        (out / "synth.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("yosys failed:", *tail, sep="\n  ")
        return 1
    stats = parse_stat((out / "stat.txt").read_text(encoding="utf-8"))
    warnings = sorted({ln.split("Warning:", 1)[1].strip()[:200] for ln in log.splitlines() if "Warning:" in ln})
    report = {"ok": True, "top": a.top, "pdk": a.pdk, "corner": a.corner, "liberty": str(lib),
              "seconds": round(time.time() - t0, 2), "warnings": warnings[:25],
              "netlist": str(out / "netlist.v"), **stats,
              "note": "post-synthesis (no placement, clock tree, hold fixing or routing)"}
    (out / "synth.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    area = report["area_um2"]
    print(f"{a.top}: {report['cells']} cells, {area:.0f} um2 ({report['flops']} flops, {report['latches']} latches; "
          f"{100 * report['sequential_area_um2'] / area:.0f}% of area sequential)" if area else "synthesized (no area reported)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
