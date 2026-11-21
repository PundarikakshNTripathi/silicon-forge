# Debug context

When a check fails, the agent that repairs the RTL needs evidence, not just a verdict. This page describes what it is
given and why. The code is in `services/orchestrator/debug/`, `guardrails/rtl_slicer.py` and `pipeline._repair`.

## What a repair prompt contains

| Section | Source | When present |
| --- | --- | --- |
| The diagnostic | the tool that failed (guardrails, Verilator, SymbiYosys) | always |
| Lines the tool complained about | `file.sv:12:5:` locations in a lint or compile message, mapped onto the RTL and marked `>>` | the message has locations |
| A waveform window | the counterexample trace: values of the contract's signals at the change times up to the failing step, `.` where a value did not change | formal failures on a real backend |
| The cone of influence | the signals that can influence the failing signal, and only those lines of the RTL | a failing signal is known |
| Earlier attempts | for this loop: what each previous repair changed (unified diff, trimmed) and the failure that followed | from the second attempt |
| A "change approach" note | added when the last two attempts ended with the same failure (compared with numbers masked) | the loop is stuck |

Sections with nothing to say are left out. Set `pipeline.debug_context: false` to turn the whole block off; the
evaluation harness uses that switch to measure what it is worth.

## Waveform windows

`waveform_window(trace, fail_time, before, after, signals, mark)` prints one row per change time. Quiet stretches cost
nothing, an unchanged value is shown as `.`, the failing time carries `@`, and the signal under investigation is the last
column, flagged with `>`. This follows MAGE (arXiv:2412.07822): what helps a debugger is the earliest point where the
output departs from the expectation and a short window around it, not a whole VCD.

`first_divergence(a, b)` returns the earliest time at which two traces differ and which signals differ, for example the RTL
against a reference model, or the same design before and after a fix. The agent exposes both as the read-only tools
`waveform` and `trace_diff`.

## Cone of influence

`RtlDependencySlicer` is a statement-level analysis, not an elaborator. It follows data dependencies and control
dependencies (the `if` and `case` conditions that guard an assignment, and the clock and reset edges of a register).
Instances are handled conservatively: every signal connected to one instance may influence the others. It does not
elaborate `generate` blocks, treat parameters as signals, read `function` bodies or follow hierarchical names. Within
those limits the result is a superset of the true cone. The agent tool is `cone_of`.

## Sampling

`pipeline.repair_candidates: N` (default 1) draws N repairs: the first at low temperature, the rest at 0.7. They are
ranked by what is cheap and deterministic, guardrails first and then Verilator lint, and ties keep the earliest. The scores
are logged as an `RTL_REPAIR` event. Formal verification is not part of the ranking; it stays the loop's own gate.
Candidates are generated one after another, so on a single-slot local server N repairs cost N times the generation time.

## What is not claimed

These are mechanisms. Whether they raise the fraction of designs a given model gets through is measured by the evals
(`docs/evals.md`), per model, with the switches above turned on and off. No number is quoted here.
