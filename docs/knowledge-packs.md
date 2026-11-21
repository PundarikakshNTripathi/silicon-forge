# Knowledge packs

A knowledge pack is a Markdown file of engineering guidance. Each `## ` section is one retrievable chunk. The RTL coder gets the
sections that match a lint diagnostic or the specification, the formal agent gets the typed-property rules, and the workspace
agent can look things up with the `knowledge` tool.

## What ships

| Pack | Covers |
| --- | --- |
| `verilator_warnings` | The common Verilator messages (WIDTH, LATCH, BLKSEQ, COMBDLY, MULTIDRIVEN, UNOPTFLAT, undeclared signals, statements outside a block) and SiliconForge's own `SF-RTL-001..006` rules, with the fix for each |
| `sv_synthesizable_idioms` | Register and combinational templates, reset style, an FSM template, counters, widths and signedness, what not to write |
| `handshake_and_fifo` | Ready/valid rules, FIFO pointers and flags, skid buffers, the five AXI-Lite channels |
| `cdc_and_resets` | Synchronizers, Gray-coded pointers, reset synchronizers. SiliconForge has no CDC analysis yet; the pack says so |
| `sdc_basics` | `create_clock`, input and output delays, uncertainty, false and multicycle paths |
| `librelane_knobs` | What `CLOCK_PERIOD`, `FP_CORE_UTIL`, `PL_TARGET_DENSITY_PCT`, `GRT_ADJUSTMENT` and `SYNTH_STRATEGY` do, and how to read a failed signoff |
| `cocotb_testbenches` | Test structure, when a read sees the new value, comparing against a model, seeds |
| `sva_formal_patterns` | The typed-property rule (no SVA, no helper signals), anti-vacuity, reading a counterexample |
| `openroad_tcl`, `sky130_pdk` | Short reference notes |

## How retrieval works

BM25 over sections, with each heading counted three times and identifiers split at underscores (`FP_CORE_UTIL` matches `core`).
There is a **relevance floor**: a query that matches nothing well returns nothing, and the prompt gets no extra text. The floors
were set from measured scores over the 71 sections: irrelevant queries scored up to about 4, real diagnostics and topic queries
13 or more. A query of up to three words cannot score that high however exact it is, so it must instead hit a word in a
section's heading (for example `LATCH`, `width`, `cocotb clock`).

## Your own packs

Put `<name>.md` files in `<data dir>/skills/` (the cockpit's Knowledge page writes there). A pack with the name of a built-in
replaces it, so a team can override `sdc_basics` with its own constraints. Start the file with a `# Title` and use `## ` for
each topic; a section with a descriptive heading is found far more reliably than one without.

## Limits

The packs are written by hand and checked by tests that retrieve them with real diagnostics, but they have not been
measured against pipeline outcomes: whether the guidance raises the fraction of designs that verify is not shown yet, and the
local-model runs on four designs are too small to tell. Treat a pack as documentation a model can read, not as a proven aid.
