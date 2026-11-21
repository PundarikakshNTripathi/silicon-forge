# SPDX-License-Identifier: AGPL-3.0-or-later
"""`siliconforge evals ...`: fetch a dataset, run an evaluation, list and compare results."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from services.orchestrator.evals import verilogeval as ve
from services.orchestrator.evals.arms import LLM, LLMReply, build_arm
from services.orchestrator.evals.runner import load_result, run_verilogeval, write_result
from services.orchestrator.evals.stats import paired_difference
from services.orchestrator.paths import RESOURCE_ROOT, data_root


def add_parser(sub) -> None:
    e = sub.add_parser("evals", help="Model evaluations: VerilogEval with seeds, intervals and ablation arms")
    es = e.add_subparsers(dest="evals_cmd", required=True)

    f = es.add_parser("fetch", help="Download the dataset (pinned commit) into the data directory")
    f.add_argument("suite", choices=["verilogeval"])
    f.add_argument("--force", action="store_true")

    r = es.add_parser("run", help="Run one model with one arm on a suite and write a result file")
    r.add_argument("suite", choices=["verilogeval"])
    r.add_argument("--profile", default="local", help="routing profile (local | gemini-agy | ...)")
    r.add_argument("--serve", default=None, metavar="GGUF", help="start a private llama-server for this model file (GPU guard applies)")
    r.add_argument("--ctx", type=int, default=16384)
    r.add_argument("--kv", default="q8_0")
    r.add_argument("--port", type=int, default=8189)
    r.add_argument("--arm", default="single", help="single | feedback | feedback-plain | sampled")
    r.add_argument("--rounds", type=int, default=3, help="model calls per candidate for the feedback arms")
    r.add_argument("--candidates", type=int, default=3, help="candidates for the sampled arm")
    r.add_argument("--n", type=int, default=1, help="samples per problem")
    r.add_argument("--k", type=int, default=1, help="pass@k to report (k <= n)")
    r.add_argument("--temperature", type=float, default=0.0, help="used when n == 1")
    r.add_argument("--problems", type=int, default=None, help="evaluate a seeded random subset of this size")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--task-type", default="rtl_generation", help="router task type, picks the model tier")
    r.add_argument("--backend", default="wsl", choices=["native", "wsl", "docker"])
    r.add_argument("--label", default=None, help="model name to record (default: the served file or the routed model)")
    r.add_argument("--tag", default="")
    r.add_argument("--out", default=None, help="result directory (default: <data dir>/benchmarks/results)")
    r.add_argument("--quiet", action="store_true")

    sc = es.add_parser("selfcheck", help="Grade each problem's own reference as the candidate: every one must pass")
    sc.add_argument("suite", choices=["verilogeval"])
    sc.add_argument("--backend", default="wsl", choices=["native", "wsl", "docker"])
    sc.add_argument("--workers", type=int, default=4)

    ls = es.add_parser("list", help="List result files")
    ls.add_argument("--dir", default=None)

    c = es.add_parser("compare", help="Paired comparison of two result files on their common problems")
    c.add_argument("a")
    c.add_argument("b")

    e.set_defaults(func=cmd_evals)


def make_runner(backend: str, cfg):
    from services.orchestrator.eda.bridge import make_backend

    return make_backend(backend, cfg).runner  # type: ignore[attr-defined]


def make_llm(cfg, task_type: str) -> tuple[LLM, str]:
    from services.orchestrator.inference.cost_tracker import CostTracker
    from services.orchestrator.inference.router import ModelRouter

    cfg.inference.cache_path = None
    cfg.inference.enable_semantic_cache = False  # measure generation, not cache hits
    cfg.inference.max_total_tokens = 200_000_000  # an evaluation is long on purpose; spend is bounded by the model's price
    tracker = CostTracker(max_budget_usd=cfg.inference.max_budget_usd, max_total_tokens=cfg.inference.max_total_tokens)
    router = ModelRouter(cfg.inference, tracker)
    tier = router.select_tier(task_type)
    target = router.chain(tier)[0]

    def llm(system: str, user: str, temperature: float) -> LLMReply:
        resp = router.complete(stage="EVAL", task_type=task_type, system_prompt=system, user_prompt=user,
                               temperature=temperature, bypass_cache=True)
        return LLMReply(resp.content, resp.prompt_tokens, resp.completion_tokens, resp.latency_s)

    return llm, f"{target.provider}:{target.model}"


def cmd_evals(args: argparse.Namespace) -> int:
    if args.evals_cmd == "fetch":
        print(ve.LICENSE_NOTE)
        path = ve.fetch(force=args.force)
        ve.write_manifest(path)
        print(f"{len(ve.load_problems(path))} problems in {path}")
        return 0
    if args.evals_cmd == "list":
        folders = [Path(args.dir)] if args.dir else [RESOURCE_ROOT / "benchmarks" / "results", data_root() / "benchmarks" / "results"]
        seen = set()
        for d in folders:
            for f in sorted(d.glob("eval-*.json")) if d.is_dir() else []:
                if f.name in seen:
                    continue
                seen.add(f.name)
                try:
                    r = load_result(f)
                except ValueError:
                    continue
                key = next((k for k in r if k.startswith("pass_at_")), None)
                m = r[key]
                print(f"{f.name}\n    {r['model']}  arm={r['arm']['arm']}  {key}={m['mean']:.3f} "
                      f"[{m['ci95'][0]:.3f}, {m['ci95'][1]:.3f}] over {m['problems']} problems")
        return 0
    if args.evals_cmd == "selfcheck":
        return _selfcheck(args)
    if args.evals_cmd == "compare":
        return _compare(Path(args.a), Path(args.b))
    return _run(args)


def _selfcheck(args: argparse.Namespace) -> int:
    """The reference solution, renamed to TopModule, must pass its own testbench. A failure means the harness is wrong."""
    import re
    import tempfile
    from concurrent.futures import ThreadPoolExecutor

    from services.orchestrator.config import load_flow_config

    runner = make_runner(args.backend, load_flow_config())
    problems = ve.load_problems(include_excluded=True)
    if not problems:
        print("dataset missing: run `siliconforge evals fetch verilogeval` first", file=sys.stderr)
        return 2

    def one(p):
        with tempfile.TemporaryDirectory(prefix="sf_ev_self_") as tmp:
            return p.id, ve.grade(runner, Path(tmp), p, re.sub(r"\bRefModule\b", "TopModule", p.ref))

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(one, problems))
    usable = [(pid, g) for pid, g in results if pid not in ve.EXCLUDED]
    bad = [(pid, g) for pid, g in usable if not g.passed]
    print(f"{len(usable) - len(bad)}/{len(usable)} reference solutions pass their own testbench")
    for pid, g in bad[:10]:
        print(f"  FAIL {pid}: compiled={g.compiled} mismatches={g.mismatches} {g.detail[:160]}")
    for pid, g in results:  # an exclusion must still be justified: it has to keep failing, or it should be lifted
        if pid in ve.EXCLUDED:
            state = "still fails, exclusion stands" if not g.passed else "NOW PASSES, lift the exclusion"
            print(f"  excluded {pid}: {state} ({ve.EXCLUDED[pid][:90]}...)")
            bad += [(pid, g)] if g.passed else []
    return 0 if not bad else 1


def _compare(a_path: Path, b_path: Path) -> int:
    a, b = load_result(a_path), load_result(b_path)
    ka = next(k for k in a if k.startswith("pass_at_"))
    if ka != next(k for k in b if k.startswith("pass_at_")):
        print("results report different k", file=sys.stderr)
        return 2
    from services.orchestrator.evals.stats import pass_at_k

    k = int(ka.rsplit("_", 1)[1])
    pa = {r["id"]: pass_at_k(r["n"], r["c"], k) for r in a["per_problem"] if r["n"] >= k}
    pb = {r["id"]: pass_at_k(r["n"], r["c"], k) for r in b["per_problem"] if r["n"] >= k}
    common = sorted(set(pa) & set(pb))
    if not common:
        print("no problems in common", file=sys.stderr)
        return 2
    d = paired_difference([pa[i] for i in common], [pb[i] for i in common])
    verdict = "the intervals separate them" if d["separated"] else "the data do not separate them"
    print(f"A: {a['model']} / {a['arm']['arm']}\nB: {b['model']} / {b['arm']['arm']}\n"
          f"common problems: {len(common)}\nA - B ({ka}): {d['mean']:+.3f}  95% CI [{d['ci95'][0]:+.3f}, {d['ci95'][1]:+.3f}]  -> {verdict}")
    return 0


def _run(args: argparse.Namespace) -> int:
    from services.orchestrator.config import load_flow_config

    cfg = load_flow_config()
    cfg.inference.profile = args.profile
    cfg.eda_mode = args.backend
    runner = make_runner(args.backend, cfg)
    if not runner.has_tool("iverilog"):
        print(f"iverilog is not available on the {runner.name} backend; install it (see docs/eda-setup.md)", file=sys.stderr)
        return 2
    if not ve.dataset_dir().is_dir():
        print("dataset missing: run `siliconforge evals fetch verilogeval` first", file=sys.stderr)
        return 2
    server = None
    label = args.label
    try:
        if args.serve:
            from services.orchestrator.inference.llamacpp import LlamaCpp, LlamaServer, ServerConfig

            exe = LlamaCpp().server_exe
            if exe is None:
                print("llama-server not installed: siliconforge models install", file=sys.stderr)
                return 2
            model = Path(args.serve).resolve()
            server = LlamaServer(exe, ServerConfig(model_path=model, ctx=args.ctx, kv_type=args.kv, port=args.port))
            server.start()
            label = label or model.stem
        llm, routed = make_llm(cfg, args.task_type)
        arm = build_arm(args.arm, args.rounds, args.candidates)
        result = run_verilogeval(
            arm, llm, runner, model=label or routed, n=args.n, k=args.k, temperature=args.temperature,
            problems=args.problems, seed=args.seed, workers=args.workers,
            on_progress=None if args.quiet else lambda s: print(s, flush=True),
            extra={"profile": args.profile, "routed": routed},
        )
    finally:
        if server is not None:
            server.stop()
    out_dir = Path(args.out) if args.out else data_root() / "benchmarks" / "results"
    path = write_result(result, out_dir, args.tag)
    key = f"pass_at_{args.k}"
    m = result[key]
    print(f"{result['model']} / {result['arm']['arm']}: {key} = {m['mean']:.3f} "
          f"(95% CI {m['ci95'][0]:.3f} to {m['ci95'][1]:.3f}) over {m['problems']} problems; "
          f"compile rate {result['compile_rate']}; {result['tokens']['calls']} calls; wrote {path}")
    return 0 if result["errors"] == 0 else 1

