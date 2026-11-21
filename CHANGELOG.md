# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/), versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Changed
- **License: AGPL-3.0-or-later** (was Apache-2.0 up to 0.3.0), with a commercial license available. SPDX headers on all source files, `THIRD_PARTY_NOTICES.md`, `docs/licensing.md`, `COMMERCIAL.md`, a contributor grant in `CONTRIBUTING.md`, and a "Source" link in the cockpit (AGPL section 13). CI checks headers and dependency licenses.
- Version 0.4.0 across the Python package and the desktop shell.
- **Installable wheel**: configs, example designs and the built web UI are bundled (`services/_resources`) and resolved through `paths.RESOURCE_ROOT`, so `pip install` works outside a source checkout.
### Fixed
- `llama-server` was started with a relative model path while running from its own directory, so the model could not be found; the path is now made absolute (this made the first GPU benchmark attempt fail).
### Added
- **Tiny Tapeout (IHP SG13CMOS5L) mode**: `siliconforge tt scaffold|check|synth|gls|pack|doctor|harden`; info.yaml and port validation, tile budget from real post-synthesis area (Yosys against the IHP liberty), and gate-level simulation of your cocotb tests with the PDK cell models. `docs/tiny-tapeout.md`.
### Fixed
- **`WslRunner` executed command substitutions inside arguments** (`$(cmd)` and backticks were expanded by a shell layer even inside single quotes), so an argument or file name could run commands, and `$VAR` in arguments was silently emptied. Arguments are now escaped; covered by a unit test and a real WSL round-trip test.
- **ISA toolchain** from one `isa.yaml`: assembler/disassembler, Verilog decoder with a formal one-hot proof, golden model, timing checker (static proof + trace), docs with bit-field SVGs, and a standalone bundle. `docs/isa-toolchain.md`.
- **Verification toolkit**: mutation testing with Yosys equivalence filtering, cocotb runner with seeds, lockstep co-simulation and functional coverage, toggle coverage, independent UART/SPI/I2C/WS2812 decoders; `siliconforge verify sim|mutate|toggle`; MCP tools `isa_check`, `run_cocotb`, `mutation_test`. `docs/verification.md`.
- `hardware/isa/pio16`: a worked example (ISA, RTL, cocotb lockstep tests, committed generated files that a test keeps in sync).
- **Workspaces and a tool-using agent**: open any folder, run `siliconforge chat` / `agent` in it, or use the new API endpoints; permission modes (ask, accept-edits, auto, plan), sandboxed file tools, an allowlisted `eda` tool with provenance, undo, and instruction files (`SILICONFORGE.md` / `AGENTS.md`). See `docs/workspaces.md`.
- **Enforced model policy**: a workspace can restrict the models the router may call (`policy.allowed_models`); an empty chain is an error, not a fallback.
- **AI-usage ledger** and `siliconforge report ai-usage`: metadata-only record of every model call and a disclosure report.
- **Gemini 3.8 Flash via the Antigravity CLI** (`cli_agy`; profiles `gemini-agy`, `dev`, `competition`), opt-in like the other CLI bridges.
- **GPU guard**: checks free VRAM before starting a local server and refuses (with options) instead of evicting another program; `siliconforge models detect`; model discovery that reuses GGUF files already on disk.
- Native folder dialog bridge in the desktop shell (`pickFolder`).
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
