# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build hook: copy the runtime resources that live outside the Python packages into `services/_resources` so that a
wheel is self-contained (`pip install siliconforge` finds its configs, example designs and web UI).

Everything else is declared in pyproject.toml. The UI is optional: if `services/dashboard/dist` has not been built, the
wheel is still produced and the API runs without the web cockpit.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
BUNDLE = ["configs", "hardware/examples", "benchmarks/results", "services/dashboard/dist"]


class BuildPy(build_py):
    def run(self) -> None:
        target = ROOT / "services" / "_resources"
        if target.exists():
            shutil.rmtree(target)
        for rel in BUNDLE:
            src = ROOT / rel
            if src.is_dir():
                shutil.copytree(src, target / rel, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "runs"))
            else:
                print(f"note: {rel} not found, not bundled")
        (target).mkdir(exist_ok=True)
        (target / "__init__.py").write_text("", encoding="utf-8")
        super().run()


setup(cmdclass={"build_py": BuildPy})
