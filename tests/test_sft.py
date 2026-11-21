# SPDX-License-Identifier: AGPL-3.0-or-later
"""SFT data preparation: the loss is on the answer only, templates are checked, over-long examples are dropped, batches pad safely."""
import json

import pytest

from services.orchestrator.evals.sft import (
    IGNORE,
    build_dataset,
    build_example,
    dataset_fingerprint,
    load_examples,
    pad_batch,
)


class StubTok:
    """Word-level stand-in with a ChatML-like template: `<|s|>role\ncontent<|e|>` per message."""

    pad_token_id = 0

    def __init__(self):
        self.vocab = {}

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False):
        text = "".join(f"<|s|>{m['role']}\n{m['content']}<|e|>\n" for m in messages)
        return text + ("<|s|>assistant\n" if add_generation_prompt else "")

    def __call__(self, text, add_special_tokens=False, **kw):
        ids = [self.vocab.setdefault(w, len(self.vocab) + 1) for w in text.replace("\n", " \n ").split(" ") if w]
        return {"input_ids": ids}


MSGS = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "write a module"},
        {"role": "assistant", "content": "module m ; endmodule"}]


def test_only_answer_tokens_carry_loss_and_the_prompt_is_masked():
    tok = StubTok()
    ex = build_example(tok, MSGS, 200)
    prompt_len = len(tok(tok.apply_chat_template(MSGS[:-1], add_generation_prompt=True))["input_ids"])
    assert ex["labels"][:prompt_len] == [IGNORE] * prompt_len
    assert all(t != IGNORE for t in ex["labels"][prompt_len:]) and len(ex["labels"]) == len(ex["input_ids"])
    assert ex["labels"][prompt_len:] == ex["input_ids"][prompt_len:]  # the unmasked labels are the answer tokens themselves
    assert prompt_len < len(ex["input_ids"])


def test_an_example_whose_prompt_leaves_no_room_is_dropped_not_truncated():
    tok = StubTok()
    assert build_example(tok, MSGS, 8) is None
    data, dropped = build_dataset(tok, [{"messages": MSGS}] * 3, 8)
    assert data == [] and dropped == 3


def test_a_template_that_does_not_extend_the_prompt_is_rejected():
    class Odd(StubTok):
        def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False):
            return "X" + super().apply_chat_template(messages, tokenize, add_generation_prompt) if not add_generation_prompt else "Y"

    with pytest.raises(ValueError, match="does not extend"):
        build_example(Odd(), MSGS, 200)


def test_truncation_keeps_labels_aligned_with_ids():
    tok = StubTok()
    long = MSGS[:2] + [{"role": "assistant", "content": " ".join(["token"] * 300)}]
    ex = build_example(tok, long, 60)
    assert len(ex["input_ids"]) == len(ex["labels"]) == 60


def test_padding_masks_labels_and_attention_and_keeps_the_longest_intact():
    a = {"input_ids": [5, 6, 7], "labels": [IGNORE, 6, 7]}
    b = {"input_ids": [5, 6], "labels": [IGNORE, 6]}
    out = pad_batch([a, b], pad_id=0)
    assert out["input_ids"] == [[5, 6, 7], [5, 6, 0]]
    assert out["labels"] == [[IGNORE, 6, 7], [IGNORE, 6, IGNORE]]
    assert out["attention_mask"] == [[1, 1, 1], [1, 1, 0]]


def test_loading_validates_roles_and_the_fingerprint_changes_with_content(tmp_path):
    good = tmp_path / "g.jsonl"
    good.write_text(json.dumps({"messages": MSGS}) + "\n")
    rows = load_examples(good)
    assert len(rows) == 1 and dataset_fingerprint(rows) == dataset_fingerprint(load_examples(good))
    other = [{"messages": [*MSGS[:2], {"role": "assistant", "content": "different"}]}]
    assert dataset_fingerprint(other) != dataset_fingerprint(rows)
    bad = tmp_path / "b.jsonl"
    bad.write_text(json.dumps({"messages": MSGS[:2]}) + "\n")
    with pytest.raises(ValueError, match="system/user/assistant"):
        load_examples(bad)
