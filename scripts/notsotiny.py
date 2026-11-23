#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""NotSoTiny runs: fetch the dataset, pick a self-checked sample, run an arm, compare arms.

    python scripts/notsotiny.py fetch
    python scripts/notsotiny.py select --n 40 --seed 0 --out benchmarks/notsotiny/sample-seed0-n40.json
    python scripts/notsotiny.py run --arm single --serve models/gguf/Qwen3.5-9B-Q4_K_M.gguf --sample benchmarks/notsotiny/sample-seed0-n40.json
    python scripts/notsotiny.py run --arm feedback --profile gemini-agy --sample ...
    python scripts/notsotiny.py run --arm agent --cli agy|claude --sample ...
    python scripts/notsotiny.py compare A.json B.json
    python scripts/notsotiny.py check project.v        # what the plain-agent arms run to see Yosys' diagnostic

Arms: `single` (one reply), `feedback` (up to two repairs from Yosys' parse diagnostic only), `agent` (a plain coding agent CLI with Yosys; see evals/plain_agent.py).
Nothing here sees the reference module. The sample list is committed before any arm is run on it.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.orchestrator.evals import notsotiny as ns  # noqa: E402
from services.orchestrator.evals.stats import bootstrap_ci, paired_difference  # noqa: E402
from services.orchestrator.paths import data_root  # noqa: E402

SCHEMA = "siliconforge.notsotiny/1"


def _runner(backend: str):
    from services.orchestrator.config import load_flow_config
    from services.orchestrator.evals.cli import make_runner

    cfg = load_flow_config()
    cfg.eda_mode = backend
    return make_runner(backend, cfg), cfg


def cmd_fetch(a) -> int:
    p = ns.fetch(force=a.force)
    print(f"{len(ns.load_tasks())} tasks at {p.name} (revision {ns.REVISION[:8]}). {ns.LICENSE_NOTE}")
    return 0


def cmd_select(a) -> int:
    runner, _ = _runner(a.backend)
    tasks = ns.load_tasks()
    with tempfile.TemporaryDirectory(prefix="sf_nst_", ignore_cleanup_errors=True) as t:
        kept, dropped = ns.selfcheck(runner, Path(t), tasks, want=a.n, seed=a.seed, timeout=a.timeout, max_chars=a.max_chars, on_progress=print)
    doc = {"schema": "siliconforge.notsotiny-sample/1", "dataset": ns.REPO, "revision": ns.REVISION, "seed": a.seed, "wanted": a.n, "max_prompt_chars": a.max_chars,
           "grader": "equiv_simple + equiv_induct -seq 30 (evals/equiv.py)", "runner": runner.name,
           "tasks": [x.id for x in kept], "dropped_before_any_model_ran": dropped, "created": ns.now()}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"kept {len(kept)}, dropped {len(dropped)}; wrote {a.out}")
    return 0


def _llm(a, cfg):
    from services.orchestrator.evals.cli import make_llm

    server = None
    label = a.label
    if a.serve:
        from services.orchestrator.inference.llamacpp import LlamaCpp, LlamaServer, ServerConfig

        exe = LlamaCpp().server_exe
        if exe is None:
            raise SystemExit("llama-server not installed: siliconforge models install")
        model = Path(a.serve).resolve()
        server = LlamaServer(exe, ServerConfig(model_path=model, ctx=a.ctx, kv_type="q8_0", port=a.port, reasoning="auto", spec_type="none"))
        server.start()
        label = label or model.stem
    cfg.inference.profile = a.profile
    llm, routed = make_llm(cfg, "rtl_generation", "off", a.max_tokens)
    return llm, label or routed, server


def _record_llm_arm(a, runner, llm, task, tmp: Path) -> dict:
    t0 = time.perf_counter()
    calls, pt, ct = 0, 0, 0
    reply = llm(task.system, task.prompt, 0.0)
    calls, pt, ct = calls + 1, pt + reply.prompt_tokens, ct + reply.completion_tokens
    code = ns.extract_body(reply.text)
    g = ns.grade(runner, tmp, task, code, timeout=a.timeout)
    if a.arm == "feedback":
        for _ in range(2):
            if g["syntax"]:
                break
            fix = (f"{task.prompt}\n\nYour previous answer did not read in Yosys. The diagnostic was:\n{g['detail']}\n\nYour previous answer:\n"
                   f"```verilog\n{code}\n```\nReturn only the corrected body between the markers, in one code block.")
            reply = llm(task.system, fix, 0.0)
            calls, pt, ct = calls + 1, pt + reply.prompt_tokens, ct + reply.completion_tokens
            code = ns.extract_body(reply.text)
            g = ns.grade(runner, tmp, task, code, timeout=a.timeout)
    return {**g, "calls": calls, "tokens_in": pt, "tokens_out": ct, "seconds": round(time.perf_counter() - t0, 1)}


