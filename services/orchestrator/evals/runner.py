# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run an evaluation and write a versioned result file.

A result file is self-describing: suite and dataset commit, model, arm and its settings, the problem selection and its
seed, tool versions, the runner that executed the tools, and per-problem outcomes. The aggregate carries a bootstrap
interval over problems. Nothing here is estimated: every count comes from a graded candidate.
"""
from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from services.orchestrator.evals import verilogeval as ve
from services.orchestrator.evals.arms import LLM, Arm
from services.orchestrator.evals.stats import summarize

SCHEMA = "siliconforge.eval/1"


def _tool_versions(runner) -> dict[str, str]:
    out = {}
    with tempfile.TemporaryDirectory(prefix="sf_ev_ver_") as tmp:
        for tool, cmd in (("iverilog", ["iverilog", "-V"]),):
            res = runner.run(cmd, Path(tmp), timeout=60)
            first = (res.stdout or res.stderr).strip().splitlines()
            out[tool] = first[0][:80] if first else "unknown"
    return out


def run_verilogeval(
    arm: Arm, llm: LLM, runner, *, model: str, n: int = 1, k: int = 1, temperature: float = 0.0, sample_temperature: float = 0.8,
    problems: int | None = None, seed: int = 0, workers: int = 1, root: Path | None = None,
    on_progress: Callable[[str], None] | None = None, extra: dict[str, Any] | None = None,
) -> dict:
    """Evaluate `arm` on VerilogEval. With n=1 the single sample uses `temperature`; with n>1 the samples use
    `sample_temperature` so that pass@k means something."""
    allp = ve.load_problems(root)
    if not allp:
        raise RuntimeError("no VerilogEval problems found; run `siliconforge evals fetch verilogeval` first")
    chosen = ve.select(allp, problems, seed)
    temp = temperature if n == 1 else sample_temperature
    t_start = time.perf_counter()

    def one(p: ve.Problem) -> dict:
        samples = []
        for _ in range(n):
            with tempfile.TemporaryDirectory(prefix="sf_ev_") as tmp:
                work = Path(tmp)
                try:
                    att = arm.solve(p, llm, runner, temp, work)
                    g = ve.grade(runner, work, p, att.rtl)
                    err = None
                except Exception as exc:  # a failed call is a failed sample, recorded, not hidden
                    att, g, err = None, ve.Grade(False, False, detail=str(exc)[:300]), f"{type(exc).__name__}: {str(exc)[:200]}"
            samples.append({
                "passed": g.passed, "compiled": g.compiled, "mismatches": g.mismatches, "testbench_samples": g.samples,
                "calls": att.calls if att else 0, "rounds": att.rounds if att else 0,
                "prompt_tokens": att.prompt_tokens if att else 0, "completion_tokens": att.completion_tokens if att else 0,
                "latency_s": round(att.latency_s, 2) if att else 0.0, "error": err,
                "detail": g.detail if not g.passed else "",
            })
        c = sum(1 for x in samples if x["passed"])
        if on_progress:
            on_progress(f"{'PASS' if c else 'FAIL'} {p.id} {c}/{n}")
        return {"id": p.id, "n": n, "c": c, "samples": samples}

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            per = list(ex.map(one, chosen))
    else:
        per = [one(p) for p in chosen]

    agg = summarize([(r["n"], r["c"]) for r in per], k=k, seed=seed)
    flat = [s for r in per for s in r["samples"]]
    return {
        "schema": SCHEMA,
        "suite": ve.SUITE,
        "dataset": {"source": f"https://github.com/{ve.REPO}", "commit": ve.PINNED_COMMIT, "license": "MIT",
                    "problems_total": len(allp), "excluded": ve.EXCLUDED, "testbench_patch": ve.TESTBENCH_PATCH},
        "model": model,
        "arm": arm.describe(),
        "n": n, "k": k, "temperature": temp,
        "selection": {"count": len(chosen), "seed": seed, "all": len(chosen) == len(allp)},
        "runner": getattr(runner, "name", "unknown"),
        "tools": _tool_versions(runner),
        f"pass_at_{k}": agg,
        "compile_rate": round(sum(1 for x in flat if x["compiled"]) / len(flat), 4) if flat else None,
        "errors": sum(1 for x in flat if x["error"]),
        "tokens": {"prompt": sum(x["prompt_tokens"] for x in flat), "completion": sum(x["completion_tokens"] for x in flat),
                   "calls": sum(x["calls"] for x in flat)},
        "wall_s": round(time.perf_counter() - t_start, 1),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        **(extra or {}),
        "per_problem": per,
    }


def write_result(result: dict, out_dir: Path, tag: str = "") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    arm = result["arm"]["arm"]
    safe_model = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in result["model"])
    name = f"eval-{result['suite']}-{safe_model}-{arm}{('-' + tag) if tag else ''}-{time.strftime('%Y-%m-%d')}.json"
    path = out_dir / name
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path


def load_result(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path.name}: not a {SCHEMA} result")
    return data

