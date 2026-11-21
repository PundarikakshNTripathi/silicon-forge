#!/usr/bin/env python3
"""Benchmark a local GGUF model on this machine: quality (real formal + LEC) and speed (TTFT, tokens/s, KV-cache reuse).

For each bundled example spec the full pipeline runs with the model as every tier (profile `local`), verify-only
(stops after LEC, no place-and-route), on a real EDA backend. Writes benchmarks/results/local-<model>-<date>.json.

Usage:
    python benchmarks/bench_local_models.py --model models/gguf/Qwen3.5-9B-Q4_K_M.gguf [--backend wsl] [--designs a,b]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.orchestrator.config import load_flow_config  # noqa: E402
from services.orchestrator.eda.bridge import EDABridge  # noqa: E402
from services.orchestrator.inference.llamacpp import LlamaCpp, LlamaServer, ServerConfig, gpu_info  # noqa: E402
from services.orchestrator.pipeline import SiliconForgePipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="path to a .gguf")
    ap.add_argument("--backend", default="wsl")
    ap.add_argument("--designs", default="", help="comma-separated example names (default: all)")
    ap.add_argument("--ctx", type=int, default=16384)
    ap.add_argument("--kv", default="q8_0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--max-repair", type=int, default=3)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    model = Path(args.model)
    lc = LlamaCpp()
    exe = lc.server_exe
    if exe is None:
        print("llama-server not installed: python -m services.orchestrator.main models install")
        return 1
    server = LlamaServer(exe, ServerConfig(model_path=model, ctx=args.ctx, kv_type=args.kv, port=args.port))
    print(f"starting llama-server for {model.name} (ctx={args.ctx}, kv={args.kv}) ...", flush=True)
    t0 = time.time()
    server.start()
    load_s = round(time.time() - t0, 1)
    vram = gpu_info()
    print(f"server ready in {load_s}s; VRAM used {vram.get('vram_used_mib')} / {vram.get('vram_total_mib')} MiB", flush=True)

    wanted = {d for d in args.designs.split(",") if d}
    rows = []
    try:
        for fx in sorted((ROOT / "hardware" / "examples").glob("*/fixtures/contract.json")):
            ex = fx.parent.parent
            if wanted and ex.name not in wanted:
                continue
            cfg = load_flow_config()
            cfg.eda_mode = args.backend
            cfg.inference.profile = "local"
            cfg.inference.cache_path = None
            cfg.inference.enable_semantic_cache = False  # measure real generation, not cache hits
            cfg.pipeline["stop_after"] = "lec"
            cfg.pipeline["max_rtl_repair_iters"] = args.max_repair
            cfg.pipeline["max_formal_iters"] = args.max_repair
            with tempfile.TemporaryDirectory(prefix="sf_bench_") as tmp:
                cfg.workspace_root = Path(tmp)
                pipeline = SiliconForgePipeline(cfg, eda=EDABridge(cfg))
                spec = (ex / "spec.md").read_text(encoding="utf-8")
                t1 = time.perf_counter()
                state = pipeline.run(spec, run_id=f"bench_{ex.name}")
                elapsed = round(time.perf_counter() - t1, 1)
            snap = pipeline.cost_tracker.snapshot()
            reached_lec = state.lec_passed
            rows.append({
                "design": ex.name,
                "verified": bool(reached_lec and state.error_message is None),
                "stage": state.stage.value,
                "error": state.error_message,
                "formal_iterations": len(state.formal_history),
                "rtl_versions": len(state.rtl_versions),
                "llm_calls": snap["calls"],
                "tokens": snap["total_tokens"],
                "elapsed_sec": elapsed,
                "tokens_per_s_p50": snap["tokens_per_s_p50"],
                "ttft_p50_s": snap["ttft_p50_s"],
                "prefix_cache_ratio": snap["prefix_cache_ratio"],
            })
            r = rows[-1]
            print(f"{'OK ' if r['verified'] else 'FAIL'} {r['design']:<18} {r['stage']:<14} calls={r['llm_calls']} "
                  f"tok/s={r['tokens_per_s_p50']} ttft={r['ttft_p50_s']}s cache={r['prefix_cache_ratio']} "
                  f"{elapsed}s {r['error'] or ''}", flush=True)
    finally:
        server.stop()

    summary = {
        "model": model.name, "ctx": args.ctx, "kv_cache": args.kv, "backend": args.backend,
        "gpu": vram.get("name"), "vram_used_mib_loaded": vram.get("vram_used_mib"), "load_s": load_s,
        "verified": sum(r["verified"] for r in rows), "designs": len(rows),
        "llama_cpp": lc.installed_tag(), "results": rows,
    }
    out = ROOT / "benchmarks" / "results" / f"local-{model.stem}{('-' + args.tag) if args.tag else ''}-{time.strftime('%Y-%m-%d')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{summary['verified']}/{summary['designs']} designs verified; wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
