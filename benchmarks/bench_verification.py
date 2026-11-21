#!/usr/bin/env python3
"""Measure the verification toolkit on the pio16 worked example and write a committed result artifact.

    python benchmarks/bench_verification.py [--backend wsl] [--seeds 8] [--mutants 80]

Everything here is a real tool run (Icarus + cocotb, Yosys with the IHP liberty, SymbiYosys); the JSON records the tool
versions, the backend that ran them, and every number that the README or docs quote. Nothing is estimated.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from services.orchestrator import isa, tt, verify
from services.orchestrator.config import load_flow_config
from services.orchestrator.eda.bridge import EDABridge

ROOT = Path(__file__).resolve().parents[1]
PIO16 = ROOT / "hardware" / "isa" / "pio16"


def tool_versions(runner) -> dict[str, str]:
    out = {}
    for tool, args in (("yosys", ["-V"]), ("iverilog", ["-V"])):
        res = runner.run([tool, *args], Path.cwd(), timeout=60)
        first = (res.stdout or res.stderr).strip().splitlines()
        out[tool] = first[0][:90] if first else "unknown"
    res = runner.run(["python3", "-c", "import cocotb; print('cocotb', cocotb.__version__)"], Path.cwd(), timeout=60)
    out["cocotb"] = res.stdout.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="wsl")
    ap.add_argument("--seeds", type=int, default=8, help="random programs per cocotb run")
    ap.add_argument("--mutants", type=int, default=80)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    cfg = load_flow_config()
    cfg.eda_mode = a.backend
    runner = EDABridge(cfg).backend.runner
    work = Path(tempfile.mkdtemp(prefix="bench_verif_")) / "pio16"
    shutil.copytree(PIO16, work, ignore=shutil.ignore_patterns("coverage.json", ".siliconforge", "sim_build", "__pycache__"))
    verify.install_tools(work)
    native = getattr(runner, "to_wsl_path", str)
    result: dict = {"date": time.strftime("%Y-%m-%d"), "backend": runner.name, "design": "hardware/isa/pio16",
                    "tools": tool_versions(runner)}

    # 1. ISA toolchain: exhaustive decode, timing proof
    spec = isa.load_spec(PIO16 / "isa.yaml")
    prog = isa.Assembler(spec).assemble((PIO16 / "uart_tx.asm").read_text(encoding="utf-8"))
    rep = isa.check_timing(prog)
    exact = [g for g in rep.static if g.exact and g.min_cycles == 16]
    result["isa"] = {"instructions": len(spec.instructions), "words_checked_for_unique_decode": 1 << spec.word_bits,
                     "uart_program_words": len(prog.image()), "uart_bit_gaps_proven_exact_16_cycles": len(exact),
                     "timing_ok": rep.ok}

    # 2. lockstep regression with coverage
    cov = native(work / "coverage.json")
    t0 = time.time()
    sim = verify.run_cocotb(runner, work, sources=["gen/rtl/pio16_decode.v", "rtl/pio16.v"], top="pio16",
                            test_module="test_pio16", test_dir="test", seeds=(1, 2, 3), includes=("gen/rtl",),
                            env={"SF_SEEDS": str(a.seeds), "SF_TOOLS": ".siliconforge/tools", "SF_COVERAGE": cov})
    coverage = json.loads((work / "coverage.json").read_text(encoding="utf-8"))
    result["lockstep"] = {"ok": sim["ok"], "tests_passed": sim["passed"], "tests_failed": sim["failed"],
                          "seeds": sim["seeds"], "random_programs_per_seed": a.seeds, "seconds": round(time.time() - t0, 1),
                          "functional_coverage": coverage["coverage"],
                          "uncovered_bins": {k: v["missing"] for k, v in {**coverage["points"], **coverage["crosses"]}.items() if v["missing"]}}

    # 3. mutation testing of that suite
    cmd = ("python3 .siliconforge/tools/sf_cocotb_run.py --sources gen/rtl/pio16_decode.v rtl/pio16.v --top pio16 "
           "--test-module test_pio16 --test-dir test --include gen/rtl --seeds 1 --env SF_SEEDS=10 "
           "--env SF_TOOLS=.siliconforge/tools --env SF_COVERAGE=cov.json --out sim.json --build-dir sim_build")
    t0 = time.time()
    mut = verify.mutation_test(runner, work, ["rtl/pio16.v"], cmd, top="pio16", max_mutants=a.mutants, seed=1, jobs=4,
                               includes=("gen/rtl",), support=("gen/rtl/pio16_decode.v",))
    result["mutation"] = {k: mut[k] for k in ("score", "killed", "survived", "equivalent", "invalid", "evaluated",
                                              "enumerated", "equivalence_check", "syntax_check", "seed")}
    result["mutation"]["seconds"] = round(time.time() - t0, 1)
    result["mutation"]["survivors"] = [{k: s[k] for k in ("line", "op", "original", "replacement")} for s in mut["survivors"]]

    # 4. real synthesis against the IHP liberty
    res = verify.run_tool(runner, work, "sf_tt_synth.py",
                          ["--top", "pio16", "--out", ".siliconforge/tt", "--include", "gen/rtl",
                           "--sources", "gen/rtl/pio16_decode.v", "rtl/pio16.v"], 600)
    synth = json.loads((work / ".siliconforge" / "tt" / "synth.json").read_text(encoding="utf-8"))
    if not synth.get("ok"):
        raise SystemExit(f"synthesis failed: {res.log[-400:]}")
    b = tt.budget(synth["area_um2"])
    result["synthesis"] = {"pdk": synth["pdk"], "corner": synth["corner"], "cells": synth["cells"],
                           "area_um2": round(synth["area_um2"], 1), "flops": synth["flops"], "latches": synth["latches"],
                           "sequential_share": round(synth["sequential_area_um2"] / synth["area_um2"], 3),
                           "smallest_tile_that_fits": b["smallest_that_fits"], "margin": b["margin"],
                           "note": synth["note"]}

    out = Path(a.out) if a.out else ROOT / "benchmarks" / "results" / f"verification-pio16-{result['date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("lockstep", "mutation", "synthesis")}, indent=2)[:1800])
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
