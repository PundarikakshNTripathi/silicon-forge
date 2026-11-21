#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""LoRA fine-tune of a small causal LM on verified samples (one round of rejection-sampling fine-tuning).

    python scripts/train_lora.py --model Qwen/Qwen2.5-Coder-1.5B-Instruct --data sft.jsonl --out adapters/rft1

Needs torch, transformers and peft (see docs/evals.md). It is a plain PyTorch loop: LoRA on every attention and MLP projection,
bf16 base weights with fp32 adapter weights, gradient checkpointing and gradient accumulation to fit a small GPU, loss on the
answer tokens only. The adapter and a `training.json` (hyperparameters, data fingerprint, losses, versions, wall time) are written
to --out. Adapters are not committed: they are derived from a model with its own license.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.orchestrator.evals.sft import (  # noqa: E402
    build_dataset,
    dataset_fingerprint,
    load_examples,
    pad_batch,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=1536)
    ap.add_argument("--batch", type=int, default=2, help="examples per step on the GPU")
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    import torch
    import transformers
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    rows = load_examples(Path(args.data))
    tok = AutoTokenizer.from_pretrained(args.model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    data, dropped = build_dataset(tok, rows, args.max_len)
    if not data:
        print("no trainable examples", file=sys.stderr)
        return 2
    answer_tokens = sum(sum(1 for t in e["labels"] if t != -100) for e in data)
    print(f"{len(data)} examples ({dropped} dropped for length), {answer_tokens} answer tokens, fingerprint {dataset_fingerprint(rows)}", flush=True)

    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.bfloat16).to("cuda")
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    cfg = LoraConfig(r=args.rank, lora_alpha=args.alpha, lora_dropout=0.05, task_type="CAUSAL_LM",
                     target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, cfg)
    for p in model.parameters():
        if p.requires_grad:
            p.data = p.data.float()  # adapter weights in fp32, base in bf16
    model.print_trainable_parameters()

    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    steps_per_epoch = math.ceil(len(data) / (args.batch * args.grad_accum))
    total = steps_per_epoch * args.epochs
    warm = max(1, int(0.05 * total))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / warm if s < warm else max(0.0, 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, total - warm)))))

    t0 = time.time()
    losses: list[float] = []
    model.train()
    step = 0
    for epoch in range(args.epochs):
        order = list(range(len(data)))
        random.shuffle(order)
        micro = [order[i:i + args.batch] for i in range(0, len(order), args.batch)]
        running, count = 0.0, 0
        for mi, idxs in enumerate(micro):
            b = pad_batch([data[i] for i in idxs], tok.pad_token_id)
            batch = {k: torch.tensor(v, device="cuda") for k, v in b.items()}
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = model(**batch).loss
            (loss / args.grad_accum).backward()
            running += float(loss.item())
            count += 1
            if (mi + 1) % args.grad_accum == 0 or mi == len(micro) - 1:
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                opt.step()
                sched.step()
                opt.zero_grad(set_to_none=True)
                step += 1
        losses.append(round(running / max(1, count), 4))
        print(f"epoch {epoch + 1}/{args.epochs}: mean loss {losses[-1]} ({time.time() - t0:.0f} s)", flush=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)
    (out / "training.json").write_text(json.dumps({
        "base_model": args.model, "examples": len(data), "dropped_for_length": dropped, "answer_tokens": answer_tokens,
        "data_fingerprint": dataset_fingerprint(rows), "epochs": args.epochs, "lr": args.lr, "rank": args.rank, "alpha": args.alpha,
        "batch": args.batch, "grad_accum": args.grad_accum, "max_len": args.max_len, "seed": args.seed, "optimizer_steps": step,
        "epoch_mean_loss": losses, "wall_s": round(time.time() - t0, 1),
        "versions": {"torch": torch.__version__, "transformers": transformers.__version__},
        "gpu": torch.cuda.get_device_name(0),
    }, indent=2), encoding="utf-8")
    print(f"saved adapter to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
