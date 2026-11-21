# SiliconForge architecture

SiliconForge is a deterministic pipeline in which **tool verdicts, not language models, decide every edge**. Models
propose artifacts (a design contract, RTL, formal properties); guardrails, SymbiYosys, Yosys and OpenROAD accept or
reject them, and rejections come back to the model as precise, localized diagnostics.

<p align="center"><img src="assets/diagrams/flow.svg" width="900" alt="The flow from spec to signoff with repair loops"></p>

<p align="center"><img src="assets/diagrams/system.svg" width="900" alt="Surfaces, core, model router and EDA runner"></p>

## 1. Contract first

The architect agent emits a `DesignContract` (ports, widths, directions, clock and reset discipline, budgets). It is
validated by a pydantic schema (`schemas.py`), including that the clock and reset are declared 1-bit inputs. Invalid
output is rejected and re-requested with the exact validation error, escalating to a stronger model tier on retry.
Every later stage is checked against this contract.

## 2. Guardrail gates

| Gate | Rules | Implementation |
| :--- | :--- | :--- |
| 1 Port contract | `SF-GRD-101..103` module present, every port name, direction and numeric width | regex over the module header |
| 2 Lint | `SF-RTL-001..006` (`#delay`, `initial`, blocking/non-blocking misuse, latch risk) | C++ `sf-telemetry` (Python fallback) plus Verilator `--lint-only` |
| 3 PDK cells | `SF-GRD-301` instantiated cells must match the PDK allowlist | `configs/pdks/*.yaml` |
| 4 Flow variables | `SF-GRD-401..403` unknown variable, commercial Tcl, clock below the PDK floor | validated against LibreLane's real variable names |
| 5 Formal vacuity | `SF-GRD-501..504` grounded signals (also inside expressions), cover reached, no front-end error | `audit_formal_ir`, `audit_formal_result` |
| 6 LEC | `SF-GRD-601` RTL equals the synthesized netlist | `audit_lec` over a Yosys `equiv_*` run |

Known limits: gate 1 and gate 2 use pattern matching, not a full SystemVerilog parser (a `pyslang` front end is on the
roadmap). Every repair, including repairs made inside the formal loop, is re-audited.

## 3. Formal loop

The formal agent never writes SVA. It emits a **typed property IR** (`RESET_VALUE`, `INVARIANT`, `HANDSHAKE_STABILITY`,
`MUTUAL_EXCLUSION`, `COMBINATIONAL_EQUIVALENCE`, `LATENCY_BOUND`, `COVER_REACHABILITY`) that `FormalIRCompiler` lowers to
yosys-compatible immediate assertions framed by `f_past_valid`. SymbiYosys runs three tasks per design:

- `bmc` with `smtbmc bitwuzla` for bounded counterexamples,
- `prove` with `abc pdr` (IC3) for unbounded proofs that plain k-induction often cannot find,
- `cover` to prove the properties are not vacuous.

On failure the real `trace.vcd` is parsed to distill the failing property, the violating cycle and the input stimulus,
and a backward cone-of-influence slice of the RTL (`guardrails/rtl_slicer.py`) is handed to the RTL agent so repairs
stay surgical. Clocked immediate assertions are reported one step after the offending state; the distiller accounts for
that.

## 4. Execution backends and provenance

`EDABridge` selects a backend from `eda_mode`:

| Backend | Runs | Notes |
| :--- | :--- | :--- |
| `simulated` | surrogate models | deterministic, for demos and CI; every result is labelled `simulated` |
| `native` | tools on `PATH` | |
| `wsl` | tools inside a WSL2 distro | Windows development path; P&R runs on the distro's ext4 filesystem |
| `docker` | tools in a container | formal, LEC and lint |

All real backends are one `RealBackend` over a `Runner` (`NativeRunner`, `WslRunner`, `DockerRunner`); provenance is
the runner's name. A missing tool raises `ToolMissingError`; there is no silent fallback. Place-and-route drives
LibreLane and reads `metrics.json` (per-corner setup slack, standard-cell area, power, DRC, LVS) plus the real GDSII.

## 5. PPA optimization

`HybridPpaOptimizer` combines a clipped-PPO-style policy update with UCB exploration and a Pareto archive over a
discrete recipe space whose keys are real LibreLane variables (`SYNTH_STRATEGY`, `FP_CORE_UTIL`,
`PL_TARGET_DENSITY_PCT`, `GRT_ADJUSTMENT`, ...). Because real episodes take minutes it supports batched parallel
episodes, early stopping once a signoff-clean recipe is found, an on-disk result cache keyed by
hash(RTL, knobs, PDK, backend), and optional clock-relaxed actions that report the fastest clock that closes.
Signoff defaults to the worst timing corner; select `tt`/`ss`/`ff` in settings if you need a specific one.

## 6. Inference layer

`ModelRouter` maps a task to a tier and the active profile's ordered `(provider, model)` chain (`configs/models.yaml`).
It falls through on transient errors, refusals and open circuit breakers, prices each call with cache-aware rates,
enforces USD, total-token and per-stage token budgets, and serves low-temperature repeats from a persistent SQLite
cache. Providers: OpenAI-compatible (llama.cpp, OpenAI, Gemini, Ollama, with streaming, time-to-first-token, JSON-schema
decoding, prefix-cache accounting), Anthropic (official SDK, prompt caching, refusal handling), an opt-in CLI bridge,
and a replay provider. See [docs/providers.md](docs/providers.md).

## 7. Surfaces

- **API** (`services/orchestrator/api`): FastAPI + SQLite. Runs execute on a worker pool; every pipeline event and model
  call is persisted and streamed over SSE (resumable). Optional bearer token for the desktop app.
- **Web UI** (`services/dashboard`): React. The pipeline strip, verification waveform, layout viewer, agent log, cost and
  model management pages all derive from the event stream.
- **Desktop** (`apps/desktop`): Electron shell that supervises a PyInstaller-frozen backend on a random localhost port
  with a per-launch token.
- **TUI** (`services/tui`): Textual app running the pipeline in-process.
- **CLI**: `siliconforge run | eval | doctor | models | serve | tui`.
- **Agent harness**: `AGENTS.md` and per-tool pointer files.

## 8. Telemetry engine

`services/telemetry_engine` is a small C++20 library and CLI (`sf-telemetry`) that parses Yosys/OpenSTA/OpenROAD logs
and lints RTL. It handles real tool formats (including multi-corner slack). Measured honestly, spawning it per call
costs ~8 ms against ~0.04 ms for in-process Python regex (`benchmarks/results/telemetry-*.json`), so it is an optional
accelerator and rule set, not a speed win in its current subprocess form. A persistent server mode or bindings are the
route to making the native path pay off.

## 9. What is and is not verified

See the README status table. In short: formal, LEC, lint and place-and-route are real on the `native`/`wsl` backends and
are covered by tests against real tools; the `simulated` backend is a surrogate; the Docker backend and the subscription
CLI bridge have unit-tested wiring but no end-to-end run on the reference machine.
