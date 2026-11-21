# SPDX-License-Identifier: AGPL-3.0-or-later
"""llama-server argument construction (no server is started, no model file is read)."""
from pathlib import Path

from services.orchestrator.inference.llamacpp import ServerConfig


def test_a_relative_model_path_becomes_absolute_because_the_server_runs_elsewhere():
    argv = ServerConfig(model_path=Path("models") / "gguf" / "m.gguf").argv(Path("llama-server"))
    given = Path(argv[argv.index("-m") + 1])
    assert given.is_absolute() and given.name == "m.gguf"


def test_reasoning_flag_is_passed_only_when_set():
    base = Path("models") / "m.gguf"
    exe = Path("llama-server")
    assert "--reasoning" not in ServerConfig(model_path=base).argv(exe)  # auto: the model's own default
    off = ServerConfig(model_path=base, reasoning="off").argv(exe)
    assert off[off.index("--reasoning") + 1] == "off"
    assert ServerConfig(model_path=base, reasoning="on").argv(exe).count("--reasoning") == 1


def test_speculative_flags_use_the_names_this_llama_cpp_build_accepts(tmp_path):
    exe = Path("llama-server")
    main, draft = tmp_path / "big.gguf", tmp_path / "small.gguf"
    a = ServerConfig(model_path=main, spec_type="draft-simple", draft_model=draft, draft_max=8, draft_min=2,
                     draft_gpu_layers=99).argv(exe)
    assert a[a.index("--spec-type") + 1] == "draft-simple"
    assert Path(a[a.index("--spec-draft-model") + 1]).name == "small.gguf"
    assert a[a.index("--spec-draft-n-max") + 1] == "8" and a[a.index("--spec-draft-n-min") + 1] == "2"
    assert a[a.index("--spec-draft-ngl") + 1] == "99"
    ng = ServerConfig(model_path=main, spec_type="ngram-mod").argv(exe)
    assert ng[ng.index("--spec-type") + 1] == "ngram-mod" and "--spec-draft-model" not in ng
    assert "--spec-type" not in ServerConfig(model_path=main).argv(exe)  # off by default


def test_inconsistent_speculative_settings_are_refused_not_ignored(tmp_path):
    import pytest

    m = tmp_path / "m.gguf"
    with pytest.raises(ValueError, match="needs a draft_model"):
        ServerConfig(model_path=m, spec_type="draft-simple").argv(Path("s"))
    with pytest.raises(ValueError, match="spec_type is 'none'"):
        ServerConfig(model_path=m, draft_model=tmp_path / "d.gguf").argv(Path("s"))
    with pytest.raises(ValueError, match="spec_type must be one of"):
        ServerConfig(model_path=m, spec_type="bogus").argv(Path("s"))
    with pytest.raises(ValueError, match="parallel=1"):
        ServerConfig(model_path=m, spec_type="ngram-mod", parallel=2).argv(Path("s"))


def test_draft_weights_count_against_free_vram():
    from services.orchestrator.inference.gpu import GpuSnapshot, can_load

    snap = GpuSnapshot(name="test gpu", total_mib=8192, used_mib=1192)
    base = can_load(None, 8192, "q8_0", 1, 99, snap=snap, weights_mib=4900)
    assert base.ok
    with_draft = can_load(None, 8192, "q8_0", 1, 99, snap=snap, weights_mib=4900, draft_mib=900)
    assert with_draft.need_mib == base.need_mib + 900 and not with_draft.ok
