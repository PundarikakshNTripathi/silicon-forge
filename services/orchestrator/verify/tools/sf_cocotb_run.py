#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run a cocotb test module against Verilog sources with Icarus, for one or many seeds, and write JSON results.

Uses cocotb's own Python runner, so it needs neither `make` nor a shell:

    sf_cocotb_run.py --sources rtl/core.v test/tb.v --top tb --test-module test --test-dir test
                     [--seeds 1,2,3] [--define GL_TEST=1] [--include src] [--waves] [--build-dir sim_build/rtl]
                     [--testcase name] [--out sim.json]

Exit status is 0 only if at least one test ran and every test passed for every seed. Each result records the seed, so a
failure can be replayed exactly (`--seeds N`). Standard library plus cocotb; it runs wherever cocotb and Icarus are.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_results(xml_path: Path) -> list[dict]:
    """cocotb writes JUnit-style XML; return [{name, status, time, message}]."""
    out = []
    for tc in ET.parse(xml_path).getroot().iter("testcase"):
        bad = next((c for c in tc if c.tag in ("failure", "error")), None)
        skipped = any(c.tag == "skipped" for c in tc)
        out.append({
            "name": f"{tc.get('classname', '')}.{tc.get('name', '')}".strip("."),
            "status": "fail" if bad is not None else "skip" if skipped else "pass",
            "sim_time_ns": float(tc.get("sim_time_ns", 0) or 0),
            "real_s": float(tc.get("time", 0) or 0),
            # JUnit uses `message`; cocotb 2.x writes the text in `error_msg` and the exception class in `error_type`
            "message": (bad.get("message") or bad.get("error_msg") or (bad.text or "")).strip()[:500] if bad is not None else "",
            "error_type": (bad.get("type") or bad.get("error_type") or "") if bad is not None else "",
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", nargs="+", required=True)
    ap.add_argument("--top", required=True, help="HDL top level (usually the testbench module)")
    ap.add_argument("--test-module", required=True, help="Python module with the cocotb tests (no .py)")
    ap.add_argument("--test-dir", default=".", help="directory holding the test module")
    ap.add_argument("--seeds", default="1", help="comma-separated seeds")
    ap.add_argument("--define", action="append", default=[], metavar="NAME[=VALUE]")
    ap.add_argument("--include", action="append", default=[])
    ap.add_argument("--build-arg", action="append", default=[])
    ap.add_argument("--build-dir", default="sim_build")
    ap.add_argument("--timescale", default="1ns/1ps")
    ap.add_argument("--testcase", default=None)
    ap.add_argument("--waves", action="store_true")
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="environment for the tests")
    ap.add_argument("--out", default="sim.json")
    a = ap.parse_args(argv)

    try:
        from cocotb_tools.runner import get_runner
    except ImportError:
        print("cocotb 2.x (cocotb_tools.runner) is not installed in this Python", file=sys.stderr)
        Path(a.out).write_text(json.dumps({"error": "cocotb runner not available"}))
        return 2

    here = Path.cwd()
    if not os.environ.get("PDK_ROOT"):  # unset or empty: default to ~/pdk so sources can say $PDK_ROOT/<pdk>/libs.ref/...
        os.environ["PDK_ROOT"] = str(Path.home() / "pdk")

    def expand(p: str) -> Path:
        return (here / os.path.expandvars(os.path.expanduser(p))).resolve()

    defines = {d.split("=", 1)[0]: (d.split("=", 1)[1] if "=" in d else "1") for d in a.define}
    unit, _, prec = a.timescale.partition("/")
    ts = (unit, prec or unit)
    runner = get_runner("icarus")
    build_dir = (here / a.build_dir).resolve()
    t_build = time.time()
    runner.build(sources=[expand(s) for s in a.sources], hdl_toplevel=a.top, build_dir=build_dir,
                 always=True, timescale=ts, waves=a.waves, defines=defines,
                 includes=[expand(i) for i in a.include], build_args=list(a.build_arg))
    build_s = round(time.time() - t_build, 2)
    extra_env = dict(kv.split("=", 1) for kv in a.env)
    runs = []
    for seed in (int(s) for s in a.seeds.split(",") if s.strip()):
        xml = build_dir / f"results_{seed}.xml"
        t = time.time()
        try:
            runner.test(hdl_toplevel=a.top, test_module=a.test_module, test_dir=(here / a.test_dir).resolve(),
                        build_dir=build_dir, seed=seed, waves=a.waves, results_xml=xml, testcase=a.testcase,
                        extra_env=extra_env, timescale=ts)
            crashed = ""
        except Exception as exc:  # noqa: BLE001 - cocotb raises when a test fails; the XML still has the details
            crashed = f"{type(exc).__name__}: {exc}"[:300]
        tests = parse_results(xml) if xml.exists() else []
        runs.append({"seed": seed, "tests": tests, "elapsed_s": round(time.time() - t, 2), "crashed": crashed})
    flat = [t for r in runs for t in r["tests"]]
    passed = sum(t["status"] == "pass" for t in flat)
    failed = sum(t["status"] == "fail" for t in flat)
    ok = bool(flat) and failed == 0 and not any(r["crashed"] and not r["tests"] for r in runs)
    waves = sorted(str(p.relative_to(here)) for p in list(build_dir.rglob("*.fst")) + list(build_dir.rglob("*.vcd"))) \
        + sorted(str(p.relative_to(here)) for p in here.glob("*.fst")) + sorted(str(p.relative_to(here)) for p in here.glob("*.vcd"))
    report = {"ok": ok, "passed": passed, "failed": failed, "seeds": [r["seed"] for r in runs], "build_s": build_s,
              "runs": runs, "waves": waves[:8], "toplevel": a.top, "test_module": a.test_module}
    Path(a.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"cocotb: {passed} passed, {failed} failed over seeds {report['seeds']}  -> {'OK' if ok else 'FAILED'}")
    for t in flat:
        if t["status"] == "fail":
            print(f"  FAIL {t['name']}: {t['message'][:200]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
