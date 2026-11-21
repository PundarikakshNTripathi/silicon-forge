# SPDX-License-Identifier: AGPL-3.0-or-later
"""`siliconforge agentbench list | run | report`: the same tasks, tools and model, different agent loops."""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

from services.orchestrator.agentbench import report, tasks
from services.orchestrator.agentbench.arms.base import Arm
from services.orchestrator.paths import data_root

ARMS = ("custom", "langgraph", "pydantic_ai")
VERSIONED = ("langgraph", "langchain", "langchain-openai", "langchain-core", "pydantic-ai-slim", "pydantic", "openai", "httpx")


def build_arm(name: str) -> Arm:
    """`custom`, `langgraph`, `pydantic_ai`; a `+guards` suffix gives a framework arm our redaction and output fencing."""
    base, _, flag = name.partition("+")
    guarded = flag == "guards"
    if flag and not guarded:
        raise SystemExit(f"unknown arm option {flag!r} in {name!r}; the only one is +guards")
    try:
        if base == "custom":
            from services.orchestrator.agentbench.arms.custom import CustomArm

            if guarded:
                raise SystemExit("+guards applies to framework arms; the custom arm always has its guards on")
            return CustomArm()
        if base == "langgraph":
            from services.orchestrator.agentbench.arms.langgraph_arm import LangGraphArm

            arm: Arm = LangGraphArm(guarded=guarded)
        elif base == "pydantic_ai":
            from services.orchestrator.agentbench.arms.pydantic_ai_arm import PydanticAiArm

            arm = PydanticAiArm(guarded=guarded)
        else:
            raise SystemExit(f"unknown arm {name!r}; choose from {', '.join(ARMS)}")
    except ImportError as exc:
        raise SystemExit(f"arm {base!r} needs the optional frameworks: pip install \"siliconforge[bench]\" ({exc})") from exc
    arm.name = name  # type: ignore[misc]
    return arm


