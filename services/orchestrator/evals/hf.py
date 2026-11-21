# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Hugging Face model (optionally with a LoRA adapter) as an evaluation `LLM`, so a fine-tuned model is graded by the same
harness, grader and result schema as everything else.

Needs `torch`, `transformers` and `peft`, which the main install does not require (`pip install torch transformers peft`);
the imports are inside the functions, so the rest of SiliconForge works without them.

Sampling is batched: the harness asks for one sample at a time, but `n` samples of the same prompt are drawn in one forward
pass the first time a prompt is seen and handed out one by one, which is what makes `--n 8` affordable on a small GPU.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from services.orchestrator.evals.arms import LLM, LLMReply


class HFModel:
    def __init__(self, model_id: str, adapter: str | None = None, max_new_tokens: int = 768, batch: int = 8,
                 dtype: str = "bfloat16") -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model_id)
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=getattr(torch, dtype)).to("cuda")
        if adapter:
            from peft import PeftModel

            self.model = PeftModel.from_pretrained(self.model, adapter)
            self.model = self.model.merge_and_unload()  # no adapter overhead at generation time
        self.model.eval()
        self.max_new_tokens, self.batch = max_new_tokens, batch
        self._pool: dict[tuple, deque] = defaultdict(deque)
        self.model_id, self.adapter = model_id, adapter

    def prompt_ids(self, system: str, user: str):
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        text = self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        return self.tok(text, return_tensors="pt").to("cuda")

    def _draw(self, system: str, user: str, temperature: float, count: int) -> list[tuple[str, int, int, float]]:
        torch = self.torch
        enc = self.prompt_ids(system, user)
        n_prompt = int(enc["input_ids"].shape[1])
        out: list[tuple[str, int, int, float]] = []
        remaining = count
        while remaining > 0:
            k = min(self.batch, remaining)
            t0 = time.perf_counter()
            kw: dict[str, Any] = {"max_new_tokens": self.max_new_tokens, "pad_token_id": self.tok.pad_token_id, "num_return_sequences": k}
            if temperature > 0:
                kw.update(do_sample=True, temperature=temperature, top_p=0.95)
            else:
                kw.update(do_sample=False, num_return_sequences=1)
                k = 1
            with torch.no_grad():
                gen = self.model.generate(**enc, **kw)
            dt = (time.perf_counter() - t0) / max(1, gen.shape[0])
            for row in gen:
                new = row[n_prompt:]
                text = self.tok.decode(new, skip_special_tokens=True)
                out.append((text, n_prompt, int((new != self.tok.pad_token_id).sum()), dt))
            remaining -= k
        return out

    def llm(self, n_hint: int = 1) -> LLM:
        """The `LLM` callable. With `n_hint` > 1 the first call for a prompt draws that many samples at once."""

        def call(system: str, user: str, temperature: float) -> LLMReply:
            key = (system, user, round(temperature, 3))
            pool = self._pool[key]
            if not pool:
                pool.extend(self._draw(system, user, temperature, n_hint if temperature > 0 else 1))
            text, pt, ct, dt = pool.popleft()
            return LLMReply(text, pt, ct, dt)

        return call
