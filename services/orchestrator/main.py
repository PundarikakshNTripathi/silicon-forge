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


NL = chr(10)


def _agent_session(args: argparse.Namespace, interactive: bool):
    from services.orchestrator.agent import AgentEvent, create_session
    from services.orchestrator.workspace import find_workspace, remember_workspace

    ws = find_workspace(args.workspace)
    remember_workspace(ws)
    profile = args.profile or ws.profile

    def show(ev: AgentEvent) -> None:
        if ev.kind == "assistant":
            print(f"{NL}{ev.text}", flush=True)
        elif ev.kind == "tool_call":
            print(f"  > {ev.text}", flush=True)
        elif ev.kind == "tool_result" and args.verbose:
            print(f"    {ev.text[:300]}", flush=True)
        elif ev.kind in ("approval", "error"):
            print(f"  ! {ev.text}", flush=True)

    def approve(kind: str, summary: str, details: dict) -> bool:
        if not interactive:
            return False
        return input(f"  allow {kind}: {summary}  [y/N] ").strip().lower() in ("y", "yes")

    session = create_session(ws, profile=profile, mode=args.mode, approver=approve, on_event=show,
                             max_steps=args.max_steps)
    return ws, profile, session


def cmd_agent(args: argparse.Namespace) -> int:
    """One-shot agent turn in a workspace (headless): `siliconforge agent "add a reset to rtl/cnt.sv"`."""
    ws, profile, session = _agent_session(args, interactive=sys.stdin.isatty() and args.mode in ("ask", "accept-edits"))
    print(f"workspace {ws.root}  profile {profile}  mode {args.mode}")
    res = session.send(args.task)
    print(f"{NL}[{res.stopped}] {res.steps} step(s)")
    return 0 if res.stopped == "done" else 1


