#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Export a finished run's results as a committed benchmark artifact (benchmarks/results/<name>.json).

Usage: python scripts/export_run.py <run_id> <name>
Keeps only what charts and tables need: provenance, formal history, LEC, per-episode PPA and signoff checks.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    run_id, name = sys.argv[1], sys.argv[2]
    report = json.loads((ROOT / "runs" / run_id / "signoff_report.json").read_text(encoding="utf-8"))
    st = report["state"]
    out = {
        "run_id": run_id,
        "exported": time.strftime("%Y-%m-%d"),
        "eda_mode": report["eda_mode"],
        "module": st["contract"]["module_name"] if st.get("contract") else None,
        "stage": st["stage"],
        "signoff_passed": st["signoff_passed"],
        "error": st.get("error_message"),
        "elapsed_sec": report["elapsed_sec"],
        "cost": {k: report["cost"].get(k) for k in ("total_cost_usd", "total_tokens")},
        "formal": [
            {"mode": f["mode"], "task_status": f["task_status"], "elapsed_sec": f["elapsed_sec"],
             "witnesses": [{"property_id": w["property_id"], "cycle": w["cycle"], "failing_signal": w["failing_signal"],
                            "stimulus": w["stimulus"]} for w in f["witnesses"]]}
            for f in st["formal_history"]
        ],
        "lec": st.get("lec_report") and {k: st["lec_report"][k] for k in ("passed", "mode", "cell_count", "elapsed_sec")},
        "episodes": [
            {k: e[k] for k in ("wns_ns", "tns_ns", "area_um2", "total_power_mw", "cell_count", "drc_violations",
                               "lvs_clean", "runtime_sec", "knobs", "reward", "mode", "corner_wns", "cached")}
            for e in st["ppa_episodes"]
        ],
        "best_index": next((i for i, e in enumerate(st["ppa_episodes"])
                            if st["best_ppa"] and e["reward"] == st["best_ppa"]["reward"]
                            and e["area_um2"] == st["best_ppa"]["area_um2"]), None),
        "signoff_checks": st.get("signoff_checks", []),
    }
    dest = ROOT / "benchmarks" / "results" / f"{name}.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
