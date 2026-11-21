# Papers worth reading, in a sensible order

Two groups. The **arXiv papers** were each checked against their abstract page (title, first author, date). The **classics**
are cited from memory of the literature with venue and year and have no link here: find them through your library or a
scholarly index, and treat the citation details as something to confirm. Preprints are not peer reviewed; the date is the
first version.

## Foundations of the tools you will use

These explain why SymbiYosys, Yosys and OpenROAD work the way they do. Classics (no verified link):

- Biere, Cimatti, Clarke, Zhu, "Symbolic Model Checking without BDDs", TACAS 1999: bounded model checking, the `bmc` task.
- Sheeran, Singh, Stalmarck, "Checking Safety Properties Using Induction and a SAT-Solver", FMCAD 2000: k-induction, how a bounded proof becomes an unbounded one.
- Bradley, "SAT-Based Model Checking without Unrolling", VMCAI 2011: IC3/PDR, the `abc pdr` engine SiliconForge uses for unbounded proofs.
- Brayton and Mishchenko, "ABC: An Academic Industrial-Strength Verification Tool", CAV 2010: the logic-synthesis and verification engine inside Yosys flows.
- Wolf, Glaser, Kepler, "Yosys: A Free Verilog Synthesis Suite", 2013: the synthesis tool itself.
- Ajayi et al., "Toward an Open-Source Digital Flow: First Learnings from the OpenROAD Project", DAC 2019: how the open RTL-to-GDSII flow came together.
- DeMillo, Lipton, Sayward, "Hints on Test Data Selection: Help for the Practicing Programmer", IEEE Computer 1978: the origin of mutation testing.

Open flows at scale:

- [Insights from Basilisk: Are Open-Source EDA Tools Ready for a Multi-Million-Gate, Linux-Booting RV64 SoC Design?](https://arxiv.org/abs/2405.04257) (Sauter et al., 2024-05): what open tools can and cannot yet do.

## LLMs for hardware: generation and benchmarks

- [VerilogEval](https://arxiv.org/abs/2309.07544) (Liu et al., 2023-09) and [Revisiting VerilogEval](https://arxiv.org/abs/2408.11053) (Pinckney et al., 2024-08): the standard Verilog generation benchmark and how it was tightened.
- [RTLLM](https://arxiv.org/abs/2308.05345) (Lu et al., 2023-08): spec-to-RTL benchmark.
- [CVDP: Comprehensive Verilog Design Problems](https://arxiv.org/abs/2506.14074) (Pinckney et al., 2025-06): design and verification tasks, including agentic ones.
- [VeriGen](https://arxiv.org/abs/2308.00708) (Thakur et al., 2023-07), [RTLCoder](https://arxiv.org/abs/2312.08617) (Liu et al., 2023-12), [CodeV](https://arxiv.org/abs/2407.10424) (Zhao et al., 2024-07): fine-tuned Verilog models; read them for what training data buys.
- [ChipNeMo](https://arxiv.org/abs/2311.00176) (Liu et al., 2023-11): domain adaptation of an LLM for chip design inside a company.
- [Chip-Chat](https://arxiv.org/abs/2305.13243) (Blocklove et al., 2023-05): an honest account of designing a chip conversationally, including what went wrong.

## Agents that use tools

- [AutoChip](https://arxiv.org/abs/2311.04887) (Thakur et al., 2023-11): feeding tool errors back to the model.
- [MAGE](https://arxiv.org/abs/2412.07822) (Zhao et al., 2024-12): multi-agent generation with sampling and testbench ranking.
- [VerilogCoder](https://arxiv.org/abs/2408.08927) (Ho et al., 2024-08): planning plus waveform tracing for debugging.
- [Spec2RTL-Agent](https://arxiv.org/abs/2506.13905) (Yu et al., 2025-06): decomposing a complex specification.
- [Veri-Sure](https://arxiv.org/abs/2601.19747) (Liu et al., 2026-01): temporal tracing, slicing and formal verification in the loop, the nearest relative of SiliconForge's repair loop.
- [Open-source LLM-driven formal verification: a multi-agent pipeline for RTL repair](https://arxiv.org/abs/2607.28877) (Tran et al., 2026-07).
- [David vs. Goliath: small models with agentic AI in hardware design](https://arxiv.org/abs/2512.05073) (Shankar et al., 2025-12): the case for running small local models behind an agent loop.
- [The Dawn of Agentic EDA](https://arxiv.org/abs/2512.23189) (Zang et al., 2025-12): the survey to read first.

## LLMs and assertions

- [AssertLLM](https://arxiv.org/abs/2402.00386) (Fang et al., 2024-02): assertions from design specifications.
- [FVEval](https://arxiv.org/abs/2410.23299) (Kang et al., 2024-10): how well models do formal-verification tasks.
- [Automated SVA Generation with LLMs](https://arxiv.org/abs/2604.11044) (Fu et al., 2026-04).

## Assistants for the physical flow

- [ORAssistant](https://arxiv.org/abs/2410.03845) (Kaintura et al., 2024-10): retrieval-based help for OpenROAD.
- [ORFS-agent](https://arxiv.org/abs/2506.08332) (Ghose et al., 2025-06): tool-using agents tuning the open flow.

## A live scientific dispute: learning-based placement

Read these together and decide for yourself. They are EDA papers, not LLM papers.

- [That Chip Has Sailed: A Critique of Unfounded Skepticism Around AI for Chip Design](https://arxiv.org/abs/2411.10053) (Goldie et al., 2024-11): the defence of AlphaChip.
- [An Updated Assessment of Reinforcement Learning for Macro Placement](https://arxiv.org/abs/2302.11014) (Cheng et al., 2023-02) and [The False Dawn](https://arxiv.org/abs/2306.09633) (Markov, 2023-06): the critiques.

## Prior art for protocol engines

The RP2040 datasheet chapter on PIO (linked from the [roadmap](roadmap.md)) is the design most contest entries descend from;
reading it first makes the entries' choices legible. The [landscape page](../landscape.md) summarizes seven public entries.
