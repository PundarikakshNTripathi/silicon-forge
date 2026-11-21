# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resource root (read-only, bundled) versus data root (writable)."""
import importlib

from services.orchestrator import paths


def test_resource_root_override_and_default(monkeypatch, tmp_path):
    assert (paths.RESOURCE_ROOT / "configs").is_dir()  # source checkout
    monkeypatch.setenv("SILICONFORGE_RESOURCES", str(tmp_path))
    try:
        assert importlib.reload(paths).RESOURCE_ROOT == tmp_path
    finally:
        monkeypatch.delenv("SILICONFORGE_RESOURCES")
        importlib.reload(paths)


def test_eval_writes_under_the_data_root_not_the_resource_root(monkeypatch, tmp_path):
    """An installed wheel's resource root is read-only site-packages; results belong in the user's data directory."""
    from services.orchestrator import main

    monkeypatch.setenv("SILICONFORGE_HOME", str(tmp_path))
    rc = main.cli_main(["eval"])
    assert rc == 0
    written = list((tmp_path / "benchmarks" / "results").glob("*-default.json"))
    assert written, "eval did not write into SILICONFORGE_HOME"
    assert not (paths.RESOURCE_ROOT / "benchmarks" / "results" / written[0].name).exists()


def test_one_version_everywhere():
    """pyproject, the desktop shell and the API must agree; they drifted once (0.2.0 / 0.3.0)."""
    import json
    import tomllib

    from services.orchestrator.version import __version__

    root = paths.RESOURCE_ROOT
    py = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    desktop = json.loads((root / "apps" / "desktop" / "package.json").read_text(encoding="utf-8"))["version"]
    assert py == desktop == __version__
