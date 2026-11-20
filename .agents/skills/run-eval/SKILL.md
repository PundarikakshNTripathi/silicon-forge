---
name: run-eval
description: Run the design eval suite across models/backends and record results as a committed artifact (pass rate, cost, latency).
---
1. `python -m services.orchestrator.main eval --suite hardware/examples --profile <local|hybrid|cloud> --backend <simulated|wsl>`.
2. Results are written to `benchmarks/results/<date>-<profile>.json`; commit the JSON, not raw run directories.
3. README numbers must cite these files; regenerate plots with `python scripts/make_plots.py`.
