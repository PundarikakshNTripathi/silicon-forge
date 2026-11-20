#!/usr/bin/env python3
"""Measure the C++ sf-telemetry engine against the pure-Python fallbacks (no claims without numbers).

Usage: python benchmarks/bench_telemetry.py [--iters 200]
Writes benchmarks/results/telemetry-<date>.json
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.orchestrator.config import load_flow_config  # noqa: E402

LOG = (ROOT / "tests" / "fixtures" / "sby_axi_bug.log").read_text(encoding="utf-8")  # ~13 KB real tool log
PPA_LOG = "\n".join([
    "      903  8.7E+03 cells", "   Chip area for module '\\axi_lite_alu': 8698.342400",
    "Writing metric timing__setup__wns__corner:nom_ss_100C_1v60: -2.74", "wns -0.18", "tns -1.42",
    "Total 0.0012 0.0008 0.0001 0.0021 100.0%", "Total DRC errors: 0", "Circuits match uniquely.",
]) * 40
RTL = (ROOT / "hardware" / "examples" / "axi_lite_alu" / "rtl" / "axi_lite_alu.sv").read_text(encoding="utf-8")


def py_parse(text: str) -> dict:
    def find(p: str, d: float) -> float:
        m = re.search(p, text, re.IGNORECASE)
        return float(m.group(1)) if m else d
    return {"wns": find(r"wns\s+(-?[0-9.]+)", 0.0), "tns": find(r"tns\s+(-?[0-9.]+)", 0.0),
            "cells": find(r"Number of cells:\s*([0-9]+)", 0.0), "drc": find(r"Total DRC errors:\s*([0-9]+)", 0.0)}


def py_lint(text: str) -> int:
    n = 0
    for line in text.splitlines():
        code = line.split("//", 1)[0]
        n += bool(re.search(r"#[0-9]+", code)) + bool(re.search(r"\binitial\b", code))
    return n


def timeit(fn, iters: int) -> dict:
    samples = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    return {"median_ms": round(statistics.median(samples), 4), "p95_ms": round(samples[int(0.95 * len(samples)) - 1], 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=200)
    args = ap.parse_args()
    binary = load_flow_config().telemetry_binary
    if not binary.exists():
        print("sf-telemetry not built: run `python scripts/dev.py build-cpp`")
        return 1

    def cpp(mode: str, text: str):
        return lambda: subprocess.run([str(binary), "--mode", mode, "--input", "-"], input=text, text=True,
                                      capture_output=True, check=False)

    results = {
        "platform": platform.platform(),
        "iters": args.iters,
        "cpp_subprocess_parse_logs": timeit(cpp("parse-logs", PPA_LOG), args.iters),
        "python_inprocess_parse_logs": timeit(lambda: py_parse(PPA_LOG), args.iters),
        "cpp_subprocess_lint_rtl": timeit(cpp("lint-rtl", RTL), args.iters),
        "python_inprocess_lint_rtl": timeit(lambda: py_lint(RTL), args.iters),
        "note": "C++ numbers include process spawn (how the pipeline calls it); Python numbers are in-process regex "
                "with FAR fewer rules, so they are a lower bound on equivalent Python work.",
    }
    out = ROOT / "benchmarks" / "results" / f"telemetry-{time.strftime('%Y-%m-%d')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
