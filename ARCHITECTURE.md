# SiliconForge System Architecture

## 1. Principal-Level Design Philosophy

Real-world silicon tapeouts fail when LLMs are treated as unconstrained text generators. SiliconForge enforces **verification-first, tool-grounded engineering**:

1. **Contract-First Decomposition**: Before writing a single line of SystemVerilog, the Architect Agent emits a strongly typed `DesignContract` JSON (ports, widths, directions, clock domains, reset polarity/synchronicity, latency targets, PPA budgets). Every downstream agent is validated against this immutable contract.
2. **Typed Property IR (No Raw SVA Hallucinations)**: LLMs frequently hallucinate invalid SystemVerilog Assertion temporal syntax or vacuous properties. SiliconForge's Formal Agent emits a structured `TypedPropertyIR` (e.g., `RESET_VALUE`, `HANDSHAKE_STABILITY`, `MUTUAL_EXCLUSION`, `COMBINATIONAL_EQUIVALENCE`, `LATENCY_BOUND`, `COVER_REACHABILITY`), which a deterministic compiler lowers into SymbiYosys-compatible SVA and `.sby` task files.
3. **Six-Gate Anti-Hallucination Shield**:
   - *Gate 1 — Port & Interface Contract Check*: Verifies exact module name, port names, directions, and bit-widths against `DesignContract`.
   - *Gate 2 — C++20 Static RTL Linter (`sf-telemetry`)*: Detects inferred latches, combinational feedback loops, blocking assignments in sequential blocks, non-synthesizable constructs (`initial`, `#delay`, `real`, `fork/join`), and unsynchronized multi-clock crossings.
   - *Gate 3 — PDK Cell Allowlist Guard*: Ensures any instantiated macros/cells exist in the target PDK (`sky130A.yaml` / `asap7.yaml`).
   - *Gate 4 — EDA Tcl Command & Flag Allowlist*: Prevents LLMs from inventing nonexistent OpenROAD/Yosys/OpenSTA commands or deprecated OpenLane 1 variables.
   - *Gate 5 — Formal Vacuity & Cover Check*: Runs SymbiYosys `cover` alongside `bmc`/`prove` (k-induction) to ensure assumptions are not over-constrained and assertions are non-vacuous.
   - *Gate 6 — Post-Synthesis Logical Equivalence Check (LEC)*: Runs Yosys `equiv_make`/`equiv_induct` between golden RTL and synthesized gate-level netlist before physical implementation.
4. **Dependency-Slicing-Guided Repair**: When formal verification or Cocotb simulation produces a counterexample, `rtl_slicer.py` computes the backward cone of influence (COI) for the failing output/assertion signal and feeds only the minimal signal slice + distilled witness trace to the RTL Coder, preventing whole-file rewrites and regressions.
5. **Hybrid RL + Evolutionary PPA Optimization**: Physical design knobs (`SYNTH_STRATEGY`, `FP_CORE_UTIL`, `PL_TARGET_DENSITY`, `CLOCK_PERIOD`, `CTS_BUF_DISTANCE`, `GRT_ADJUSTMENT`) form a discrete-continuous action space. SiliconForge optimizes a scalarized + Pareto-front objective over WNS, TNS, core area, total power, DRC/LVS violations, and runtime using a clipped PPO-style policy update combined with UCB exploration and Pareto elitism.
6. **Tiered Inference Routing & Cost Control**: Tasks are routed across three tiers (`local_fast`, `balanced`, `frontier_reasoning`) with SHA-256 semantic prompt caching, automatic fallback, and a strict per-run USD/token circuit breaker.

---

## 2. End-to-End Pipeline DAG

```text
[Natural-Language Spec]
          │
          ▼
  (1) Architect Agent ──► DesignContract JSON + RAG Skill Context
          │
          ▼
  (2) RTL Coder Agent ──► Synthesizable SystemVerilog (.sv)
          │
          ▼
  (3) Anti-Hallucination Guardrails (Contract + C++20 Lint + PDK/Tcl Allowlist)
          │ (fail: localized diagnostic feedback -> step 2)
          ▼
  (4) Formal Verifier + Simulation Loop
      ├── Typed Property IR -> SVA -> SymbiYosys (BMC + k-Induction + Cover)
      ├── Verilator / Cocotb Testbench
      └── (fail: Witness Distiller + COI RTL Slicer -> minimal patch -> step 2)
          │
          ▼
  (5) Synthesis (Yosys + ABC) + Formal LEC (RTL == Gate Netlist)
          │
          ▼
  (6) Physical Design & RL PPA Optimization Loop (OpenROAD / LibreLane)
      ├── Floorplan -> Placement -> CTS -> Routing -> OpenSTA -> Magic/KLayout DRC -> Netgen LVS
      ├── C++20 Telemetry Engine parses timing, area, power, DRC/LVS into PPAMetrics
      └── RL/Evolutionary Policy updates knobs & tracks Pareto Front
          │
          ▼
  (7) Signoff Gate (WNS >= 0, DRC == 0, LVS == clean, LEC == pass) ──► GDSII + Signoff Report
```
