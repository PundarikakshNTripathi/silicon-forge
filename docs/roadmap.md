# Roadmap and future work

What is deliberately **not** in v0.5.0, why, and what would make it worth doing. Items are ordered by how much they would change
what an engineer can trust or do, not by effort. Nothing here is promised for a date.

## First: one clean piece of evidence

Everything below matters less than this. The project's claim is that gating each stage on a tool verdict beats simpler setups; today
that is argued, not shown (see [landscape.md](landscape.md#6-where-siliconforge-is-weaker)). The missing result is a single comparison
that could come out against us:

- **Setups:** the raw model; the model with compile and simulation feedback; the full tool-gated pipeline; and a plain coding agent
  (Claude Code or Gemini CLI) given Yosys and SymbiYosys and no SiliconForge code.
- **Tasks:** a set that is not saturated and not memorised: [NotSoTiny](landscape.md#3b-added-in-november-2026-read-on-their-own-pages-2026-11-23)
  (formal-equivalence grading, refreshed as new Tiny Tapeout shuttles appear) and the corrected CVDP/RTLLM subsets, plus a private
  held-out set kept out of the repository. Visible tests for the agent, hidden tests for the score.
- **Reporting:** intervals, every failure listed, and the grader checked first (the reference passes, a mutant fails).
- **Stop rule, decided in advance:** if the pipeline is not clearly better than model-plus-simulation-feedback, or the plain coding
  agent matches it, the project is positioned as teaching and tooling and nothing is spent on training or compute.

The first step exists: `services/orchestrator/evals/equiv.py` is a pairwise equivalence grader (Yosys `equiv_simple`, then `equiv_induct -seq 30`, the NotSoTiny recipe) with the self-check above. It needs no model and runs on CPU. What is missing is the task sets, the baselines and the runs.

Only after that is there a reason to look at learning:

| Step | Evidence it is worth it | Cost note |
| :--- | :--- | :--- |
| Verdict-labelled memory (what worked on which kind of failure, replay-checked before use) | A gain on repeated failure families, measured against the same agent without it. Published memory gains are 2 to 8 points and fragile. | Small. |
| Rejection-sampling fine-tuning on verified answers, at a larger scale than the 1.5B round | The ChipMATE ablation puts most of the available gain here, not in RL. | Teacher tokens and a rented 24 GB GPU; tens of dollars. |
| A short reinforcement-learning pilot (tens of steps, LoRA) with a hidden second checker and mutation-checked tests | Only if supervised tuning has already moved the held-out score. Published RL adds 1.4 to 6.5 points and reward hacking is the main risk. | A few dollars for the pilot, hundreds for a real run. |
| Improvement loops over harness and prompts (evolutionary search against the verifier) | Only with a sealed audit set the loop cannot read, paired runs, at least two repeats and matched budgets. Call it verified continual improvement, not self-improvement. | Small. |

## Worth doing next

| Item | Why it matters | State |
| :--- | :--- | :--- |
| **Re-test the fixed agent loop on tasks written blind** | The two weaknesses the head-to-head found (answer format, `edit_file` misses) are fixed and the loop scores 92.3% on the original 13 tasks and 100% on 4 new ones, but both sets were visible when the fixes were written ([docs/agent-benchmark.md](agent-benchmark.md#after-the-fixes-contaminated-read-this-before-quoting-a-number)). A fair number needs tasks written by someone who has not read the failures, and the frameworks re-run beside it. | Fixed; the clean re-test is not done. |
| A **test and assertion agent** accepted only if its tests kill mutants | The mutation tool exists (`verify mutate`); an agent that writes cocotb tests and is rewarded by the kill rate, with surviving mutants fed back, is the natural closed loop for verification engineers. | Parts exist; the loop does not. |
| **Resuming an interrupted run** from the last completed stage | Long place-and-route runs lose work when the machine sleeps. Today a run is marked `interrupted` and can be started again from its stored request; it does not continue. It needs the flow state saved after each stage, which the pipeline does not do yet. | Not started. |
| **CDC** beyond the structural check | `siliconforge verify cdc` is a heuristic on RTL text: it finds unsynchronized and single-flop crossings of registers between `always_ff` clock domains. It does not follow ports, `assign`s, generate blocks, reset synchronizers or handshakes. A netlist-level check from the SDC clocks would. | Heuristic shipped. |
| **Larger designs on gf180mcuD and ihp-sg13g2** | Both PDKs have one small design (`up_down_counter`) through the project flow, signoff clean ([docs/flow.md](flow.md#pdks)); nothing larger, and no clock-tree or congestion stress. | One tiny design each. |

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
