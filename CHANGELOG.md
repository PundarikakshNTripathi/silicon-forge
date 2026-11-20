# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/), versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Added
- **Real EDA backends** (`native` / `wsl` / `docker`) running Yosys, SymbiYosys (BMC + PDR + cover), Verilator and LibreLane (OpenROAD) behind one interface; every result carries `mode` provenance and missing tools raise instead of silently mocking.
- Real LibreLane place-and-route with real GDSII, multi-corner PPA from `metrics.json`, EDA result cache, parallel RL episodes with early stopping.
- Typed property IR: `MUTUAL_EXCLUSION`, `COMBINATIONAL_EQUIVALENCE`, `LATENCY_BOUND`; pydantic schemas validating all structured LLM output with feedback-and-retry.
- Guardrail gates 5 (formal vacuity) and 6 (LEC) as real gates; port-width and expression-level signal grounding checks.
- Three new example designs (`up_down_counter`, `sync_fifo`, `uart_tx`) verified against real SymbiYosys (good RTL proven, injected bugs caught).
- CLI subcommands: `run`, `eval`, `doctor`, `serve`; event streaming and cancellation in the pipeline.
- Benchmarks: measured C++ telemetry vs Python (`benchmarks/results/`).
- Repository foundation: LICENSE, contribution/security docs, CI, cross-platform dev tasks, agent harness files.
### Changed
- `MockChipDesignLLM` (hard-coded design) replaced by `ReplayProvider` serving bundled fixtures; unknown specs now fail loudly.
- Simulated flows no longer write a fake GDSII file.
- C++ unit tests use an always-on `CHECK` (the previous `assert()` was compiled out in Release, so they passed vacuously) and parse real Yosys/OpenSTA formats.
### Removed
- Unused prototype Tcl/`.sby`/LibreLane config files under `eda/` (superseded by the real backends).
### Fixed
- `pip install -e .` failed on setuptools flat-layout discovery.
- `sf-telemetry.exe` was never found on Windows.
