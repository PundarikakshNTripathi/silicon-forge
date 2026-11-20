#!/usr/bin/env bash
set -euo pipefail

echo "================================================================"
echo " SiliconForge Autonomous Spec-to-RTL-to-GDSII Demo"
echo " (with deliberate initial bug to demonstrate Formal COI Repair)"
echo "================================================================"
PYTHONPATH=. python3 -m services.orchestrator.main \
  --spec hardware/examples/axi_lite_alu/spec.md \
  --config configs/flow_default.yaml \
  --inject-bug
