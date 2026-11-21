# SPDX-License-Identifier: AGPL-3.0-or-later
"""llama-server argument construction (no server is started, no model file is read)."""
from pathlib import Path

from services.orchestrator.inference.llamacpp import ServerConfig


def test_a_relative_model_path_becomes_absolute_because_the_server_runs_elsewhere():
    argv = ServerConfig(model_path=Path("models") / "gguf" / "m.gguf").argv(Path("llama-server"))
    given = Path(argv[argv.index("-m") + 1])
    assert given.is_absolute() and given.name == "m.gguf"