def versions() -> dict[str, str]:
    out = {}
    for pkg in VERSIONED:
        try:
            out[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            continue
    return out


def add_parser(sub) -> None:
    p = sub.add_parser("agentbench", help="Benchmark agent loops (ours and framework-built) on the same tasks, tools and model")
    p.add_argument("action", choices=["list", "run", "report", "compare"])
    p.add_argument("files", nargs="*", help="report: result files; compare: the earlier file, then the later one")
    p.add_argument("--max-drop", type=float, default=0.10, help="compare: a drop this large (0.10 = 10 points) can be a regression")
    p.add_argument("--gate", action="store_true", help="compare: exit 1 if any arm regressed (for CI and scheduled canaries)")
    p.add_argument("--arms", default=",".join(ARMS), help=f"comma-separated: {', '.join(ARMS)}; add +guards to a framework arm")
    p.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--upstream", default="http://127.0.0.1:8189", help="llama-server (or any OpenAI-compatible server) root URL")
    p.add_argument("--serve", default=None, metavar="GGUF", help="start llama-server on this model for the run (refuses if the GPU is busy)")
    p.add_argument("--ctx", type=int, default=16384)
    p.add_argument("--port", type=int, default=8189)
    p.add_argument("--model", default="local", help="model name sent to the server")
    p.add_argument("--label", default=None, help="name recorded in the result (default: the GGUF's file name)")
    p.add_argument("--backend", default="none", choices=["none", "wsl", "native", "docker"],
                   help="EDA backend for simulation-checked tasks; with none those tasks are skipped and reported")
    p.add_argument("--max-steps", type=int, default=None, help="override every task's step budget")
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--deadline", type=float, default=300.0, help="seconds per run before tools start refusing")
    p.add_argument("--tag", default="")
    p.add_argument("--out", default=None, help="result directory (default: <data dir>/benchmarks/results)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_agentbench)


def _runner(backend: str) -> Any:
    if backend == "none":
        return None
    from services.orchestrator.config import load_flow_config
    from services.orchestrator.eda.bridge import EDABridge, normalize_mode

    cfg = load_flow_config()
    cfg.eda_mode = normalize_mode(backend)
    return EDABridge(cfg).backend.runner


def cmd_agentbench(args: argparse.Namespace) -> int:
    if args.action == "list":
        for t in tasks.SUITE:
            print(f"{t.id:<22} {t.category:<14} {'EDA ' if t.needs_eda else '    '}{t.title}")
        return 0
    if args.action == "report":
        for f in args.files:
            doc = json.loads(Path(f).read_text(encoding="utf-8"))
            print(json.dumps(doc["summary"], indent=2) if args.json else report.render(doc))
        return 0 if args.files else 2
    if args.action == "compare":
        if len(args.files) != 2:
            print("compare needs exactly two result files: the earlier one, then the later one", file=sys.stderr)
            return 2
        before, after = (json.loads(Path(f).read_text(encoding="utf-8")) for f in args.files)
        c = report.compare_docs(before, after, args.max_drop)
        print(json.dumps(c, indent=2) if args.json else report.render_compare(c))
        return 1 if (args.gate and c["regressions"]) else 0
    return _run(args)


def _run(args: argparse.Namespace) -> int:
    import httpx

    from services.orchestrator.agentbench.proxy import MeasuringProxy
    from services.orchestrator.agentbench.runner import RunRecord, run_matrix

    wanted = [t.strip() for t in args.tasks.split(",") if t.strip()]
    unknown = [t for t in wanted if t not in tasks.BY_ID]
    if unknown:
        print(f"unknown task(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    suite = [tasks.BY_ID[t] for t in wanted] or list(tasks.SUITE)
    arms = [build_arm(a.strip()) for a in args.arms.split(",") if a.strip()]
    if not arms:
        print("no arms given", file=sys.stderr)
        return 2
    runner = _runner(args.backend)
    server = None
    label = args.label
    try:
        if args.serve:
            from services.orchestrator.inference.llamacpp import LlamaCpp, LlamaServer, ServerConfig

            exe = LlamaCpp().server_exe
            if exe is None:
                print("llama-server not installed: siliconforge models install", file=sys.stderr)
                return 2
            model_path = Path(args.serve).resolve()
            server = LlamaServer(exe, ServerConfig(model_path=model_path, ctx=args.ctx, port=args.port, reasoning="off"))
            server.start()
            args.upstream = server.base_url
            label = label or model_path.stem
        try:
            httpx.get(args.upstream.rstrip("/") + "/health", timeout=5)
        except httpx.HTTPError as exc:
            print(f"no model server at {args.upstream} ({type(exc).__name__}); start one or pass --serve", file=sys.stderr)
            return 2
        label = label or args.model
        with MeasuringProxy(args.upstream) as proxy:
            httpx.post(proxy.base_url + "/chat/completions", timeout=300,
                       json={"model": args.model, "messages": [{"role": "user", "content": "Say OK."}], "max_tokens": 8,
                             "chat_template_kwargs": {"enable_thinking": False}})  # warm-up: weights paged in, prompt cache primed
            proxy.records.clear()

            def progress(r: RunRecord) -> None:
                state = "skipped" if r.skipped else ("solved" if r.solved and not r.violations else "FAILED")
                print(f"  {r.task:<22} {r.arm:<22} trial {r.trial}  {state:<8} {r.wall_s:6.1f}s  {r.stopped}"
                      + (f"  violations: {len(r.violations)}" if r.violations else ""), flush=True)

            print(f"{len(suite)} task(s) x {len(arms)} arm(s) x {args.trials} trial(s) against {label}")
            records = run_matrix(suite, arms, args.trials, proxy, base_url=proxy.base_url, model=args.model, runner=runner,
                                 progress=progress, deadline_s=args.deadline, max_steps=args.max_steps, temperature=args.temperature)
        meta = {"model": label, "trials": args.trials, "arms": [a.name for a in arms], "tasks": [t.id for t in suite],
                "temperature": args.temperature, "thinking": "off", "streaming": "off", "deadline_s": args.deadline,
                "max_steps": args.max_steps, "backend": args.backend, "ctx": args.ctx if args.serve else None,
                "versions": versions(), "python": platform.python_version(),
                "note": "all arms: same model, sampling, tools, sandbox and step budget; model calls timed at one proxy, tool calls at "
                        "one tool seam; unit of analysis is the task"}
        doc = report.build_doc(records, meta)
        out_dir = Path(args.out) if args.out else data_root() / "benchmarks" / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-._" else "-" for c in label)
        out = out_dir / f"agentbench-{safe}{('-' + args.tag) if args.tag else ''}-{time.strftime('%Y-%m-%d')}.json"
        out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        print()
        print(json.dumps(doc["summary"], indent=2) if args.json else report.render(doc))
        print(f"\nwrote {out}")
        return 0
    finally:
        if server is not None:
            server.stop()
