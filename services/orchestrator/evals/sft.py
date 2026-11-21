# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data preparation for supervised fine-tuning on verified samples. No torch here, so it is testable anywhere.

The loss is taken on the assistant's answer only. Training on the prompt as well teaches the model to reproduce problem
statements and wastes capacity; a mask bug here is silent, which is why this is a separate, tested function.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

IGNORE = -100


class Tokenizer(Protocol):
    def apply_chat_template(self, messages: list[dict[str, str]], tokenize: bool = ..., add_generation_prompt: bool = ...) -> str: ...
    def __call__(self, text: str, **kw: Any) -> Any: ...


def load_examples(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in rows:
        roles = [m["role"] for m in r["messages"]]
        if roles != ["system", "user", "assistant"]:
            raise ValueError(f"expected system/user/assistant messages, got {roles}")
    return rows


def dataset_fingerprint(rows: Iterable[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps(r["messages"], sort_keys=True).encode())
    return h.hexdigest()[:16]


def build_example(tok: Tokenizer, messages: list[dict[str, str]], max_len: int) -> dict[str, list[int]] | None:
    """Token ids and labels for one example. Prompt tokens get -100 so they carry no loss. Returns None when the prompt alone
    leaves no room for an answer within `max_len` (such an example would teach nothing and is dropped, not truncated)."""
    prompt = tok.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True)
    full = tok.apply_chat_template(messages, tokenize=False)
    if not full.startswith(prompt):
        raise ValueError("the chat template does not extend the prompt; cannot mask the prompt reliably")
    p_ids = list(tok(prompt, add_special_tokens=False)["input_ids"])
    f_ids = list(tok(full, add_special_tokens=False)["input_ids"])
    if f_ids[: len(p_ids)] != p_ids:
        raise ValueError("tokenizing the prompt and the full text disagree on the prompt tokens")
    if len(p_ids) >= max_len - 8:
        return None
    ids = f_ids[:max_len]
    labels = ([IGNORE] * len(p_ids) + f_ids[len(p_ids):])[:max_len]
    return {"input_ids": ids, "labels": labels}


def build_dataset(tok: Tokenizer, rows: list[dict[str, Any]], max_len: int) -> tuple[list[dict[str, list[int]]], int]:
    out, dropped = [], 0
    for r in rows:
        ex = build_example(tok, r["messages"], max_len)
        if ex is None:
            dropped += 1
        else:
            out.append(ex)
    return out, dropped


def pad_batch(examples: list[dict[str, list[int]]], pad_id: int) -> dict[str, list[list[int]]]:
    """Right-pad to the longest example in the batch; padded positions are masked from the loss and the attention."""
    width = max(len(e["input_ids"]) for e in examples)
    ids, labels, mask = [], [], []
    for e in examples:
        n = width - len(e["input_ids"])
        ids.append(e["input_ids"] + [pad_id] * n)
        labels.append(e["labels"] + [IGNORE] * n)
        mask.append([1] * len(e["input_ids"]) + [0] * n)
    return {"input_ids": ids, "labels": labels, "attention_mask": mask}
