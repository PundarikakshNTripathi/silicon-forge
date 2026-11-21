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
