#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-platform developer tasks for SiliconForge (Windows / Linux / macOS).

Usage:
    python scripts/dev.py build-cpp     # build sf-telemetry with CMake (MSVC on Windows)
    python scripts/dev.py test          # C++ ctest + Python pytest
    python scripts/dev.py lint          # ruff
    python scripts/dev.py demo          # run the simulated demo flow
    python scripts/dev.py doctor        # report toolchain availability
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPP_DIR = ROOT / "services" / "telemetry_engine"
BUILD_DIR = CPP_DIR / "build"
IS_WIN = platform.system() == "Windows"


def _vcvars() -> Path | None:
    """Locate vcvars64.bat for the newest Visual Studio install (Windows only)."""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates: list[Path] = []
    for base in (Path(pf) / "Microsoft Visual Studio", Path(pf86) / "Microsoft Visual Studio"):
        if base.exists():
            candidates += sorted(base.glob("*/*/VC/Auxiliary/Build/vcvars64.bat"), reverse=True)
    return candidates[0] if candidates else None


def _run(cmd: list[str] | str, cwd: Path = ROOT, shell: bool = False) -> int:
    print(f"$ {cmd if isinstance(cmd, str) else ' '.join(cmd)}", flush=True)
    return subprocess.call(cmd, cwd=cwd, shell=shell)


def build_cpp() -> int:
    gen = ["-G", "Ninja"] if shutil.which("ninja") else []
    cfg = f"cmake -S . -B build {' '.join(gen)} -DCMAKE_BUILD_TYPE=Release"
    steps = f"{cfg} && cmake --build build"
    if IS_WIN and not shutil.which("cl"):
        vc = _vcvars()
        if vc is None:
            print("error: MSVC not found. Install 'Desktop development with C++' (Visual Studio).")
            return 1
        return _run(f'cmd /c ""{vc}" >nul && {steps}"', cwd=CPP_DIR, shell=True)
    return _run(steps, cwd=CPP_DIR, shell=True)


def test_cpp() -> int:
    if not BUILD_DIR.exists() and build_cpp() != 0:
        return 1
    return _run("ctest --test-dir build --output-on-failure", cwd=CPP_DIR, shell=True)


def test_py() -> int:
    return _run([sys.executable, "-m", "pytest", "-q"])


def lint() -> int:
    return _run([sys.executable, "-m", "ruff", "check", "services", "tests", "scripts"])


def demo() -> int:
    return _run([
        sys.executable, "-m", "services.orchestrator.main",
        "run", "--spec", "hardware/examples/axi_lite_alu/spec.md", "--backend", "simulated",
        "--inject-bug",
    ])


def doctor() -> int:
    tools = [
        "python", "uv", "node", "pnpm", "cmake", "ninja", "cl", "g++", "git", "gh", "docker",
        "wsl", "yosys", "sby", "verilator", "iverilog", "openroad", "klayout", "llama-server",
        "nvidia-smi",
    ]
    print(f"platform: {platform.platform()}  python: {platform.python_version()}")
    for t in tools:
        print(f"  {'ok ' if shutil.which(t) else '-- '} {t:14s} {shutil.which(t) or ''}")
    exe = "sf-telemetry.exe" if IS_WIN else "sf-telemetry"
    print(f"  {'ok ' if (BUILD_DIR / exe).exists() else '-- '} {exe} (built engine)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("task", choices=["build-cpp", "test", "test-cpp", "test-py", "lint", "demo", "doctor"])
    task = p.parse_args().task
    if task == "build-cpp":
        return build_cpp()
    if task == "test-cpp":
        return test_cpp()
    if task == "test-py":
        return test_py()
    if task == "test":
        return test_cpp() or test_py()
    if task == "lint":
        return lint()
    if task == "demo":
        return demo()
    return doctor()


if __name__ == "__main__":
    sys.exit(main())
