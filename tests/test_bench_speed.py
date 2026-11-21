# SPDX-License-Identifier: AGPL-3.0-or-later
"""The speed benchmark's arithmetic and arm table, tested without a server or a GPU."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def load():
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "bench_speed.py"
    spec = importlib.util.spec_from_file_location("bench_speed_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_summarize_takes_medians_and_overall_acceptance():
    b = load()
    s = b.summarize([
        {"predicted_per_second": 40.0, "prompt_per_second": 900, "predicted_n": 100, "draft_n": 80, "draft_n_accepted": 60},
        {"predicted_per_second": 50.0, "prompt_per_second": 1100, "predicted_n": 100, "draft_n": 20, "draft_n_accepted": 20},
        {"predicted_per_second": 60.0, "prompt_per_second": 1000, "predicted_n": 100},
    ])
    assert s["decode_tok_s_median"] == 50.0 and (s["decode_tok_s_min"], s["decode_tok_s_max"]) == (40.0, 60.0)
    assert s["draft_acceptance"] == 0.8 and s["drafted"] == 100 and s["tokens_generated"] == 300  # pooled, not averaged per request
    assert "draft_acceptance" not in b.summarize([{"predicted_per_second": 30.0}])  # no drafting, no acceptance figure
    assert b.summarize([]) == {"samples": 0}


def test_speedup_is_a_ratio_of_medians_and_none_when_missing():
    b = load()
    assert b.speedup({"decode_tok_s_median": 60.0}, {"decode_tok_s_median": 40.0}) == 1.5
    assert b.speedup({}, {"decode_tok_s_median": 40.0}) is None


def test_arm_table_builds_valid_configs_and_rejects_unknown_names(tmp_path):
    b = load()
    m = tmp_path / "m.gguf"
    assert b.arm_config("baseline", m, 16384, 8190).spec_type == "none"
    assert b.arm_config("kv-f16", m, 16384, 8190).kv_type == "f16"
    assert b.arm_config("ctx-32k", m, 16384, 8190).ctx == 32768
    ng = b.arm_config("ngram-mod", m, 16384, 8190)
    assert ng.spec_type == "ngram-mod" and ng.reasoning == "off"
    d = b.arm_config(f"draft:{tmp_path / 'd.gguf'}", m, 16384, 8190)
    assert d.spec_type == "draft-simple" and d.draft_model.name == "d.gguf"
    for cfg in (ng, d):
        cfg.validate()
    with pytest.raises(SystemExit):
        b.arm_config("turbo", m, 16384, 8190)


def test_every_prompt_is_distinct_so_ngram_speculation_cannot_replay_an_earlier_answer():
    b = load()
    prompts = b.build_prompts(Path(__file__).resolve().parents[1])
    assert set(prompts) == {"generate", "edit"} and len(prompts["generate"]) == len(prompts["edit"]) >= 4
    for kind, items in prompts.items():
        assert len(set(items)) == len(items), f"{kind} prompts repeat"
    assert not set(prompts["generate"]) & set(prompts["edit"])


def test_a_label_suffix_reruns_the_same_configuration(tmp_path):
    b = load()
    m = tmp_path / "m.gguf"
    again = b.arm_config("baseline@end", m, 16384, 8190)
    assert again.kv_type == b.arm_config("baseline", m, 16384, 8190).kv_type and again.spec_type == "none"
    assert b.arm_config("ngram-mod@2", m, 16384, 8190).spec_type == "ngram-mod"


def test_identity_counts_compare_prompt_by_prompt():
    b = load()
    h = [b.output_hash(t) for t in ("a", "b", "c")]
    assert b.identical_count(h, h) == {"identical": 3, "of": 3}
    assert b.identical_count(h, [h[0], b.output_hash("x"), h[2]]) == {"identical": 2, "of": 3}
    assert b.output_hash("a") == b.output_hash("a") != b.output_hash("b")
