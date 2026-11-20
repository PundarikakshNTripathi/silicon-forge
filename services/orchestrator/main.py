"""SiliconForge CLI.

    siliconforge run    --spec hardware/examples/axi_lite_alu/spec.md --backend wsl
    siliconforge doctor
    siliconforge eval   --backend simulated
    siliconforge serve                      # HTTP API + web UI (added with the API layer)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from services.orchestrator.config import load_flow_config
from services.orchestrator.eda.bridge import MODES, EDABridge, normalize_mode
from services.orchestrator.inference.providers import ReplayProvider
from services.orchestrator.pipeline import SiliconForgePipeline
from services.orchestrator.state import FlowState


def _build_pipeline(args: argparse.Namespace) -> SiliconForgePipeline:
    cfg = load_flow_config(args.config)
    if getattr(args, "backend", None):
        cfg.eda_mode = normalize_mode(args.backend)
    profile = getattr(args, "profile", None) or getattr(args, "provider", None)
    if profile:
        cfg.inference.profile = "replay" if profile == "mock" else profile
    provider = None
    if cfg.inference.profile == "replay":
        provider = ReplayProvider(inject_initial_bug=getattr(args, "inject_bug", False))
    return SiliconForgePipeline(cfg, provider=provider, eda=EDABridge(cfg))


def _summary(state: FlowState, pipeline: SiliconForgePipeline) -> dict:
    best = state.best_ppa
    return {
        "run_id": state.run_id,
        "module": state.contract.module_name if state.contract else None,
        "eda_mode": state.eda_mode,
        "stage": state.stage.value,
        "signoff_passed": state.signoff_passed,
        "lec_passed": state.lec_passed,
        "formal_iterations": len(state.formal_history),
        "error": state.error_message,
        "best_ppa": {
            "wns_ns": best.wns_ns, "tns_ns": best.tns_ns, "area_um2": best.area_um2,
            "total_power_mw": best.total_power_mw, "drc_violations": best.drc_violations,
            "lvs_clean": best.lvs_clean, "reward": best.reward, "mode": best.mode,
        } if best else None,
        "cost_usd": pipeline.cost_tracker.total_cost_usd,
        "total_tokens": pipeline.cost_tracker.total_tokens,
        "gds_path": state.gds_path,
    }


def _print_event(ev: dict) -> None:
    print(f"  [{ev['stage']:<15}] {ev['status']:<7} {ev['detail']}", flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    pipeline = _build_pipeline(args)
    spec_text = Path(args.spec).read_text(encoding="utf-8")
    state = pipeline.run(spec_text, run_id=args.run_id, on_event=None if args.quiet else _print_event)
    print(json.dumps(_summary(state, pipeline), indent=2))
    return 0 if state.signoff_passed else 1


def cmd_eval(args: argparse.Namespace) -> int:
    """Run every bundled example through the pipeline and write a results artifact."""
    root = Path(__file__).resolve().parents[2]
    rows = []
    for fx in sorted((root / "hardware" / "examples").glob("*/fixtures/contract.json")):
        ex = fx.parent.parent
        args.inject_bug = True
        pipeline = _build_pipeline(args)
        spec = (ex / "spec.md").read_text(encoding="utf-8")
        t0 = time.perf_counter()
        state = pipeline.run(spec, run_id=f"eval_{ex.name}_{int(t0)}")
        row = _summary(state, pipeline) | {"design": ex.name, "elapsed_sec": round(time.perf_counter() - t0, 2)}
        rows.append(row)
        print(f"{'PASS' if state.signoff_passed else 'FAIL'} {ex.name:<18} {state.stage.value:<10} "
              f"formal_iters={row['formal_iterations']} {row['elapsed_sec']}s {state.error_message or ''}")
    out = Path(args.out) if args.out else root / "benchmarks" / "results" / f"{time.strftime('%Y-%m-%d')}-{args.backend or 'default'}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"backend": args.backend, "results": rows}, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0 if all(r["signoff_passed"] for r in rows) else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_flow_config(args.config)
    print("SiliconForge doctor")
    for tool in ("python", "node", "pnpm", "cmake", "git", "wsl", "docker", "nvidia-smi", "llama-server"):
        print(f"  {'ok ' if shutil.which(tool) else '-- '} {tool}")
    for mode in MODES:
        if mode == "simulated":
            continue
        cfg.eda_mode = mode
        try:
            tools = EDABridge(cfg).check_tools()
        except Exception as exc:  # noqa: BLE001 - diagnostics must never crash
            print(f"  backend {mode:<7}: unavailable ({type(exc).__name__}: {exc})")
            continue
        ok = ", ".join(t for t, v in tools.items() if v) or "none"
        missing = ", ".join(t for t, v in tools.items() if not v) or "none"
        print(f"  backend {mode:<7}: have [{ok}]  missing [{missing}]")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("Install the API extras: uv pip install -e '.[api]'")
        return 1
    uvicorn.run("services.orchestrator.api.app:create_app", factory=True, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="siliconforge", description="SiliconForge: autonomous Spec-to-GDSII chip design agent")
    sub = p.add_subparsers(dest="cmd")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--config", default="configs/flow_default.yaml", help="Flow YAML config")
        sp.add_argument("--backend", choices=MODES, default=None, help="EDA backend (overrides config eda_mode)")
        sp.add_argument("--profile", default=None,
                        help="Model routing profile: replay | local | hybrid | cloud | openai | gemini | subscription")
        sp.add_argument("--provider", dest="provider", default=None, help=argparse.SUPPRESS)

    r = sub.add_parser("run", help="Run the full flow on a spec")
    r.add_argument("--spec", required=True, help="Path to hardware specification (markdown/text)")
    r.add_argument("--run-id", default=None)
    r.add_argument("--inject-bug", action="store_true", help="Replay provider: start with a known-buggy RTL to exercise repair")
    r.add_argument("--quiet", action="store_true", help="Only print the final JSON summary")
    common(r)
    r.set_defaults(func=cmd_run)

    e = sub.add_parser("eval", help="Run all bundled examples and record results")
    e.add_argument("--out", default=None)
    common(e)
    e.set_defaults(func=cmd_eval)

    d = sub.add_parser("doctor", help="Check the toolchain")
    d.add_argument("--config", default="configs/flow_default.yaml")
    d.set_defaults(func=cmd_doctor)

    s = sub.add_parser("serve", help="Start the HTTP API (and web UI)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)
    return p


def cli_main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Back-compat: `python -m ...main --spec x` behaves like `run --spec x`
    if argv and argv[0].startswith("--") and argv[0] not in ("-h", "--help"):
        argv = ["run", *argv]
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(cli_main())