def cmd_run(a) -> int:
    runner, cfg = _runner(a.backend)
    sample = json.loads(Path(a.sample).read_text(encoding="utf-8"))
    by_id = {t.id: t for t in ns.load_tasks()}
    guard = ns.scrambled_on_disk() if a.arm == "agent" else contextlib.nullcontext()
    guard.__enter__()
    ids = sample["tasks"][: a.max_tasks] if a.max_tasks else sample["tasks"]
    server = None
    llm = None
    label = a.cli if a.arm == "agent" else None
    if a.arm != "agent":
        llm, label, server = _llm(a, cfg)
    records = []
    stopped = ""
    t_start = time.perf_counter()
    try:
        for i, tid in enumerate(ids):
            task = by_id[tid]
            with tempfile.TemporaryDirectory(prefix="sf_nst_", ignore_cleanup_errors=True) as t:
                tmp = Path(t)
                if a.arm == "agent":
                    from services.orchestrator.evals import plain_agent as pa

                    r = pa.run(a.cli, task, tmp / "ws", ROOT, model=a.model, timeout=a.agent_timeout, transcripts=data_root() / "transcripts" / ns.SUITE)
                    code = r.pop("code")
                    if r["lookup_markers"]:  # the transcript shows a lookup of the reference: the run is void, whatever the answer
                        g = {"syntax": False, "verdict": "void_reference_lookup", "detail": "transcript contains: " + ", ".join(r["lookup_markers"])}
                    else:
                        g = ns.grade(runner, tmp / "grade", task, code, timeout=a.timeout)
                    rec = {**g, **r}
                    if r["error"] and "limit" in r["error"].lower():
                        stopped = f"stopped after {i} tasks: {r['error'][:120]}"
                        records.append({"id": tid, **rec})
                        break
                else:
                    rec = _record_llm_arm(a, runner, llm, task, tmp)
            records.append({"id": tid, **rec})
            print(f"{i + 1:>3}/{len(ids)} {tid:<18} {rec['verdict']:<12} {rec.get('seconds', '')}s", flush=True)
    finally:
        guard.__exit__(None, None, None)
        if server is not None:
            server.stop()
    n = len(records)
    ok = [1.0 if r["verdict"] == "equivalent" else 0.0 for r in records]
    syn = [1.0 if r.get("syntax") else 0.0 for r in records]
    doc = {"schema": SCHEMA, "suite": ns.SUITE, "dataset": {"repo": ns.REPO, "revision": ns.REVISION, "license": "Apache-2.0"},
           "arm": a.arm + (f":{a.cli}" if a.arm == "agent" else ""), "model": label, "sample": Path(a.sample).name, "seed": sample["seed"],
           "void_runs": sum(1 for r in records if r["verdict"] == "void_reference_lookup"), "tasks_run": n, "tasks_in_sample": len(sample["tasks"]), "stopped": stopped or None, "runner": runner.name,
           "grader": sample["grader"], "equivalent_rate": {"mean": sum(ok) / n if n else None, "ci95": list(bootstrap_ci(ok)) if n else None},
           "syntax_ok_rate": sum(syn) / n if n else None,
           "tokens": {"in": sum(r.get("tokens_in") or 0 for r in records), "out": sum(r.get("tokens_out") or 0 for r in records)},
           "wall_s": round(time.perf_counter() - t_start, 1), "created": ns.now(), "per_task": records}
    out = Path(a.out) if a.out else ROOT / "benchmarks" / "results"
    out.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-._" else "-" for c in str(label))
    path = out / f"eval-{ns.SUITE}-{safe}-{a.arm}{('-' + a.tag) if a.tag else ''}-{time.strftime('%Y-%m-%d')}.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    m = doc["equivalent_rate"]
    print(f"{label} / {a.arm}: equivalent {m['mean']:.3f} (95% CI {m['ci95'][0]:.3f} to {m['ci95'][1]:.3f}) over {n} tasks; syntax ok {doc['syntax_ok_rate']:.3f}; wrote {path}")
    return 0


