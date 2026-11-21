#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure local inference speed for several llama-server configurations on this machine.

Each arm starts its own server (refusing, as always, to start if another program holds the VRAM), sends the same prompts with
temperature 0 and thinking off, and records decode and prompt tokens per second from llama-server's own `timings`, draft
acceptance when speculation is on, and VRAM in use once the model is loaded. Two kinds of prompt are used, because
speculative decoding helps one and not the other:

* `generate`: write a module from a spec (new text: little to copy);
* `edit`: rewrite an existing file with one change (most of the output repeats the prompt).

Arms: `baseline`, `kv-f16`, `kv-q4_0`, `ctx-4k`, `ctx-32k`, `ngram-mod`, `ngram-simple`, `draft:<path/to/draft.gguf>`.

Usage:
    python benchmarks/bench_speed.py --model models/gguf/Qwen3.5-9B-Q4_K_M.gguf --arms baseline,ngram-mod,kv-f16
Writes benchmarks/results/speed-<model>-<date>.json (file names only, no local paths).
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMA = "siliconforge.speed/1"
GENERATE = ("Write a synthesizable SystemVerilog module `up_down_counter` with a 4-bit count output, inputs clk, rst_n (active-low "
            "asynchronous reset), up_down and enable. It counts up when up_down is 1 and down otherwise, only while enable is 1. "
            "Reply with the module only.")
EDIT_TEMPLATE = ("Here is a SystemVerilog file:\n\n```systemverilog\n{src}\n```\n\nRewrite the whole file so the counter wraps "
                 "silently instead of saturating (if it saturates) and add a comment above each always block. Output the complete "
                 "file in one code block and nothing else.")


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Median decode/prompt speed and overall draft acceptance from a list of llama-server `timings` dicts."""
    if not samples:
        return {"samples": 0}
    dec = [s["predicted_per_second"] for s in samples if s.get("predicted_per_second")]
    pro = [s["prompt_per_second"] for s in samples if s.get("prompt_per_second")]
    drafted = sum(s.get("draft_n", 0) or 0 for s in samples)
    accepted = sum(s.get("draft_n_accepted", 0) or 0 for s in samples)
    out: dict[str, Any] = {
        "samples": len(samples),
        "decode_tok_s_median": round(statistics.median(dec), 2) if dec else None,
        "decode_tok_s_min": round(min(dec), 2) if dec else None,
        "decode_tok_s_max": round(max(dec), 2) if dec else None,
        "prompt_tok_s_median": round(statistics.median(pro), 1) if pro else None,
        "tokens_generated": sum(s.get("predicted_n", 0) or 0 for s in samples),
    }
    if drafted:
        out["draft_acceptance"] = round(accepted / drafted, 3)
        out["drafted"], out["accepted"] = drafted, accepted
    return out


def speedup(arm: dict[str, Any], base: dict[str, Any]) -> float | None:
    a, b = arm.get("decode_tok_s_median"), base.get("decode_tok_s_median")
    return round(a / b, 3) if a and b else None


def arm_config(name: str, model: Path, base_ctx: int, port: int):
    """(ServerConfig for the arm). Unknown arm names are an error."""
    from services.orchestrator.inference.llamacpp import ServerConfig

    kw: dict[str, Any] = {"model_path": model, "ctx": base_ctx, "port": port, "reasoning": "off"}
    if name == "baseline":
        pass
    elif name == "kv-f16":
        kw["kv_type"] = "f16"
    elif name == "kv-q4_0":
        kw["kv_type"] = "q4_0"
    elif name == "ctx-4k":
        kw["ctx"] = 4096
    elif name == "ctx-32k":
        kw["ctx"] = 32768
    elif name in ("ngram-mod", "ngram-simple", "ngram-map-k"):
        kw["spec_type"] = name
    elif name.startswith("draft:"):
        kw.update(spec_type="draft-simple", draft_model=Path(name.split(":", 1)[1]), draft_max=8)
    else:
        raise SystemExit(f"unknown arm {name!r}")
    return ServerConfig(**kw)


def vram_used_mib() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=15).stdout.strip().splitlines()
        return int(out[0]) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def ask(base_url: str, prompt: str, max_tokens: int) -> dict[str, Any]:
    import httpx

    body = {"model": "local", "messages": [{"role": "user", "content": prompt}], "temperature": 0.0, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": False}
    r = httpx.post(f"{base_url}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    return r.json().get("timings", {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--arms", default="baseline,ngram-mod")
    ap.add_argument("--reps", type=int, default=3, help="requests per prompt kind per arm")
    ap.add_argument("--max-tokens", type=int, default=700)
    ap.add_argument("--ctx", type=int, default=16384)
    ap.add_argument("--port", type=int, default=8190)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    from services.orchestrator.inference.gpu import GpuBusyError, snapshot
    from services.orchestrator.inference.llamacpp import LlamaCpp, LlamaServer

    model = Path(args.model)
    exe = LlamaCpp().server_exe
    src = (ROOT / "hardware" / "examples" / "up_down_counter" / "rtl" / "up_down_counter.sv").read_text(encoding="utf-8")
    prompts = {"generate": GENERATE, "edit": EDIT_TEMPLATE.format(src=src)}
    snap = snapshot()
    results: dict[str, Any] = {}
    for name in [a.strip() for a in args.arms.split(",") if a.strip()]:
        cfg = arm_config(name, model, args.ctx, args.port)
        server = LlamaServer(exe, cfg)
        try:
            server.start()
        except GpuBusyError as exc:
            results[name] = {"skipped": f"GPU not free: {str(exc)[:160]}"}
            print(f"{name}: skipped, {results[name]['skipped']}", flush=True)
            continue
        except (ValueError, RuntimeError) as exc:
            results[name] = {"error": str(exc)[:200]}
            print(f"{name}: failed to start: {exc}", flush=True)
            continue
        try:
            ask(server.base_url, "Say OK.", 8)  # warm-up: weights paged in, graphs built
            row: dict[str, Any] = {"vram_used_mib": vram_used_mib(), "ctx": cfg.ctx, "kv": cfg.kv_type, "spec_type": cfg.spec_type}
            for kind, prompt in prompts.items():
                timings = [ask(server.base_url, prompt, args.max_tokens) for _ in range(args.reps)]
                row[kind] = summarize(timings)
                print(f"{name:<14} {kind:<9} {row[kind].get('decode_tok_s_median')} tok/s decode, "
                      f"acceptance {row[kind].get('draft_acceptance', '-')}", flush=True)
            results[name] = row
        finally:
            server.stop()
            time.sleep(3)  # let the driver release the VRAM before the next arm is checked against it

    base = results.get("baseline", {})
    for row in results.values():
        for kind in prompts:
            if kind in row and kind in base:
                row[kind]["speedup_vs_baseline"] = speedup(row[kind], base[kind])
    doc = {"schema": SCHEMA, "model": model.name, "gpu": snap.name, "gpu_total_mib": snap.total_mib, "reps": args.reps,
           "max_tokens": args.max_tokens, "temperature": 0.0, "thinking": "off", "arms": results,
           "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "note": "decode speed is llama-server's own predicted_per_second; one request at a time; same prompts for every arm"}
    out = ROOT / "benchmarks" / "results" / f"speed-{model.stem}{('-' + args.tag) if args.tag else ''}-{time.strftime('%Y-%m-%d')}.json"
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"wrote {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
