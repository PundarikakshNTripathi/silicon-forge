#!/usr/bin/env python3
"""Run every bundled example (good RTL must pass, bug RTL must be caught) through a real EDA backend.

Usage: python scripts/verify_examples.py [--backend wsl|native|docker] [--lec]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.orchestrator.config import load_flow_config  # noqa: E402
from services.orchestrator.eda.bridge import EDABridge  # noqa: E402
from services.orchestrator.schemas import PROPERTY_LIST, ContractModel  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="wsl")
    ap.add_argument("--lec", action="store_true")
    args = ap.parse_args()
    cfg = load_flow_config()
    cfg.eda_mode = args.backend
    bridge = EDABridge(cfg)
    bad = 0
    for ex in sorted((ROOT / "hardware" / "examples").iterdir()):
        fx = ex / "fixtures"
        if not (fx / "contract.json").exists():
            continue
        contract = ContractModel.model_validate_json((fx / "contract.json").read_text()).to_state()
        props = [p.model_dump() for p in PROPERTY_LIST.validate_python(json.loads((fx / "props.json").read_text()))]
        good = next((ex / "rtl").glob("*.sv")).read_text()
        for label, rtl, want_pass in (("good", good, True), ("bug", (fx / "rtl_bug.sv").read_text(), False)):
            with tempfile.TemporaryDirectory(prefix="sf_ex_") as tmp:
                rep, _ = bridge.run_formal_verification(Path(tmp), contract, rtl, props)
                ok = rep.passed == want_pass
                extra = ""
                if args.lec and want_pass:
                    lec = bridge.run_synthesis_and_lec(Path(tmp), contract, rtl)
                    ok = ok and lec.passed
                    extra = f" LEC={'pass' if lec.passed else 'FAIL'}({lec.cell_count} cells)"
                w = rep.witnesses[0].property_id if rep.witnesses else "-"
                print(f"{'OK ' if ok else 'BAD'} {ex.name:18s} {label:4s} {rep.task_status} witness={w} {rep.elapsed_sec}s{extra}")
                bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
