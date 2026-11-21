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
    ws_path = getattr(args, "workspace", None)
    if ws_path:
        from services.orchestrator.workspace import load_workspace, remember_workspace

        ws = load_workspace(ws_path)
        ws.apply(cfg)  # profile, backend, model policy, runs and the ledger all live in the workspace
        remember_workspace(ws)
    if getattr(args, "episodes", None):
        cfg.pipeline["max_pnr_rl_episodes"] = args.episodes
    if getattr(args, "parallel", None):
        cfg.rl_optimizer["parallel_episodes"] = args.parallel
    if getattr(args, "relax", False):
        cfg.rl_optimizer["allow_clock_relax"] = True
    if getattr(args, "verify_only", False):
        cfg.pipeline["stop_after"] = "lec"
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


def cmd_models(args: argparse.Namespace) -> int:
    """Manage the local llama.cpp server and GGUF models."""
    from services.orchestrator.inference.llamacpp import (
        LlamaCpp,
        LlamaServer,
        ServerConfig,
        gpu_info,
    )

    lc = LlamaCpp()
    if args.action in ("status", "detect"):
        gpu = gpu_info()
        print(f"GPU: {gpu.get('name', 'none')}  VRAM {gpu.get('vram_used_mib', '-')}/{gpu.get('vram_total_mib', '-')} MiB")
        print(f"llama.cpp: {lc.installed_tag() or 'not installed'}")
        for m in lc.discover_models():
            print(f"  model: {m['name']} ({m['size_gb']} GB, {m['source']})  {m['path']}")
        from services.orchestrator.inference.gpu import foreign_servers

        servers = foreign_servers(own_port=args.port)
        for sv in servers:
            tag = "ours" if sv["ours"] else "other program (read-only, SiliconForge will not touch it)"
            print(f"  server: {sv['url']}  {', '.join(sv['models']) or '?'}  [{tag}]")
        if not servers:
            print("  no model server found on 8080/8000/1234/11434/8189")
        return 0
    if args.action == "install":
        print(f"installed: {lc.install()}")
        return 0
    if args.action == "pull":
        if not (args.repo and args.file):
            print("usage: siliconforge models pull --repo <hf-repo> --file <name.gguf>")
            return 2
        print(f"ready: {lc.pull_model(args.repo, args.file)}")
        return 0
    exe = lc.server_exe
    if exe is None:
        print("llama-server not installed: run `siliconforge models install`")
        return 1
    if args.action == "start":
        if not (args.file or args.path):
            print("usage: siliconforge models start (--file <name.gguf> | --path <file.gguf>) [--ctx N] [--cpu]")
            return 2
        model = Path(args.path) if args.path else lc.home / "gguf" / args.file
        if not model.exists():
            print(f"model not found: {model}")
            return 1
        server = LlamaServer(exe, ServerConfig(model_path=model, ctx=args.ctx, port=args.port,
                                               gpu_layers=0 if args.cpu else 99))
        from services.orchestrator.inference.gpu import GpuBusyError

        try:
            server.start()
        except GpuBusyError as exc:
            print(f"not started: {exc}")
            return 3
        print(f"serving {model.name} on {server.base_url} (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            server.stop()
        return 0
    return 2


def cmd_init(args: argparse.Namespace) -> int:
    from services.orchestrator.workspace import WorkspaceError, init_workspace, remember_workspace

    try:
        ws = init_workspace(args.path, name=args.name, profile=args.profile, backend=args.backend, pdk=args.pdk,
                            allowed_models=args.allow_model or None, max_budget_usd=args.max_budget,
                            overwrite=args.force)
    except WorkspaceError as exc:
        print(f"error: {exc}")
        return 1
    remember_workspace(ws)
    print(f"workspace ready: {ws.root}")
    print(f"  profile={ws.profile} backend={ws.backend} pdk={ws.pdk or 'default'} "
          f"allowed_models={ws.allowed_models or 'any'}")
    print("  add SILICONFORGE.md (or AGENTS.md) there to give the agent standing instructions")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from services.orchestrator.inference.ledger import read_ledger, render_report
    from services.orchestrator.workspace import load_workspace

    ws = load_workspace(args.workspace)
    if args.kind != "ai-usage":
        return 2
    rows = read_ledger(ws.ledger_path)
    if not rows:
        print(f"No AI calls recorded yet in {ws.ledger_path}. Run something through this workspace first.")
        return 1
    text = render_report(rows, project=ws.name, allowed_models=ws.allowed_models)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    try:
        from services.tui.app import main as tui_main
    except ImportError:
        print("Install the TUI extra: uv pip install -e '.[tui]'")
        return 1
    tui_main()
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    try:
        from services.orchestrator.mcp_server import main as mcp_main
    except ImportError:
        print("Install the MCP extra: uv pip install -e '.[mcp]'")
        return 1
    mcp_main()
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
                        help="Model routing profile: replay | local | hybrid | dev | gemini-agy | competition | cloud | ...")
        sp.add_argument("--provider", dest="provider", default=None, help=argparse.SUPPRESS)
        sp.add_argument("--workspace", default=None, help="Run inside this workspace (its profile, policy and ledger apply)")

    r = sub.add_parser("run", help="Run the full flow on a spec")
    r.add_argument("--spec", required=True, help="Path to hardware specification (markdown/text)")
    r.add_argument("--run-id", default=None)
    r.add_argument("--inject-bug", action="store_true", help="Replay provider: start with a known-buggy RTL to exercise repair")
    r.add_argument("--quiet", action="store_true", help="Only print the final JSON summary")
    r.add_argument("--episodes", type=int, default=None, help="Max place-and-route optimizer episodes")
    r.add_argument("--parallel", type=int, default=None, help="Layout episodes to run concurrently")
    r.add_argument("--relax", action="store_true", help="Let the optimizer try relaxed clock periods")
    r.add_argument("--verify-only", action="store_true", help="Stop after formal proof and equivalence check")
    common(r)
    r.set_defaults(func=cmd_run)

    e = sub.add_parser("eval", help="Run all bundled examples and record results")
    e.add_argument("--out", default=None)
    common(e)
    e.set_defaults(func=cmd_eval)

    d = sub.add_parser("doctor", help="Check the toolchain")
    d.add_argument("--config", default="configs/flow_default.yaml")
    d.set_defaults(func=cmd_doctor)

    m = sub.add_parser("models", help="Manage local llama.cpp models")
    m.add_argument("action", choices=["status", "detect", "install", "pull", "start"])
    m.add_argument("--repo", default=None)
    m.add_argument("--file", default=None, help="GGUF name inside SiliconForge's model folder")
    m.add_argument("--path", default=None, help="GGUF anywhere on disk (e.g. your Hugging Face cache), used in place")
    m.add_argument("--cpu", action="store_true", help="Run on the CPU and leave the GPU alone")
    m.add_argument("--ctx", type=int, default=16384)
    m.add_argument("--port", type=int, default=8189)
    m.set_defaults(func=cmd_models)

    i = sub.add_parser("init", help="Create a workspace (siliconforge.yaml) in a directory")
    i.add_argument("path", nargs="?", default=".")
    i.add_argument("--name", default=None)
    i.add_argument("--profile", default="dev", help="Routing profile (replay | local | dev | gemini-agy | competition ...)")
    i.add_argument("--backend", choices=MODES, default="wsl")
    i.add_argument("--pdk", default=None)
    i.add_argument("--allow-model", action="append", default=[], metavar="GLOB",
                   help="Restrict the workspace to models matching this glob (repeatable), e.g. 'gemini-3.8-flash-*'")
    i.add_argument("--max-budget", type=float, default=None, help="USD budget for metered models")
    i.add_argument("--force", action="store_true", help="Overwrite an existing siliconforge.yaml")
    i.set_defaults(func=cmd_init)

    rp = sub.add_parser("report", help="Generate reports from a workspace")
    rp.add_argument("kind", choices=["ai-usage"])
    rp.add_argument("--workspace", default=".")
    rp.add_argument("--out", default=None)
    rp.set_defaults(func=cmd_report)

    t = sub.add_parser("tui", help="Terminal UI")
    t.set_defaults(func=cmd_tui)

    mc = sub.add_parser("mcp", help="MCP server (stdio) for coding agents")
    mc.set_defaults(func=cmd_mcp)

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