def cmd_compare(a) -> int:
    x, y = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (a.a, a.b))
    xa = {r["id"]: r["verdict"] == "equivalent" for r in x["per_task"]}
    ya = {r["id"]: r["verdict"] == "equivalent" for r in y["per_task"]}
    ids = sorted(set(xa) & set(ya))
    d = paired_difference([float(xa[i]) for i in ids], [float(ya[i]) for i in ids])
    print(f"{y['arm']} ({y['model']}) minus {x['arm']} ({x['model']}): {d['mean'] * 100:+.1f} points "
          f"(95% interval {d['ci95'][0] * 100:+.1f} to {d['ci95'][1] * 100:+.1f}) over {len(ids)} shared tasks")
    return 0


def cmd_table(a) -> int:
    """A markdown table of result files, with each arm's paired difference from the first on the tasks they share (so no number is typed by hand)."""
    docs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in a.files]
    base = {r["id"]: r["verdict"] == "equivalent" for r in docs[0]["per_task"]}
    print("| Setup | Tasks | Equivalent (95% interval) | Reads in Yosys | Void runs | vs first row (shared tasks, paired) |")
    print("| :--- | ---: | :--- | ---: | ---: | :--- |")
    for d in docs:
        m = d["equivalent_rate"]
        mine = {r["id"]: r["verdict"] == "equivalent" for r in d["per_task"]}
        ids = sorted(set(base) & set(mine))
        pd = paired_difference([float(base[i]) for i in ids], [float(mine[i]) for i in ids]) if d is not docs[0] and len(ids) > 1 else None
        diff = "-" if pd is None else f"{pd['mean'] * 100:+.1f} points ({pd['ci95'][0] * 100:+.1f} to {pd['ci95'][1] * 100:+.1f}), {len(ids)} tasks"
        n_eq = sum(mine.values())
        print(f"| {d['model']} / {d['arm']} | {d['tasks_run']} | {m['mean'] * 100:.1f}% ({m['ci95'][0] * 100:.1f} to {m['ci95'][1] * 100:.1f}), {n_eq} of {d['tasks_run']} "
              f"| {d['syntax_ok_rate'] * 100:.0f}% | {d.get('void_runs', 0)} | {diff} |")
    return 0


def cmd_check(a) -> int:
    runner, _ = _runner(a.backend)
    project = Path(a.file).read_text(encoding="utf-8")
    ok, diag = ns.syntax_check(runner, Path(tempfile.mkdtemp(prefix="sf_nst_check_")), ns.clean(project))
    print("OK: the project reads and elaborates in Yosys" if ok else "FAILED:\n" + diag)
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", default="wsl", choices=["wsl", "native", "docker"])
    s = p.add_subparsers(dest="cmd", required=True)
    f = s.add_parser("fetch")
    f.add_argument("--force", action="store_true")
    f.set_defaults(fn=cmd_fetch)
    se = s.add_parser("select")
    se.add_argument("--n", type=int, default=40)
    se.add_argument("--seed", type=int, default=0)
    se.add_argument("--timeout", type=float, default=180.0)
    se.add_argument("--max-chars", type=int, default=30000, help="skip tasks whose prompt is longer (a small model's context bounds every arm)")
    se.add_argument("--out", required=True)
    se.set_defaults(fn=cmd_select)
    r = s.add_parser("run")
    r.add_argument("--arm", required=True, choices=["single", "feedback", "agent"])
    r.add_argument("--sample", required=True)
    r.add_argument("--max-tasks", type=int, default=None)
    r.add_argument("--profile", default="local")
    r.add_argument("--serve", default=None, metavar="GGUF")
    r.add_argument("--ctx", type=int, default=16384)
    r.add_argument("--port", type=int, default=8189)
    r.add_argument("--max-tokens", type=int, default=4000)
    r.add_argument("--label", default=None)
    r.add_argument("--cli", default="agy", choices=["agy", "claude"])
    r.add_argument("--model", default=None, help="model name passed to the CLI (agent arm)")
    r.add_argument("--agent-timeout", type=float, default=700.0)
    r.add_argument("--timeout", type=float, default=180.0)
    r.add_argument("--tag", default="")
    r.add_argument("--out", default=None)
    r.set_defaults(fn=cmd_run)
    c = s.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.set_defaults(fn=cmd_compare)
    tb = s.add_parser("table")
    tb.add_argument("files", nargs="+")
    tb.set_defaults(fn=cmd_table)
    k = s.add_parser("check")
    k.add_argument("file")
    k.set_defaults(fn=cmd_check)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
