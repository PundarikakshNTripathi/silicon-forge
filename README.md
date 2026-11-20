# SiliconForge — Autonomous Spec-to-RTL-to-GDSII Chip Design Agent

**SiliconForge** is a production-grade, multi-language, multi-agent ASIC design platform that autonomously transforms a natural-language hardware specification into verified synthesizable SystemVerilog, formally proven properties, and PPA-optimized physical layout (GDSII) using open-source EDA toolchains.

---

## Core Capabilities

| Subsystem | Language / Stack | What It Does |
| :--- | :--- | :--- |
| **Multi-Agent Orchestrator** | Python 3.11, FastAPI, Asyncio | Executes a deterministic DAG state machine: `Spec -> DesignContract -> RTL -> Guardrails -> Formal/Sim -> Synthesis/LEC -> P&R + RL PPA -> Signoff`. |
| **Hallucination Guardrails** | Python + C++20 Static Linter | 6-gate verification: Port-contract integrity, C++20 static RTL lint (latches, comb loops, blocking-in-seq, unsynthesizable constructs), PDK stdcell allowlist, OpenROAD/Yosys Tcl flag allowlist, SVA vacuity detection, and dependency-sliced minimal patching. |
| **Formal Verification Loop** | Python + SymbiYosys + Z3 | Compiles a **Typed Property IR** (never raw LLM-guessed SVA) into SymbiYosys `.sby` tasks (`bmc`, `prove` k-induction, `cover`), distills counterexample traces into localized signal slices, and enforces Yosys Logical Equivalence Checking (LEC) post-synthesis. |
| **RL & Evolutionary PPA Optimizer** | Python (NumPy) | Hybrid Contextual-Bandit / PPO-clipped policy + Evolutionary Pareto-front explorer tuning synthesis & physical design knobs (`SYNTH_STRATEGY`, `FP_CORE_UTIL`, `PL_TARGET_DENSITY`, `CLOCK_PERIOD`, `CTS_BUF_DISTANCE`, `GRT_ADJUSTMENT`) with multi-objective reward (WNS/TNS, area, power, DRC/LVS). |
| **Inference, Cost & Model Router** | Python | Tiered model routing (Local/Fast for log triage & formatting, Frontier Reasoning for architecture & counterexample root-cause), semantic prompt caching, per-stage USD/token ledger, and hard budget circuit breakers. |
| **Domain Skills & Hybrid RAG** | Python + Markdown Skill Packs | Grounded BM25/term-weighted retrieval over verified Sky130A/ASAP7 PDK rules, OpenROAD Tcl recipes, and SymbiYosys SVA patterns. |
| **EDA Telemetry & Static Engine** | C++20 (`sf-telemetry`) | Sub-millisecond native parser for OpenSTA timing reports, Yosys synthesis stats, OpenROAD area/power reports, Magic/KLayout DRC logs, and static SystemVerilog linting. |
| **Real-Time Cockpit UI** | TypeScript, React 18, Vite | Live pipeline DAG visualizer, PPA Pareto-front explorer, formal counterexample inspector, guardrail audit panel, and token/cost telemetry. |
| **Open-Source EDA Flow** | SystemVerilog, Tcl, YAML, Docker | Native integration with [LibreLane / OpenLane 2](https://github.com/The-OpenROAD-Project/openlane), Yosys, OpenROAD, OpenSTA, SymbiYosys, Verilator, Cocotb, Magic, Netgen, and KLayout for Sky130A and ASAP7 PDKs. |

---

## Folder Structure

```text
siliconforge/
├── README.md
├── ARCHITECTURE.md
├── Makefile
├── pyproject.toml
├── configs/
│   ├── flow_default.yaml              # Master pipeline, RL, guardrail, and budget config
│   ├── pdks/
│   │   ├── sky130A.yaml               # SkyWater 130nm PDK constraints & stdcell allowlist
│   │   └── asap7.yaml                 # ASAP7 7nm predictive PDK constraints & allowlist
│   └── prompts/                       # Principal-engineer-crafted system prompts
├── services/
│   ├── orchestrator/                  # Python 3.11 AI orchestrator, agents, RL, guardrails, EDA bridge
│   ├── telemetry_engine/              # C++20 native EDA log/PPA parser & static RTL linter
│   └── dashboard/                     # TypeScript + React 18 real-time monitoring cockpit
├── eda/
│   ├── tcl/                           # Production Yosys, OpenROAD, and LEC Tcl scripts
│   ├── formal/                        # SymbiYosys (.sby) formal verification templates
│   └── librelane/                     # LibreLane / OpenLane 2 reference configuration
├── hardware/
│   └── examples/axi_lite_alu/         # Complete reference design (Spec, SV RTL, SVA, Cocotb TB)
├── docker/                            # Multi-container production Dockerfiles & docker-compose.yml
├── scripts/                           # Setup and demo runners
└── tests/                             # Comprehensive Python unit & end-to-end integration tests
```

---

## Quickstart (Local — Works Immediately Without Heavy EDA Install)

SiliconForge includes a deterministic **MockEDA + MockLLM** execution mode so you can build, test, and run the entire Spec-to-GDSII pipeline (including the compiled C++20 telemetry engine, guardrails, formal IR compiler, and RL PPA optimizer) in seconds on any Linux/macOS machine.

```bash
# 1. Build the C++20 telemetry engine and verify the environment
make build-cpp

# 2. Run all unit & end-to-end integration tests (C++20 + Python)
make test

# 3. Run the autonomous Spec-to-GDSII demo on the reference AXI4-Lite ALU
make demo
```

### Production Mode (Real Open-Source EDA + Live LLM Providers)

1. Configure your LLM provider keys (supports OpenAI, Anthropic, Gemini, or local vLLM/Ollama):
   ```bash
   export OPENAI_API_KEY="sk-..."        # or ANTHROPIC_API_KEY / GEMINI_API_KEY
   export SILICONFORGE_EDA_MODE="docker" # "native" if Yosys/OpenROAD/sby are on PATH, or "docker"
   ```
2. Launch the full stack (Orchestrator API + EDA Worker Container + React Cockpit):
   ```bash
   docker compose -f docker/docker-compose.yml up --build
   ```
3. Open the real-time cockpit at `http://localhost:5173` and submit a hardware specification via the UI or REST API (`POST http://localhost:8000/api/v1/runs`).

---

## Research Foundations

SiliconForge incorporates proven techniques from 2025–2026 EDA and AI-for-Hardware literature:
- **Open-Source RTL-to-GDSII Infrastructure**: [OpenLane / LibreLane](https://github.com/The-OpenROAD-Project/openlane) and [Chipyard Sky130 + OpenROAD](https://chipyard.readthedocs.io/en/main/vlsi/sky130-openroad-tutorial/) flows.
- **Closed-Loop Formal Verification with Typed Property IR & Vacuity Detection**: Inspired by [Open-Source LLM-Driven Formal Verification (arXiv:2607.28877)](https://arxiv.org/html/2607.28877v1).
- **Dependency-Slicing-Guided Minimal RTL Patching**: Inspired by [Veri-Sure (arXiv:2601.19747)](https://arxiv.org/html/2601.19747v2).
- **Evolutionary & RL-Driven PPA Optimization**: Inspired by [REvolution (arXiv:2510.21407)](https://arxiv.org/html/2510.21407v1), [Validation-First RTL (arXiv:2604.19856)](https://arxiv.org/html/2604.19856v1), and [Can AI Agents Really Complete RTL-to-GDS? (arXiv:2607.17528)](https://arxiv.org/html/2607.17528v3).
