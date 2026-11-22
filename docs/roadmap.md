# Roadmap and future work

What is deliberately **not** in v0.5.0, why, and what would make it worth doing. Items are ordered by how much they would change
what an engineer can trust or do, not by effort. Nothing here is promised for a date.

## Worth doing next

| Item | Why it matters | State |
| :--- | :--- | :--- |
| Fix the agent loop's two measured weaknesses, then re-test on **new** tasks | The head-to-head against LangGraph and Pydantic AI found them: the loop does not write a requested `ANSWER:` line inside its JSON envelope, and 11 of 34 `edit_file` calls missed their target with no re-check afterwards ([docs/agent-benchmark.md](agent-benchmark.md)). Fixing and re-running on the same 13 tasks would overstate the gain, so the re-test needs new ones. | Diagnosed, not fixed. |
| A **test and assertion agent** accepted only if its tests kill mutants | The mutation tool exists (`verify mutate`); an agent that writes cocotb tests and is rewarded by the kill rate, with surviving mutants fed back, is the natural closed loop for verification engineers. | Parts exist; the loop does not. |
| **Resuming an interrupted run** from the last completed stage | Long place-and-route runs lose work when the machine sleeps. Today a run is marked `interrupted` and can be started again from its stored request; it does not continue. It needs the flow state saved after each stage, which the pipeline does not do yet. | Not started. |
| **CDC** beyond the structural check | `siliconforge verify cdc` is a heuristic on RTL text: it finds unsynchronized and single-flop crossings of registers between `always_ff` clock domains. It does not follow ports, `assign`s, generate blocks, reset synchronizers or handshakes. A netlist-level check from the SDC clocks would. | Heuristic shipped. |
| **IHP SG13G2** and more PDKs validated | `gf180mcuD` has one design through the project flow; `ihp-sg13g2` is an allowlist only (see [docs/flow.md](flow.md#pdks)). | See flow.md. |

## Would be good, needs evidence first

- **Hybrid retrieval (BM25 plus embeddings).** The knowledge packs are a few hundred chunks. Hybrid search is adopted only if a labelled
  query set shows it beating BM25 with an interval; that set has not been built, so retrieval stays BM25
  ([docs/infrastructure.md](infrastructure.md)).
- **CVDP** as a second benchmark suite, so results do not rest on a nearly saturated VerilogEval. The licence and the runnable subset
  have not been checked.
- **A comparison against published OpenROAD-Flow-Scripts numbers** on `gcd` and `picorv32` (and `ibex`, which needs a SystemVerilog
  front end Yosys' `read_verilog -sv` does not provide).
- **A genuine long-reasoning run**: the 16k-token thinking measurement is in [docs/evals.md](evals.md); a larger model and more problems would
  settle what thinking is worth on this task.
- **A second fine-tuning round and a larger base model** (the first round was a 1.5B model).

## Smaller, or only for later

- Hierarchical hardening (macros) and SRAM integration in the project flow.
- Token streaming in the terminal UI: needs the agent protocol to move from one JSON object per step to native tool calling.
- A waveform viewer and a regression board (failures grouped by `triage` bucket) in the web UI.
- Scheduling the canary workflow, base images pinned by digest, a signed container image and signed installers.
- A hosted multi-user deployment (Postgres, real authentication). A different product; see [docs/infrastructure.md](infrastructure.md).
