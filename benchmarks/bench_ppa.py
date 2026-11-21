#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compare place-and-route knob search strategies on real flow runs, at the same evaluation budget.

Arms: `default` (LibreLane's defaults, one run), `random`, `lhs` (Latin hypercube), `bandit` (the original recipe bandit) and
`gpbo` (Gaussian-process Bayesian optimization). Every arm is scored by the same quality reward (no runtime term).

The objective is built from a probe, so that it can tell strategies apart instead of saturating: the design is first run with
default knobs at a relaxed clock to measure its critical path; the target clock is then 10% above that path, and the area and
power budgets are what the default run used. A strategy earns reward by saving area and power while still meeting timing.

Usage:
    python benchmarks/bench_ppa.py --design up_down_counter --budget 8 --seeds 4 --backend wsl
Writes benchmarks/results/ppa-<design>-<date>.json (best-so-far curves per arm and seed, every evaluated point).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.orchestrator.config import load_flow_config  # noqa: E402
from services.orchestrator.eda.bridge import EDABridge  # noqa: E402
from services.orchestrator.rl.bo import (  # noqa: E402
    DEFAULT_SPACE,
    GPBayesOptimizer,
    latin_hypercube,
    random_points,
)
from services.orchestrator.rl.optimizer import HybridPpaOptimizer  # noqa: E402
from services.orchestrator.rl.ppa_env import PpaOptimizationEnv  # noqa: E402
from services.orchestrator.schemas import ContractModel  # noqa: E402

SCHEMA = "siliconforge.ppa/1"


def load_design(name: str):
    ex = ROOT / "hardware" / "examples" / name
    contract = ContractModel(**json.loads((ex / "fixtures" / "contract.json").read_text(encoding="utf-8"))).to_state()
    rtl = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ex / "rtl").glob("*.sv")))
    return contract, rtl


def point(m, knobs) -> dict:
    return {"knobs": knobs, "reward": m.reward, "wns_ns": m.wns_ns, "area_um2": m.area_um2, "power_mw": m.total_power_mw,
            "drc": m.drc_violations, "lvs_clean": m.lvs_clean, "cached": m.cached,
            "flow_failed": bool(m.extra.get("flow_failed"))}


