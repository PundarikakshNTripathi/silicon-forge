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

### 3b. Added in November 2026 (read on their own pages, 2026-11-23)

These change two things this page used to imply. First, "tool verdicts decide" is now a stated principle of the big vendors too, so the
slogan is not a difference; evidence is. Second, waveform debugging is no longer a gap: free servers exist.

| Item | What it is | What it means here |
|---|---|---|
| [Harmonic Aristotle](https://harmonic.fun) | A paid service that compiles SystemVerilog and SVA into Lean 4, lets a model propose proof steps and has the Lean kernel check them; returns proved, refuted (with a counterexample) or unresolved per property. Verilog, Rust and C; also an MCP server. Its Ibex multiplier/divider demo (3 Nov) proved 9 properties with no environment assumptions. Closed, vendor-reported. | The closest "agent plus a checker that decides" for hardware, and it makes "an AI proved my RTL" a one-line MCP call for anyone with a budget. SiliconForge's formal path is SymbiYosys (bounded and unbounded, with vacuity checks), open and free, and it does not try to match Lean-level proofs. |
| [TraceWeave](https://github.com/gokeshenzhen/TraceWeave) (MIT), [Tencent/wave-mcp](https://github.com/Tencent/wave-mcp) | Free MCP servers that give an agent waveform and log tools (FST/VCD, drivers, first divergence, handshakes). TraceWeave reports a repair pass-rate rise from 78.33% to 88.75% on 240 tasks (self-reported, not checked here). | Overlaps the waveform window and cone tracing in `docs/debug-context.md`, with more tools. Not a differentiator; worth interoperating with. |
| [ChipMATE](https://arxiv.org/abs/2605.12857) (Qwen3.5-4B and 9B, Apache-2.0 code and weights, gated) | A Verilog agent and a Python reference-model agent cross-check each other on 1,000 random stimuli, with no golden testbench. SFT adds 19 to 22 points on VerilogEval; the RL on top adds 1.4 to 6.5. | The nearest published result on our base-model family, and the evidence that SFT, not RL, does most of the work. Training took 8 to 16 datacentre GPUs and over $2,000 of teacher tokens; we can serve and fine-tune the 4B pair, not repeat the training. Its agentic rows use several times the samples of the single-pass baselines. |
| [NotSoTiny](https://arxiv.org/abs/2512.20823) (HPAI-BSC, Apache-2.0) | 1,146 module-completion tasks from Tiny Tapeout designs, refreshed as new shuttles appear, graded by Yosys formal equivalence (`equiv_simple`, then `equiv_induct` at k=30). Best model in the paper: about 23% equivalent. | The best-fit public benchmark for this project: formal oracle on open tools, headroom, and the same Tiny Tapeout ecosystem. Bounded induction can miss deep bugs. Not yet run here. |
| Build-A-Chip-Bench (ChipAgents and NVIDIA) | A private spec-to-GDS benchmark on open tools; best agent score 16.6% (functional 65, layout 75, PPA 25.5, all models in one harness), most open models near 0. Harness and tasks not released. | Shows the open-tool end-to-end task is unsolved. Vendor-authored and closed, so it cannot be a dependency. |
| ChipAgents' audit of CVDP, RTLLM and ChipBench | Using Lean 4, they report that few tasks have a specification and tests that agree, and publish corrected subsets (`chipagents/*-verified` on Hugging Face; licences follow the upstream sets). | Every public RTL number, ours on VerilogEval included, is noisy and partly contaminated. A hidden-test, audited comparison is the clearest open gap (see the roadmap). |

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

- **Only a small hidden-test comparison, and it did not favour the tool check.** On 40 NotSoTiny tasks (one run each, wide intervals) a Yosys parse check added nothing, and a plain coding agent with Yosys
  was not separated from a single reply (`docs/evals.md`). VerilogEval is close to saturated and probably contaminated for strong models, and our own AgentBench tasks are public. No run on corrected CVDP/RTLLM sets.
- **Learning is small.** One LoRA round on a 1.5B model (+8.1 points on held-out problems) is the only fine-tuning result. No
  reinforcement learning has been run; the published recipes for it need datacentre GPUs and gain a few points over supervised
  fine-tuning (ChipMATE above).
- No UVM or constrained-random class libraries; CDC is a text-level heuristic, not sign-off; no hierarchical macros or SRAM macros in
  the project flow; no line or branch coverage here (Icarus has none, and the WSL setup has no C++ toolchain).
- Three PDKs have a signoff-clean run, each of one small design; designs are blocks, not SoCs; timing, DRC and LVS signoff for the
  contest process rely on the official Tiny Tapeout flow.
- Waveform and log tooling for agents is now widely available and free; this project's version is not ahead of it.
