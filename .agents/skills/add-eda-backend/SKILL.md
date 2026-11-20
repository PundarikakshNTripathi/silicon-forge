---
name: add-eda-backend
description: Add or modify an EDA execution backend (native, wsl, docker, simulated). Use when wiring Yosys/sby/OpenROAD/LibreLane into the pipeline.
---
1. Implement the backend interface in `services/orchestrator/eda/backends/` (formal, synth+LEC, pnr, sim).
2. Every returned result must set `mode` to the backend name; a missing tool raises `ToolMissingError`: never fall back silently.
3. Parse real outputs (sby VCD/logs, LibreLane `metrics.json`) into the typed models in `state.py`; add fixtures from real runs under `tests/fixtures/`.
4. Mark real-tool tests `@pytest.mark.real_eda`; keep the simulated path covering CI.
5. Register in `eda/backends/__init__.py` and `scripts/dev.py doctor`.