def curve(rewards: list[float]) -> list[float]:
    best, out = float("-inf"), []
    for r in rewards:
        best = max(best, r)
        out.append(round(best, 5))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", default="up_down_counter")
    ap.add_argument("--backend", default="wsl")
    ap.add_argument("--budget", type=int, default=8, help="flow evaluations per run")
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--arms", default="random,lhs,bandit,gpbo")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    cfg = load_flow_config()
    cfg.eda_mode = args.backend
    contract, rtl = load_design(args.design)
    bridge = EDABridge(cfg)
    cache = Path(tempfile.gettempdir()) / "sf_ppa_bench_cache" / args.design
    out_dir = Path(tempfile.mkdtemp(prefix="sf_ppa_"))
    weights = cfg.rl_optimizer.get("reward_weights", {})

    def make_env(targets: dict, tag: str) -> PpaOptimizationEnv:
        d = out_dir / tag
        d.mkdir(parents=True, exist_ok=True)
        return PpaOptimizationEnv(bridge, d, contract, targets, weights, rtl_source=rtl, cache_dir=cache, quality_only=True)

    # --- probe: default knobs at a relaxed clock give the critical path and the default area and power
    relaxed = {"clock_period_ns": 20.0, "max_area_um2": 1e9, "max_power_mw": 1e3, "min_wns_ns": 0.0}
    t0 = time.time()
    m0, _ = make_env(relaxed, "probe").evaluate_knobs({"CLOCK_PERIOD": 20.0}, 1)
    crit = 20.0 - m0.wns_ns
    clock = round(max(1.0, crit * 1.10), 1)
    targets = {"clock_period_ns": clock, "max_area_um2": m0.area_um2, "max_power_mw": m0.total_power_mw, "min_wns_ns": 0.0}
    print(f"probe: critical path {crit:.2f} ns -> target clock {clock} ns; default area {m0.area_um2:.0f} um2, "
          f"power {m0.total_power_mw:.3f} mW ({time.time() - t0:.0f} s)", flush=True)

    env_default = make_env(targets, "default")
    md, rd = env_default.evaluate_knobs({"CLOCK_PERIOD": clock}, 1)
    results: dict[str, dict] = {"default": {"runs": [{"seed": 0, "rewards": [rd], "best_curve": [rd], "points": [point(md, {})]}]}}
    print(f"default knobs at the target clock: reward {rd:.4f}, WNS {md.wns_ns:.3f} ns", flush=True)

    for arm in [a for a in args.arms.split(",") if a]:
        runs = []
        seeds = [0] if arm == "bandit" else list(range(args.seeds))  # the bandit has no randomness
        for seed in seeds:
            env = make_env(targets, f"{arm}-{seed}")
            pts, rewards = [], []

            def evaluate(knobs, _env=env, _pts=pts, _rewards=rewards):
                m, r = _env.evaluate_knobs(knobs, _env.next_episode())
                _pts.append(point(m, knobs))
                _rewards.append(r)
                return r, bool(m.extra.get("flow_failed"))

            if arm == "random":
                for k in random_points(DEFAULT_SPACE, args.budget, seed):
                    evaluate(k)
            elif arm == "lhs":
                for k in latin_hypercube(DEFAULT_SPACE, args.budget, seed):
                    evaluate(k)
            elif arm == "gpbo":
                opt = GPBayesOptimizer(DEFAULT_SPACE, seed=seed, n_init=3)
                for _ in range(args.budget):
                    k = opt.suggest()
                    reward, failed = evaluate(k)
                    opt.observe(k, reward, failed)
            elif arm == "bandit":
                bandit = HybridPpaOptimizer(num_actions=env.num_actions)
                episodes, _, _ = bandit.optimize(env, max_episodes=args.budget)
                for m in episodes:
                    pts.append(point(m, m.knobs))
                    rewards.append(m.reward)
            else:
                raise SystemExit(f"unknown arm {arm}")
            runs.append({"seed": seed, "rewards": [round(r, 5) for r in rewards], "best_curve": curve(rewards), "points": pts})
            print(f"{arm:<7} seed {seed}: best {max(rewards):.4f} after {len(rewards)} evaluations "
                  f"({sum(1 for p in pts if p['cached'])} from cache)", flush=True)
        results[arm] = {"runs": runs}

    summary = {}
    for arm, v in results.items():
        finals = [r["best_curve"][-1] for r in v["runs"]]
        fails = [sum(1 for p in r["points"] if p.get("flow_failed")) for r in v["runs"]]
        summary[arm] = {"runs": len(finals), "best_mean": round(sum(finals) / len(finals), 5), "best_min": min(finals), "best_max": max(finals),
                        "failed_flows_per_run": round(sum(fails) / len(fails), 2)}
    flow_version = bridge.backend.check_tools() if hasattr(bridge.backend, "check_tools") else {}
    doc = {
        "schema": SCHEMA, "design": args.design, "backend": bridge.mode, "pdk": cfg.pdk, "budget": args.budget, "seeds": args.seeds,
        "objective": {"targets": targets, "probe": {"critical_path_ns": round(crit, 3), "default_area_um2": m0.area_um2,
                                                    "default_power_mw": m0.total_power_mw},
                      "note": "reward = signoff-aware quality score without the runtime term; area and power are relative to the default run"},
        "space": [{"name": d.name, "kind": d.kind, "low": d.low, "high": d.high, "choices": list(d.choices)} for d in DEFAULT_SPACE.dims],
        "tools_present": flow_version, "summary": summary, "arms": results, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        # no local paths in a committed result: drop the layout locations
        "default_metrics": asdict(md) | {"run_dir": None, "gds_path": None,
                                         "extra": {k: v for k, v in md.extra.items() if k != "render_png"}},
    }
    out = ROOT / "benchmarks" / "results" / f"ppa-{args.design}{('-' + args.tag) if args.tag else ''}-{time.strftime('%Y-%m-%d')}.json"
    out.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
