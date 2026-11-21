#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure local inference speed for several llama-server configurations on this machine.

Each arm starts its own server (refusing, as always, to start if another program holds the VRAM), sends the same prompts with
temperature 0 and thinking off, and records decode and prompt tokens per second from llama-server's own `timings`, draft
acceptance when speculation is on, and VRAM in use once the model is loaded. Two kinds of prompt are used, because
speculative decoding helps one and not the other:

* `generate`: write a module from the spec of one of the bundled designs (new text: little to copy);
* `edit`: rewrite one of the bundled RTL files adding comments (most of the output repeats the prompt).

**Every prompt is sent exactly once per arm.** An earlier version sent one prompt three times; llama.cpp's n-gram speculation
keeps a pool that is shared across requests, so the repeats were drafted from the previous answer (100% acceptance, 375 tok/s on
a fresh-generation prompt) and the speed-up was an artifact. With distinct prompts the pool can only help when the text really
repeats.

Arms: `baseline`, `kv-f16`, `kv-q4_0`, `ctx-4k`, `ctx-32k`, `ngram-mod`, `ngram-simple`, `draft:<path/to/draft.gguf>`.

Usage:
    python benchmarks/bench_speed.py --model models/gguf/Qwen3.5-9B-Q4_K_M.gguf --arms baseline,ngram-mod,kv-q4_0
Writes benchmarks/results/speed-<model>-<date>.json (file names only, no local paths).
"""
from __future__ import annotations

import argparse
import hashlib
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
GENERATE_TEMPLATE = ("Write a synthesizable SystemVerilog module that implements this specification. Reply with the module only.\n\n"
                     "{spec}")
EDIT_TEMPLATE = ("Here is a SystemVerilog file:\n\n```systemverilog\n{src}\n```\n\nRewrite the whole file with a one-line comment "
                 "above every always block and every assign, without changing behaviour. Output the complete file in one code "
                 "block and nothing else.")


def build_prompts(root: Path) -> dict[str, list[str]]:
    """One generate prompt and one edit prompt per bundled example design (four of each), all distinct."""
    out: dict[str, list[str]] = {"generate": [], "edit": []}
    for d in sorted((root / "hardware" / "examples").iterdir()):
        spec, rtl = d / "spec.md", next(iter(sorted((d / "rtl").glob("*.sv"))), None)
        if not spec.is_file() or rtl is None:
            continue
        out["generate"].append(GENERATE_TEMPLATE.format(spec=spec.read_text(encoding="utf-8").strip()))
        out["edit"].append(EDIT_TEMPLATE.format(src=rtl.read_text(encoding="utf-8").strip()))
    return out


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
    """(ServerConfig for the arm). Unknown arm names are an error. `name@label` is the same configuration run again under another
    label (e.g. `baseline@end`), which shows drift in the machine's own speed over the course of a sweep."""
    from services.orchestrator.inference.llamacpp import ServerConfig

    name = name.split("@", 1)[0]
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


def output_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def identical_count(hashes: list[str], reference: list[str]) -> dict[str, int]:
    """How many of an arm's outputs match the reference arm's, prompt for prompt."""
    return {"identical": sum(a == b for a, b in zip(hashes, reference, strict=False)), "of": min(len(hashes), len(reference))}


def ask(base_url: str, prompt: str, max_tokens: int) -> tuple[dict[str, Any], str]:
    """(llama-server timings, answer text)."""
    import httpx

    body = {"model": "local", "messages": [{"role": "user", "content": prompt}], "temperature": 0.0, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": False}
    r = httpx.post(f"{base_url}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    data = r.json()
    return data.get("timings", {}), data["choices"][0]["message"].get("content") or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--arms", default="baseline,ngram-mod")
    ap.add_argument("--max-tokens", type=int, default=700)
    ap.add_argument("--ctx", type=int, default=16384)
    ap.add_argument("--port", type=int, default=8190)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    from services.orchestrator.inference.gpu import GpuBusyError, snapshot
    from services.orchestrator.inference.llamacpp import LlamaCpp, LlamaServer

    model = Path(args.model)
    exe = LlamaCpp().server_exe
    prompts = build_prompts(ROOT)
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
            # warm-up: weights paged in, graphs built, GPU clocks up. A neutral prompt, so it does not seed the n-gram pool
            # with RTL text, and long enough to reach steady state (the first arm of an earlier sweep ran 25% slow without it)
            ask(server.base_url, "Describe, in plain prose, how a silicon wafer becomes a packaged chip.", 200)
            ask(server.base_url, "Explain what a flip-flop is and why timing matters, in about two hundred words.", 200)
            row: dict[str, Any] = {"vram_used_mib": vram_used_mib(), "ctx": cfg.ctx, "kv": cfg.kv_type, "spec_type": cfg.spec_type}
            for kind, plist in prompts.items():
                answers = [ask(server.base_url, p, args.max_tokens) for p in plist]  # each distinct prompt exactly once
                row[kind] = summarize([t for t, _ in answers])
                row[kind]["output_hashes"] = [output_hash(text) for _, text in answers]
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
                # greedy (temperature 0) answers compared with the baseline's, prompt by prompt. The baseline's own re-run
                # (`baseline@...`) is the control: if that differs too, the difference is not speculation's doing.
                row[kind]["same_output_as_baseline"] = identical_count(row[kind]["output_hashes"], base[kind]["output_hashes"])
    doc = {"schema": SCHEMA, "model": model.name, "gpu": snap.name, "gpu_total_mib": snap.total_mib,
           "prompts_per_kind": {k: len(v) for k, v in prompts.items()}, "max_tokens": args.max_tokens, "temperature": 0.0,
           "thinking": "off", "arms": results, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "note": "decode speed is llama-server's own predicted_per_second; one request at a time; the same distinct prompts "
                   "for every arm, each sent once (repeating a prompt inflates n-gram speculation)"}
    out = ROOT / "benchmarks" / "results" / f"speed-{model.stem}{('-' + args.tag) if args.tag else ''}-{time.strftime('%Y-%m-%d')}.json"
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"wrote {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
