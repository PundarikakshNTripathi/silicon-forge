# Verification toolkit

Tools for answering "how do I know this RTL is right, and how good is the evidence?". They are standard-library-only
scripts that run wherever your EDA tools run (native, WSL or Docker) through SiliconForge's `Runner`. They are copied into
`<workspace>/.siliconforge/tools/` before use, so the version that produced a result sits next to it, and you can vendor
them into your own CI. Every result carries its `provenance` (`native`, `wsl` or `docker`); the simulated backend refuses to run them.

| Tool | What it answers | CLI |
|---|---|---|
| cocotb runner (`sf_cocotb_run.py`) | do the tests pass, for which seeds, replayable? | `siliconforge verify sim` |
| Lockstep + coverage (`sf_lockstep.py`) | does the RTL match a golden model after *every* instruction? which behaviours did the tests reach? | used inside cocotb tests |
| Mutation testing (`sf_mutate.py`) | would the tests notice a planted bug? | `siliconforge verify mutate` |
| Toggle coverage (`sf_toggle.py`) | which signal bits never moved both ways? | `siliconforge verify toggle` |
| Protocol decoders (`sf_protocols.py`) | does the pin behaviour actually speak UART / SPI / I2C / WS2812? | library and CLI |

All of them are also MCP tools (`run_cocotb`, `mutation_test`, `isa_check`) for Claude Code, Codex and Antigravity.

## Lockstep co-simulation

The golden model comes from the ISA spec ([isa-toolchain](isa-toolchain.md)). A cocotb test runs the same program on the
RTL and the model and, after every retired instruction, compares the whole architectural state. The first difference stops
the test with the instruction that caused it and the trace before it:

```
RTL and model diverge after instruction #12:
  x: rtl=254 model=255
recent instructions (oldest first):
  #11 @4 ldx {'v': 0}
  #12 @5 djnz {'t': 5}
```

External stimulus goes in the `before_step` hook so both sides see identical inputs (the model runs first, so stimulus chosen
later would reach only the RTL; the worked example hit exactly this bug in its own harness). The example also checks the
clock gap between instructions against the model's cycle counts.

## Mutation testing

`sf_mutate.py` plants single-point bugs (operator swaps, constant ±1, negated conditions, deleted non-blocking
assignments), reruns your test command on each, and reports the kill rate and every survivor with file, line and exact change.
Mutants that fail to parse are `invalid`. If Yosys can *prove* a mutant equivalent to the original it is excluded, so the
tests are not blamed for missing a change that changes nothing; a mutant Yosys cannot decide stays in the pool, which can only lower the score. The baseline must pass first, or the run aborts.

Worked example (`hardware/isa/pio16`, seed 1, WSL): **90.9 %** (40 killed, 4 survived of 44). The survivors are specific
holes: the reset value of `wait_cnt` is unchecked (Icarus treats X leniently), and no test writes program memory while the
engine runs. Both are things a reviewer would want to know.

Limits: mutants are line-oriented and skip declarations, widths, indices and parameters; this is RTL-level mutation, not
gate-level. A high score means the tests are sensitive to the bugs tried, not that no bug exists.

## Coverage

*Functional* coverage is defined by you (`CoverGroup`: points, bins, crosses) and merges across tests and seeds into one
JSON file. *Toggle* coverage reads a VCD/FST dump. It is not line or branch coverage, and the report says so in its `kind`
field. (Icarus has no code coverage; Verilator coverage needs a C++ toolchain this environment does not have.)

## Independent protocol decoders

The decoders see only edges on wires, never any RTL, so a design cannot agree with them out of a shared misunderstanding.
UART reports framing errors, parity errors and `max_edge_error` (how far the real edges sit from the ideal bit grid, in bit
times), which is how bit-banged timing bugs appear. Reference encoders for each protocol let you test the decoders themselves.

## Worked example

```bash
cd hardware/isa/pio16
siliconforge init . --backend wsl --force
siliconforge verify sim --sources gen/rtl/pio16_decode.v rtl/pio16.v --top pio16 --test-module test_pio16 \
    --test-dir test --include gen/rtl --seeds 1,2 --env SF_TOOLS=.siliconforge/tools
```

The tests (`test/test_pio16.py`) run constrained-random programs in lockstep (2,720 instructions over 25 programs at
95.8 % functional coverage in the recorded run), a directed UART transmission judged by the independent decoder, and an
undefined-instruction check.
