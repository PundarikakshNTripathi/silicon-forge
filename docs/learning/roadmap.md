# Learning roadmap: from zero to taping out a verified chip

A path through the skills this project uses, in the order they build on each other. Every link was checked when this page was
written (`python scripts/check_links.py` rechecks them). Resources are free to read unless marked *(book)* or *(paid)*.
Each stage ends with something you can *do*, because reading alone does not build this skill.

How long: if you already program, roughly a month per stage at evenings-and-weekends pace; stages 0 to 2 are the
foundation, 3 to 5 are where this project lives.

## Stage 0: logic and how a computer is built

*Goal: know what a gate, a flip-flop and a clock edge are, and how they make a CPU.*

- [nand2tetris](https://www.nand2tetris.org/): build a computer from NAND gates up; free for self-study, with a
  [Coursera version](https://www.coursera.org/learn/build-a-computer) *(paid certificate)*.
- [MIT 6.004 Computation Structures](https://ocw.mit.edu/courses/6-004-computation-structures-spring-2017/): lectures and
  notes from logic to a pipelined processor.
- *(book)* Harris and Harris, *Digital Design and Computer Architecture*: the standard text, with Verilog and SystemVerilog in parallel.

**Do:** finish the nand2tetris hardware projects (they use a small HDL; it is the same thinking as Verilog).

## Stage 1: a hardware description language, by writing a lot of it

*Goal: write synthesizable Verilog/SystemVerilog and know what hardware it implies.*

- [HDLBits](https://hdlbits.01xz.net/wiki/Main_Page): hundreds of small Verilog problems with instant feedback; the best drill there is.
- [Project F](https://projectf.io/): FPGA and Verilog tutorials that build real graphics and timing circuits.
- [ChipVerify](https://www.chipverify.com/): Verilog and SystemVerilog reference and examples.
- [EDA Playground](https://www.edaplayground.com/): run a simulation in the browser with nothing installed.
- [ZipCPU's beginner Verilog, formal verification and Verilator tutorial](https://zipcpu.com/tutorial/): ten lessons that
  introduce simulation and formal checking together from the start, which is the habit worth building.

**Do:** solve the HDLBits "Circuits" and "Verification: Reading simulations" sets, then write a UART transmitter and check its waveform.

## Stage 2: computer architecture

*Goal: read and design an instruction set, a datapath and a small pipeline.*

- [Onur Mutlu's Digital Design and Computer Architecture](https://safari.ethz.ch/digitaltechnik/) (ETH Zurich): full
  lecture set; the videos are on his [YouTube channel](https://www.youtube.com/onurmutlulectures).
- [RISC-V specifications](https://riscv.org/technical/specifications/): the cleanest real ISA to study.
- [RP2040 datasheet](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf), the chapter on PIO: the programmable
  I/O engine that most protocol-emulator designs descend from.
- *(book)* Patterson and Hennessy, *Computer Organization and Design: RISC-V Edition*; Hennessy and Patterson,
  *Computer Architecture: A Quantitative Approach* for depth.

**Do:** design a 16-bit ISA on paper, then run it through SiliconForge's [ISA toolchain](../isa-toolchain.md).

## Stage 3: verification and formal methods

*Goal: prove things about hardware instead of hoping, and measure how good your tests are.*

- [cocotb documentation](https://docs.cocotb.org/): Python testbenches, the tool the Tiny Tapeout template uses.
- [ZipCPU formal verification posts](https://zipcpu.com/formal/formal.html): the most practical open writing on formal
  hardware verification, from first assertion to whole CPUs.
- [SymbiYosys documentation](https://symbiyosys.readthedocs.io/) and its
  [quickstart](https://yosyshq.readthedocs.io/projects/sby/en/latest/quickstart.html).
- [YosysHQ riscv-formal](https://github.com/YosysHQ/riscv-formal): how a real ISA gets a formal specification.
- [mcy mutation coverage](https://github.com/YosysHQ/mcy): measuring testbench quality by planting bugs (ISC license).
- [pyuvm](https://github.com/pyuvm/pyuvm): the UVM methodology (agents, sequences, scoreboards) written in Python on top of
  cocotb, so you can learn the structure without a commercial simulator (Apache-2.0).
- [Verible](https://github.com/chipsalliance/verible): an open SystemVerilog parser, style linter, formatter and language server
  (Apache-2.0). [sv-tests](https://github.com/chipsalliance/sv-tests) shows which SystemVerilog features each open tool
  supports, which is worth checking before you rely on a construct; both sit under the
  [CHIPS Alliance SV Tools Project](https://chipsalliance.org/news/sv-tools-project-launch/) (announced March 2026).
- [The 2026 Functional Verification Study](https://blogs.sw.siemens.com/verificationhorizons/2026/09/08/the-2026-functional-verification-study/)
  (Foster, Siemens EDA and Wilson Research Group, 2026-09): the industry survey. It reports 5% of IC/ASIC respondents achieving
  first-silicon success, against 14.4% in 2024. It is a vendor-sponsored survey of self-selected respondents, so read the
  method before the headline, but it is the best public picture of why verification takes most of the effort.
- [Verification Academy](https://verificationacademy.com/): vendor-neutral UVM and verification methodology articles (registration may be needed).
- *(book)* Spear and Tumbush, *SystemVerilog for Verification*; Seligman, Schubert and Kumar, *Formal Verification: An Essential Toolkit for Modern VLSI Design*.

**Do:** take a module you wrote in stage 1, prove an invariant with SymbiYosys, then run SiliconForge's mutation tool on its
testbench and close the survivors ([verification guide](../verification.md)).

## Stage 4: from RTL to layout with open tools

*Goal: synthesize, place and route a design and read the reports.*

- [Zero to ASIC Course](https://www.zerotoasiccourse.com/) *(paid)*: the guided path from Verilog to a taped-out chip; its
  YouTube channel carries free material.
- [Tiny Tapeout guides](https://tinytapeout.com/guides/local-hardening): the hardening guide is the practical reference.
- [OpenROAD documentation](https://openroad.readthedocs.io/) and
  [OpenROAD Flow Scripts](https://openroad-flow-scripts.readthedocs.io/).
- [LibreLane documentation](https://librelane.readthedocs.io/): the flow SiliconForge drives.
- [Yosys](https://github.com/YosysHQ/yosys), the synthesis tool, and [Verilator](https://verilator.org/guide/latest/) for fast simulation and linting.
- Open PDKs: [SkyWater SKY130](https://skywater-pdk.readthedocs.io/), [IHP Open PDK](https://github.com/IHP-GmbH/IHP-Open-PDK) (the Tiny Tapeout IHP shuttles).
- *(book)* Weste and Harris, *CMOS VLSI Design*: what is under the standard cells.
- [Coursera VLSI CAD: Logic to Layout](https://www.coursera.org/learn/vlsi-cad-logic) *(check the current enrolment options)*: the algorithms inside synthesis and place-and-route.

**Do:** harden one small design on both a 1x1 and a larger tile and compare area and timing; read every warning.

## Stage 5: tape out something

*Goal: ship, so the whole chain stops being theory.*

- [Tiny Tapeout](https://tinytapeout.com/) shuttles are the cheapest way to get a real chip; the
  [IHP template](https://github.com/TinyTapeout/ttihp-verilog-template) is the starting point.
- The [Jane Street x Tiny Tapeout protocol emulator competition](https://tinytapeout.com/competitions/jane-street-tt-protocol-emulator)
  is a concrete, judged target: a programmable chip that speaks several protocols, with a verification write-up.
- [FOSSi Foundation](https://fossi-foundation.org/) and [CHIPS Alliance](https://www.chipsalliance.org/) for the open-silicon community.
- Alternatives to writing Verilog by hand: [Amaranth](https://amaranth-lang.org/docs/amaranth/latest/) (Python) and
  [Hardcaml](https://github.com/janestreet/hardcaml) (OCaml, from Jane Street).

**Do:** use [`siliconforge tt`](../tiny-tapeout.md) on your design before you submit, and publish what you verified and what you did not.

## Stage 6: AI for chip design

*Goal: use and judge LLM tools for hardware with calibrated skepticism.*

Start with the survey [The Dawn of Agentic EDA](https://arxiv.org/abs/2512.23189), then the benchmarks
([VerilogEval](https://arxiv.org/abs/2309.07544), [CVDP](https://arxiv.org/abs/2506.14074)) to learn how these tools are
measured, and the annotated list in [papers.md](papers.md). Read [the landscape](../landscape.md) for where SiliconForge sits.

After that, read [Exploring the Agentic Frontier of Verilog Code Generation](https://arxiv.org/abs/2603.19347) and
[Agentic Hardware Design as Repository-Level Code Evolution](https://arxiv.org/abs/2606.28279): between them they explain why
an agent loop can make a model worse and why the scoring tests must stay out of the agent's reach.

**Do:** run an LLM on a spec of your own and let the formal tool, not your opinion, decide whether the result is right. Then
measure instead of guessing: `siliconforge evals run verilogeval` with the `single` arm and with the `feedback` arm on the same
model and problems, and `siliconforge evals compare` the two result files. If the interval contains zero, the loop did not
demonstrably help ([evals guide](../evals.md)).

**Do, for your own RTL:** put a design you wrote through `siliconforge flow` and read the signoff checklist line by line,
especially the gates reported as *not reported*. Knowing what a clean report does and does not cover is the skill
([flow guide](../flow.md)).

## Practice platforms, in one place

[HDLBits](https://hdlbits.01xz.net/wiki/Main_Page) (small problems), [EDA Playground](https://www.edaplayground.com/) (browser simulation),
[Tiny Tapeout](https://tinytapeout.com/) (real silicon), an inexpensive FPGA board (real hardware to test protocols against),
and your own designs checked with [`siliconforge verify`](../verification.md).
