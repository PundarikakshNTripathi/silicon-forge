# Landscape: who else is doing this, and where each approach wins

SiliconForge turns a specification into verified RTL, formal proofs and a layout using open tools. This page compares it
with the other places that problem is being attacked, says what each does better, and records what was adopted.

How to read it: academic and open-source systems below were read through their papers or repositories; **commercial products
are listed from public announcements only and were not evaluated**, so no claim here is a benchmark of them. Every arXiv
reference was checked against its abstract page.

## 1. Research systems

| System | Idea | Where it is stronger than SiliconForge | What SiliconForge does differently |
|---|---|---|---|
| [MAGE](https://arxiv.org/abs/2412.07822) (2412.07822) | multi-agent RTL generation with sampling and testbench-based ranking | higher single-module pass rates through many candidates | tool verdicts (formal, equivalence) decide every edge; candidates are checked, not voted |
| [VerilogCoder](https://arxiv.org/abs/2408.08927) (2408.08927) | graph-based planning and waveform tracing for debugging | richer debugging signal from waveforms | counterexamples are parsed into typed witnesses and the cone of influence is sliced for repair |
| [Veri-Sure](https://arxiv.org/abs/2601.19747) (2601.19747) | temporal tracing, slicing and formal verification for RTL | closest in spirit to the repair loop | SiliconForge continues past RTL into equivalence, layout and signoff |
| [Spec2RTL-Agent](https://arxiv.org/abs/2506.13905) (2506.13905) | decomposing a complex specification into modules | hierarchical specs | single-module contracts today; hierarchical work is the next step |
| [AutoChip](https://arxiv.org/abs/2311.04887), [Chip-Chat](https://arxiv.org/abs/2305.13243) | EDA-tool feedback to an LLM; conversational design | the original evidence that tool feedback helps | typed property IR instead of free-text feedback |
| [AssertLLM](https://arxiv.org/abs/2402.00386), [FVEval](https://arxiv.org/abs/2410.23299), [Automated SVA Generation](https://arxiv.org/abs/2604.11044) | natural language to SystemVerilog assertions, and how to measure it | assertion-quality benchmarks | properties come from a closed set of typed templates and are checked for vacuity |
| [ORAssistant](https://arxiv.org/abs/2410.03845), [ORFS-agent](https://arxiv.org/abs/2506.08332) | RAG assistant for OpenROAD; agentic flow tuning | OpenROAD knowledge and flow-parameter search | an RL + UCB + Pareto optimizer over real layout runs with a result cache, and signoff at the worst timing corner |
| [Open-source LLM formal verification pipeline](https://arxiv.org/abs/2607.28877) (2607.28877) | multi-agent RTL repair with open models | open-model formal repair | same direction; SiliconForge adds provenance on every tool result |
| [Dawn of Agentic EDA](https://arxiv.org/abs/2512.23189), [small models with agentic scaffolding](https://arxiv.org/abs/2512.05073) | survey; how far small models go with an agent loop | the survey is the best map of the field | the local-first design bets on exactly the small-model result |

**Open physical-design agents** (checked 2026-11-21; none of these was run for this page):

- [AgenticLane](https://github.com/meyanik/Agenticlane) (MIT): a multi-agent orchestration layer for LibreLane RTL-to-GDS flows,
  built as a LibreLane plugin. The repository showed five commits, so it is early. It is the closest open project to the
  physical half of SiliconForge; SiliconForge differs in keeping tool verdicts, not agents, as the gate on every stage.
- [ORFS-agent](https://arxiv.org/abs/2506.08332) and [Automated QoR improvement in OpenROAD with coding agents](https://arxiv.org/abs/2601.06268)
  (both Ghose): agents that tune the open flow's parameters, and agents that propose diffs to OpenROAD itself.

Benchmarks these systems are measured on: [VerilogEval](https://arxiv.org/abs/2309.07544) and its
[revisit](https://arxiv.org/abs/2408.11053), [RTLLM](https://arxiv.org/abs/2308.05345), and NVIDIA's
[CVDP](https://arxiv.org/abs/2506.14074), which covers design and verification tasks and agentic settings. They measure
simulation-based correctness (CVDP also covers verification tasks). From their abstracts, none of them takes a design all
the way through formal proof, equivalence checking and layout, which is what SiliconForge's own evaluation reports.

Models: [RTLCoder](https://arxiv.org/abs/2312.08617), [CodeV](https://arxiv.org/abs/2407.10424),
[VeriGen](https://arxiv.org/abs/2308.00708) and [ChipNeMo](https://arxiv.org/abs/2311.00176) show what domain tuning buys.

## 2. Open flows SiliconForge builds on

OpenROAD Flow Scripts, LibreLane, SiliconCompiler and Hammer differ in architecture (monolithic flows versus plugin
frameworks, per the LibreLane FAQ) rather than in a single winner. SiliconForge drives LibreLane and does not compete with
it. [Basilisk](https://arxiv.org/abs/2405.04257) is the honest scale reference: open tools can produce a multi-million-gate
RV64 SoC, but not without effort, which is why SiliconForge targets blocks and contest-sized designs.

A caution about learning-based placement: the [AlphaChip rebuttal](https://arxiv.org/abs/2411.10053) and the
[independent assessments](https://arxiv.org/abs/2302.11014) ([2306.09633](https://arxiv.org/abs/2306.09633)) disagree about
how well RL macro placement works against classical methods. SiliconForge's optimizer tunes flow knobs over real runs and
reports measured results; it makes no claim about macro placement.

## 3. Commercial agentic EDA (public announcements only, not evaluated)

Cadence (ChipStack, Cerebrus), Synopsys (AgentEngineer, DSO.ai), Siemens (Fuse, Questa One) and startups including
ChipAgents, Cognichip, Bronco and Silimate are building agent layers over proprietary signoff tools. Their advantages are
structural and not something an open project matches: access to signoff-grade tools and foundry PDKs, UVM and
coverage-closure infrastructure, regression farms, and vendor support. What an open local-first tool can offer instead:
auditability (every result says which tool produced it), no data leaving the machine, and a flow a student can run.

## 4. What contest entries actually do

Seven public entries to the Jane Street x Tiny Tapeout protocol-emulator competition were read (READMEs and linked docs,
figures as the repositories state them; none was independently verified):

| Entry | Architecture | Verification described |
|---|---|---|
| [jeddfadhley/protocol-emulator-asic](https://github.com/jeddfadhley/protocol-emulator-asic) | two PIO-like engines, packet RAM + DMA, 8x4 tiles, 29,260 cells | lockstep against a Python model (about 700k clocks per CI run), BMC + k-induction, 48-bug mutation test, SDF gate-level at three corners, FPGA bring-up, detailed AI-use doc |
| [Edoouuard/protocol-emulator-ASIC](https://github.com/Edoouuard/protocol-emulator-ASIC) | PicoRV32 with 18 custom instructions, 8x4 | static worst-case timing checker, proof of timing non-interference, 15-bug mutation check, 13 cocotb tests on RTL and gate level |
| [OliverKlug/sophos-protocol-emulator](https://github.com/OliverKlug/sophos-protocol-emulator) | one PIO-like machine, 6x4, 11,624 cells, 40 MHz | Python golden model, SymbiYosys proofs, a candid status ledger |
| [kdp1965/ihp-um-janestreet-prism](https://github.com/kdp1965/ihp-um-janestreet-prism) | two 16-state machines inside a RISC-V SoC, 8x4 | only CI badges described |
| three smaller entries | PIO-like or pin-engine designs, 6x4 | mostly cocotb or testbenches; one has never compiled its RTL |

Patterns: six of seven are PIO-style pin engines with 16-bit words; formal appears in three, mutation testing in two,
gate-level simulation in three, FPGA bring-up in one. **Gaps nobody fills:** published coverage numbers, protocol cost per
cell or per tile, real-hardware conformance, electrical pin behaviour, silicon bring-up plans.

## 5. What was adopted

| From | Adopted | Where |
|---|---|---|
| strong contest entries | lockstep co-simulation against an independent model, mutation testing, gate-level simulation | `docs/verification.md`, `docs/tiny-tapeout.md` |
| Edoouuard's timing checker | cycle gaps between pin events, proved from the control-flow graph | `docs/isa-toolchain.md` |
| the gaps above | functional + toggle coverage reported, independent protocol decoders, per-protocol area from real synthesis, an AI-usage ledger | `verify/`, `tt/`, `inference/ledger.py` |
| agentic systems (Chip-Chat, AutoChip, MAGE) | tool-using agent in the user's folder | `docs/workspaces.md` |
| benchmark practice (CVDP, VerilogEval) | evaluation against formal and equivalence, not only simulation | `benchmarks/` |
| MAGE | a text waveform window around the failing step and the earliest divergence between traces | `services/orchestrator/debug/`, `docs/debug-context.md` |
| Veri-Sure | dependency slicing, now with control dependencies (the first version ignored `if` and `case` conditions) | `guardrails/rtl_slicer.py` |
| repository-level evolution (2606.28279) | scoring tests withheld from the agent; the agent sees compile diagnostics only | `docs/evals.md` |
| agent-harness study (2603.19347) | a single-call baseline in every comparison, because a loop can lower the score | `siliconforge evals` |
| RLVR work (CodeV-R1) | verifier-based reward and a train/test split by problem, in an environment with the testbench hidden | `evals/env.py`, `evals/trajectory.py` |
| open-flow practice | a project flow for existing RTL with signoff gates, where a missing metric is never a pass | `docs/flow.md` |

Not adopted, deliberately: n-way candidate voting without a tool verdict, vendor-style UVM, and any claim about macro
placement or scale beyond blocks.

## 6. Where SiliconForge is weaker

No UVM or constrained-random class libraries; one benchmark suite (VerilogEval, close to saturated for strong models) and no CVDP or ChipBench run yet; no clock-domain-crossing analysis, hierarchical macros or SRAM macros in the project flow; no learned improvement demonstrated yet beyond what `docs/evals.md` reports; no line or branch coverage here (Icarus has none, and the WSL setup has no C++
toolchain); one PDK family is deeply supported; designs are blocks, not SoCs; timing, DRC and LVS signoff for the contest
process rely on the official Tiny Tapeout flow.
