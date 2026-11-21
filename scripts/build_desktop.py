#!/usr/bin/env python3
"""Build the SiliconForge desktop app: UI -> C++ engine -> frozen Python backend (PyInstaller) -> Electron installer.

Usage:
    python scripts/build_desktop.py            # unpacked app + installers
    python scripts/build_desktop.py --dir      # unpacked app only (fast; used for the smoke test)
    python scripts/build_desktop.py --skip-ui --skip-cpp
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "apps" / "desktop"
SEP = ";" if os.name == "nt" else ":"
EXE = ".exe" if os.name == "nt" else ""


def run(cmd: list[str] | str, cwd: Path = ROOT, **kw) -> None:
    print(f"$ {cmd if isinstance(cmd, str) else ' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, shell=isinstance(cmd, str), **kw)


def build_ui() -> None:
    run("pnpm install --frozen-lockfile --ignore-scripts && pnpm run build", cwd=ROOT / "services" / "dashboard")


def build_cpp() -> None:
    run([sys.executable, "scripts/dev.py", "build-cpp"])


def freeze_backend() -> Path:
    out = ROOT / "build" / "pyinstaller"
    shutil.rmtree(out, ignore_errors=True)
    telemetry = ROOT / "services" / "telemetry_engine" / "build" / f"sf-telemetry{EXE}"
    data = [
        (ROOT / "configs", "configs"),
        (ROOT / "hardware" / "examples", "hardware/examples"),
        (ROOT / "services" / "orchestrator" / "skills" / "packs", "services/orchestrator/skills/packs"),
        (ROOT / "services" / "dashboard" / "dist", "services/dashboard/dist"),
        (ROOT / "benchmarks" / "results", "benchmarks/results"),
    ]
    if telemetry.exists():
        data.append((telemetry, "services/telemetry_engine/build"))
    cmd = [
        sys.executable, "-m", "PyInstaller", "services/orchestrator/backend_entry.py", "--name", "siliconforge-backend",
        "--onedir", "--noconfirm", "--clean", "--distpath", str(out / "dist"), "--workpath", str(out / "work"),
        "--specpath", str(out), "--console",
        "--collect-submodules", "uvicorn", "--collect-submodules", "keyring.backends",
        "--hidden-import", "anthropic", "--hidden-import", "httpx",
    ]
    for src, dst in data:
        cmd += ["--add-data", f"{src}{SEP}{dst}"]
    run(cmd)
    dist = DESKTOP / "backend-dist"
    shutil.rmtree(dist, ignore_errors=True)
    shutil.copytree(out / "dist" / "siliconforge-backend", dist)
    return dist


def package(dir_only: bool) -> None:
    if not (DESKTOP / "node_modules" / "electron" / "dist").exists():
        run("pnpm install", cwd=DESKTOP)
        run("node node_modules/electron/install.js", cwd=DESKTOP)
    run("pnpm exec electron-builder --dir" if dir_only else "pnpm exec electron-builder", cwd=DESKTOP)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="store_true", help="unpacked app only")
    ap.add_argument("--skip-ui", action="store_true")
    ap.add_argument("--skip-cpp", action="store_true")
    ap.add_argument("--skip-backend", action="store_true")
    args = ap.parse_args()
    if not args.skip_ui:
        build_ui()
    if not args.skip_cpp:
        build_cpp()
    if not args.skip_backend:
        freeze_backend()
    package(args.dir)
    print("done: see apps/desktop/dist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
