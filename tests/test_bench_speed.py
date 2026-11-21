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