def cmd_chat(args: argparse.Namespace) -> int:
    """Interactive session in the current directory, like Claude Code or Codex: type a request, approve what it does."""
    ws, profile, session = _agent_session(args, interactive=True)
    print(f"SiliconForge in {ws.root}{NL}profile {profile}  mode {args.mode}  (/undo, /mode <m>, /exit)")
    while True:
        try:
            line = input(f"{NL}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/undo":
            print(session.tools.undo())
            continue
        if line.startswith("/mode"):
            new = line.split()[-1]
            if new in ("ask", "accept-edits", "auto", "plan"):
                session.mode = new  # type: ignore[assignment]
                print(f"mode: {new}")
            else:
                print("modes: ask, accept-edits, auto, plan")
            continue
        session.send(line)


def _workspace_runner(args: argparse.Namespace):
    """The EDA runner (native, wsl or docker) for the workspace's backend; refuses the simulated backend."""
    from services.orchestrator.eda.bridge import EDABridge
    from services.orchestrator.workspace import find_workspace

    ws = find_workspace(args.workspace)
    cfg = load_flow_config(args.config)
    ws.apply(cfg)
    if args.backend:
        cfg.eda_mode = normalize_mode(args.backend)
    if cfg.eda_mode == "simulated":
        raise SystemExit("verification needs a real backend: set backend in siliconforge.yaml or pass --backend wsl|native|docker")
    return ws, EDABridge(cfg).backend.runner


def cmd_verify(args: argparse.Namespace) -> int:
    """Verification toolkit: sim (cocotb), mutate, toggle, decode."""
    from services.orchestrator import verify

    ws, runner = _workspace_runner(args)
    try:
        if args.action == "sim":
            rep = verify.run_cocotb(runner, ws.root, sources=args.sources, top=args.top, test_module=args.test_module,
                                    test_dir=args.test_dir, seeds=tuple(int(s) for s in args.seeds.split(",")),
                                    includes=tuple(args.include), defines=dict(d.split("=", 1) for d in args.define),
                                    env=dict(e.split("=", 1) for e in args.env), waves=args.waves)
            print(f"[{rep['provenance']}] {rep['passed']} passed, {rep['failed']} failed over seeds {rep['seeds']}")
            for ex in rep["failure_excerpts"]:
                print(ex)
            return 0 if rep["ok"] else 1
        if args.action == "mutate":
            rep = verify.mutation_test(runner, ws.root, args.file, args.test, top=args.top, max_mutants=args.max,
                                       seed=args.seed, jobs=args.jobs, includes=tuple(args.include),
                                       support=tuple(args.support))
            pct = "n/a" if rep["score"] is None else f"{rep['score'] * 100:.1f}%"
            print(f"[{rep['provenance']}] mutation score {pct}: {rep['killed']} killed, {rep['survived']} survived, "
                  f"{rep['equivalent']} proven equivalent, {rep['invalid']} invalid (seed {rep['seed']})")
            for s in rep["survivors"][:20]:
                print(f"  survived {s['file']}:{s['line']}  {s['op']}  {s['original'].strip()!r} -> {s['replacement'].strip()!r}")
            return 0
        if args.action == "toggle":
            rep = verify.toggle_coverage(runner, ws.root, args.dump, scope=args.scope or "", exclude=tuple(args.exclude))
            pct = "n/a" if rep["coverage"] is None else f"{rep['coverage'] * 100:.1f}%"
            print(f"[{rep['provenance']}] toggle coverage {rep['covered_bits']}/{rep['bits']} bits ({pct})")
            for n in rep["not_toggled"][:15]:
                print(f"  never toggled both ways: {n}")
            return 0
    except verify.VerifyError as exc:
        print(f"verify failed: {exc}")
        return 2
    return 2


def cmd_tt(args: argparse.Namespace) -> int:
    """Tiny Tapeout (IHP SG13CMOS5L): scaffold, check, synth, gls, pack, doctor, harden."""
    from services.orchestrator import tt
    from services.orchestrator.verify import VerifyError

    root = Path(args.workspace).resolve()
    if args.action == "scaffold":
        if not args.top:
            print("usage: siliconforge tt scaffold DIR --top tt_um_yourname_project")
            return 2
        root = Path(args.dir).resolve()
        try:
            files = tt.scaffold(root, args.top, title=args.title, author=args.author, description=args.description,
                                tiles=args.tiles, clock_hz=args.clock_hz, force=args.force)
        except (ValueError, FileExistsError) as exc:
            print(f"error: {exc}")
            return 1
        print(f"wrote {len(files)} files under {root}")
        return 0
    if args.action == "pack":
        info = tt.load_info(root)
        out = root / "SUBMISSION.md"
        out.write_text(tt.submission_checklist(root, info), encoding="utf-8")
        print(f"wrote {out}")
        return 0
    if args.action == "doctor":
        ok, why = tt.docker_ready()
        print(f"docker: {'ready ' + why if ok else why}")
        for t in ("yosys", "iverilog", "verilator"):
            print(f"  {'ok ' if shutil.which(t) else '-- '} {t} (host PATH)")
        print(f"hardening flow pinned by Tiny Tapeout: LibreLane {tt.LIBRELANE_VERSION}, PDK {tt.PDK_VERSION[:10]}")
        return 0
    if args.action == "harden":
        ok, why = tt.docker_ready()
        if not ok:
            print(f"cannot harden locally: {why}")
            print("Official hardening runs LibreLane in a container. Start Docker Desktop and rerun, or push the project to")
            print("GitHub: its `gds` workflow (TinyTapeout/tt-gds-action) hardens it and runs the precheck for you.")
            return 3
        print("Docker is ready. Run the official flow from the project folder:")
        print(f"  git clone {tt.TT_TOOLS_REPO} tt && pip install -r tt/requirements.txt librelane=={tt.LIBRELANE_VERSION}")
        print("  ./tt/tt_tool.py --create-user-config --pdk ihp-sg13cmos5l && ./tt/tt_tool.py --harden --pdk ihp-sg13cmos5l")
        return 0
    # the rest need a real EDA backend and the IHP PDK
    ws, runner = _workspace_runner(args)
    root = ws.root
    try:
        if args.action == "synth":
            rep = tt.synthesize(runner, root)
            area = rep["area_um2"] or 0.0
            share = f", {100 * rep['sequential_area_um2'] / area:.0f}% sequential" if area else ""
            print(f"[{rep['provenance']}] {rep['cells']} cells, {area:.0f} um2, {rep['flops']} flops, "
                  f"{rep['latches']} latches{share}")
            info = tt.load_info(root)
            b = tt.budget(rep["area_um2"], str((info.get("project") or {}).get("tiles", "")) or None)
            for r in b["options"]:
                if r["tiles"] in ("1x1", "1x2", "2x2", "3x2", "4x2", "4x4", "6x4", "8x4"):
                    print(f"  {r['tiles']:>4}  {r['utilization']:>6.0%}  {r['verdict']}")
            print(f"smallest that fits (with {b['margin']}x margin for layout): {b['smallest_that_fits']}")
            return 0
        if args.action == "check":
            synth = tt.synthesize(runner, root) if args.synth else None
            rep = tt.preflight(root, synth)
            print(rep.text())
            return 0 if rep.ok else 1
        if args.action == "gls":
            rep = tt.run_gls(runner, root)
            print(f"[{rep['provenance']}] gate-level: {rep['passed']} passed, {rep['failed']} failed ({rep['netlist_kind']})")
            for ex in rep["failure_excerpts"]:
                print(ex)
            return 0 if rep["ok"] else 1
    except (VerifyError, FileNotFoundError) as exc:
        print(f"tt {args.action} failed: {exc}")
        return 2
    return 2


def cmd_isa(args: argparse.Namespace) -> int:
    """ISA toolchain: generate / asm / disasm / check / run, all from one isa.yaml."""
    from services.orchestrator import isa as isa_pkg

    try:
        spec = isa_pkg.load_spec(args.spec)
    except (isa_pkg.IsaError, OSError, KeyError) as exc:
        print(f"isa spec error: {exc}")
        return 1
    if args.action == "gen":
        for f in isa_pkg.generate_all(args.spec, args.out or "."):
            print(f"wrote {f}")
        return 0
    if args.action == "disasm":
        words = [int(x, 16) for x in Path(args.program).read_text().split() if x.strip()]
        print(isa_pkg.disassemble(spec, words), end="")
        return 0
    try:
        prog = isa_pkg.Assembler(spec).assemble(Path(args.program).read_text(encoding="utf-8"))
    except isa_pkg.AsmError as exc:
        print("assembly failed:")
        for e in exc.errors:
            print(f"  {e}")
        return 1
    if args.action == "asm":
        if args.hex:
            Path(args.hex).write_text(prog.hex(args.depth), encoding="utf-8")
            print(f"wrote {args.hex} ({len(prog.words)} words)")
        print(prog.listing())
        return 0
    inputs = {k: int(v, 0) for k, v in (kv.split("=", 1) for kv in args.input)}
    if args.action == "check":
        rep = isa_pkg.check_timing(prog, inputs)
        print(rep.text())
        print("timing: OK" if rep.ok else "timing: FAILED")
        return 0 if rep.ok else 1
    if args.action == "run":
        mach = isa_pkg.Machine(spec, prog, illegal="halt")
        mach.inputs = inputs
        for _ in range(args.steps):
            if mach.halted:
                break
            r = mach.step()
            print(f"cycle {mach.cycles - r.cycles:>6}  @{r.addr:<3} {isa_pkg.disassemble_word(spec, r.word):<20} {mach.s.snapshot()}")
        return 0
    return 2


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

    def agent_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--workspace", default=".", help="Folder the agent works in (default: here)")
        sp.add_argument("--profile", default=None, help="Model profile (default: the workspace's)")
        sp.add_argument("--mode", choices=["ask", "accept-edits", "auto", "plan"], default="ask")
        sp.add_argument("--max-steps", type=int, default=14)
        sp.add_argument("--verbose", action="store_true", help="Show tool results")

    ag = sub.add_parser("agent", help="Give the workspace agent one task (headless)")
    ag.add_argument("task")
    agent_opts(ag)
    ag.set_defaults(func=cmd_agent)

    ch = sub.add_parser("chat", help="Interactive agent session in this folder")
    agent_opts(ch)
    ch.set_defaults(func=cmd_chat)

    isa = sub.add_parser("isa", help="ISA toolchain from an isa.yaml: gen, asm, disasm, check, run")
    isa.add_argument("action", choices=["gen", "asm", "disasm", "check", "run"])
    isa.add_argument("spec", help="Path to isa.yaml")
    isa.add_argument("program", nargs="?", default=None, help="Assembly file (hex file for disasm)")
    isa.add_argument("--out", default=None, help="gen: output directory")
    isa.add_argument("--hex", default=None, help="asm: write a $readmemh image")
    isa.add_argument("--depth", type=int, default=None, help="asm: pad the image to this many words")
    isa.add_argument("--input", action="append", default=[], metavar="NAME=VALUE", help="check/run: external input, e.g. pins=0x01")
    isa.add_argument("--steps", type=int, default=64, help="run: instruction budget")
    isa.set_defaults(func=cmd_isa)

    vf = sub.add_parser("verify", help="Verification toolkit: sim (cocotb), mutate, toggle")
    vf.add_argument("action", choices=["sim", "mutate", "toggle"])
    vf.add_argument("--workspace", default=".")
    vf.add_argument("--config", default="configs/flow_default.yaml")
    vf.add_argument("--backend", choices=MODES, default=None)
    vf.add_argument("--sources", nargs="+", default=[], help="sim: Verilog sources, relative to the workspace")
    vf.add_argument("--top", default=None, help="sim: HDL top level; mutate: module for the equivalence check")
    vf.add_argument("--test-module", default=None, help="sim: cocotb module (no .py)")
    vf.add_argument("--test-dir", default=".")
    vf.add_argument("--seeds", default="1")
    vf.add_argument("--include", action="append", default=[])
    vf.add_argument("--define", action="append", default=[], metavar="NAME=VALUE")
    vf.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    vf.add_argument("--waves", action="store_true")
    vf.add_argument("--file", action="append", default=[], help="mutate: source file to mutate (repeatable)")
    vf.add_argument("--test", default=None, help="mutate: shell command that exits 0 when the tests pass")
    vf.add_argument("--max", type=int, default=60, help="mutate: how many mutants to evaluate")
    vf.add_argument("--seed", type=int, default=1)
    vf.add_argument("--jobs", type=int, default=4)
    vf.add_argument("--dump", default=None, help="toggle: VCD or FST file")
    vf.add_argument("--scope", default=None, help="toggle: hierarchical prefix, e.g. tb.dut")
    vf.add_argument("--exclude", action="append", default=[])
    vf.add_argument("--support", action="append", default=[], help="mutate: other sources the top module needs")
    vf.set_defaults(func=cmd_verify)

    ttp = sub.add_parser("tt", help="Tiny Tapeout (IHP SG13CMOS5L): scaffold, check, synth, gls, pack, doctor, harden")
    ttp.add_argument("action", choices=["scaffold", "check", "synth", "gls", "pack", "doctor", "harden"])
    ttp.add_argument("dir", nargs="?", default=".", help="scaffold: directory to create")
    ttp.add_argument("--workspace", default=".")
    ttp.add_argument("--config", default="configs/flow_default.yaml")
    ttp.add_argument("--backend", choices=MODES, default=None)
    ttp.add_argument("--top", default=None, help="scaffold: top module (must start with tt_um_)")
    ttp.add_argument("--title", default="")
    ttp.add_argument("--author", default="")
    ttp.add_argument("--description", default="")
    ttp.add_argument("--tiles", default="1x1")
    ttp.add_argument("--clock-hz", type=int, default=50_000_000)
    ttp.add_argument("--force", action="store_true")
    ttp.add_argument("--synth", action="store_true", help="check: also synthesize and check area and latches")
    ttp.set_defaults(func=cmd_tt)

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
