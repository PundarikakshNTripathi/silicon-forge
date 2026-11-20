#!/usr/bin/env bash
set -euo pipefail

echo "[SiliconForge] Building C++20 telemetry & static RTL linter..."
make -C services/telemetry_engine
make -C services/telemetry_engine test

echo "[SiliconForge] Installing Python package (editable)..."
python3 -m pip install --quiet -e ".[dev]" || echo "[Info] Offline/sandbox mode: run with PYTHONPATH=. python3 ..."
echo "[SiliconForge] Setup complete."
