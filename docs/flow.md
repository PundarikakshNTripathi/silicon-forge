# The project flow: your RTL through the open toolchain

`siliconforge flow` takes RTL you already have and runs it through lint, synthesis with an equivalence check, and place and
route with signoff gates, on real tools. No model is involved. The result is a report you can keep, diff and attach to a
review.

```bash
siliconforge flow init . --top pio16 --sources "rtl/*.v" "gen/rtl/*.v" --clock clk --period 20 --pdk sky130A
siliconforge flow check .                      # validate the project in about a second, no tools
siliconforge flow run .                        # lint, synth + equivalence, place and route + signoff
siliconforge flow run . --stages lint,synth    # leave the layout out
siliconforge flow report .siliconforge/flow/pio16-20261121-153000     # re-render report.html
siliconforge flow diff run-a/report.json run-b/report.json
```

## The project section

`flow init` adds a `project:` section to `siliconforge.yaml` (other keys are left alone):

```yaml
project:
  top: pio16                       # the module to implement
  sources: [rtl/*.v, gen/rtl/*.v]  # files or globs relative to this file; order kept, duplicates dropped
  include_dirs: [gen/rtl]          # `include search path (each source's own folder is searched too)
  defines: {SYNTHESIS: 1}          # preprocessor defines
  clock: {port: clk, period_ns: 20}
  sdc: constraints/top.sdc         # optional; without it the flow constrains the clock only
  pdk: sky130A
  vars: {FP_CORE_UTIL: 40}         # extra LibreLane variables, restricted to an allowlist (see project.py)
  waive: [antenna]                 # signoff checks you accept as failing; they show as waived in the report
```

`flow check` finds what it can without running anything: missing files, a top module that no source defines, a clock that
is not one of its ports, a negative period, a misspelled LibreLane variable. Comments are ignored when looking for module
names, so `// module old_version;` does not count.

## What each stage does

| Stage | Tool | Passes when |
| --- | --- | --- |
| `lint` | Verilator, `--lint-only -Wall`, multi-file, include dirs and defines | No `%Error`. Warnings are counted by kind and reported, not failed. |
| `synth` | Yosys | Synthesis completes and the flattened RTL is proven equivalent to the flattened gate-level netlist (`equiv_simple`, `equiv_induct`). `--no-lec` skips the proof and says so. |
| `pnr` | LibreLane (OpenROAD, Magic, KLayout, Netgen) | The signoff gates below are clean. |

A stage that cannot run (tool missing, timeout) is an **error** with the reason. It is never reported as a pass. A stage is
**skipped** only when you left it out with `--stages`. The overall result is `clean`, `failed` or `incomplete`; `incomplete`
means something could not run or a required metric was not reported, and the report says that this is not a pass.

## Signoff gates

Read from LibreLane's `metrics.json`. Each gate is `pass`, `fail` or `not reported`. A metric the run did not emit is never
counted as a pass.

| Gate | Required | Limit |
| --- | --- | --- |
| Setup slack, worst corner; setup violations | yes | slack >= 0 ns; 0 violations |
| Hold slack, worst corner; hold violations | yes | slack >= 0 ns; 0 violations |
| Design rule check (Magic, KLayout, router, summed) | yes | 0 |
| Layout versus schematic | yes | 0 |
| Antenna; layout versus stream-out (XOR); linter errors | no | 0 |
| Max slew, capacitance, fanout | no | 0 |
| IR drop, worst voltage | no | reported, no limit |

Per-corner setup and hold slack are listed worst first. A waived check is shown as waived with the original value.

## Reproducibility and provenance

Each report records the runner (`native`, `wsl` or `docker`), the version of Verilator, Yosys and LibreLane, a fingerprint of
the sources, constraints and settings, and a hash of the exact LibreLane configuration. `flow diff` says whether two reports
used the same inputs, which tools changed, and what moved in area, power, slack and the individual gates.

### Pinning tools with `flow.lock`

```bash
siliconforge flow lock .              # writes flow.lock with the installed Verilator, Yosys and LibreLane versions and the PDK name
siliconforge flow lock . --check      # exit 3 and list the differences if anything installed has changed
siliconforge flow run . --locked      # refuse to start (exit 3, nothing run) when an installed tool differs from the lock
```

Commit `flow.lock` with the project. A tool that is missing never matches, and a lock cannot be written while a tool is missing.
The lock pins tools and the PDK *name*; it does not pin the RTL (the report's fingerprint identifies that) or the PDK revision,
which is installed outside these tools. Checked on the reference machine: `lock` then `lock --check` passes, and a lock edited to
a different Yosys version makes `run --locked` refuse.

## A worked result

`pio16` (a 16-bit programmable pin engine: two source files and an include directory) on sky130A with a 20 ns clock, through
WSL, tools Verilator 5.055, Yosys 0.71 and LibreLane 3.0.17. Result in `benchmarks/results/flow-pio16-sky130A-2026-11-21.json`:

- lint clean (2 warnings), flattened equivalence proven, 1,894 generic cells;
- worst setup slack +1.99 ns (slow corner), worst hold slack +0.11 ns, DRC, LVS, antenna and XOR all zero, GDSII produced;
- **1,097 max-slew, 49 max-capacitance and 22 max-fanout violations**, so the electrical gates fail and the overall result is `failed`.

A second run on identical inputs gave identical area, cell count, power, slack and gate outcomes (`flow diff` lists no
change), so on this design and machine the flow was repeatable. That is two runs, not a guarantee.

## In CI: the GitHub Action

`.github/actions/flow` runs the flow on every push and uploads the report, failing the job when a signoff gate fails. It runs
`siliconforge flow check`, then `flow run --locked`, so a runner whose tools differ from `flow.lock` stops instead of producing a
number that is not comparable. The report (`report.html`, `report.json`) is uploaded even when the flow fails.

```yaml
# .github/workflows/flow.yml in your project
on: [push, pull_request]
jobs:
  flow:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: YosysHQ/setup-oss-cad-suite@v3      # Yosys, SymbiYosys, Verilator on PATH
      - uses: PundarikakshNTripathi/silicon-forge/.github/actions/flow@main
        with:
          path: .
          backend: native
          version: main        # a tag or branch of SiliconForge; there is no PyPI release yet
```

Place and route needs LibreLane and the PDK as well; for the full flow use `backend: docker` on a runner with the image, or run only
`--stages lint,synth` in CI with `extra-args`. Inputs reach the shell only through environment variables (no `${{ }}` inside `run:`),
which a test checks (`tests/test_github_action.py`).

## Limits

- The front end is Yosys' `read_verilog -sv`. It handles the synthesizable Verilog-2001 and common SystemVerilog subset; designs
  that lean on interfaces, packages with parameterized types or classes need a different front end, which is not wired in.
- The equivalence check compares RTL with Yosys' generic synthesis result, not with the technology-mapped netlist that
  LibreLane produces. It catches synthesis and flattening problems, not a defect in the PDK mapping step.
- The signoff gates are only as complete as the metrics a run emits. Antenna, slew and similar gates read `not reported`
  until the flow steps that produce them have run.
- No clock-domain-crossing analysis, no hierarchical (macro) hardening and no SRAM macro integration yet. Multi-corner timing is
  whatever LibreLane's PDK configuration provides.
- These are open tools. This page makes no claim of parity with commercial signoff tools; the measured results are in
  `benchmarks/results/`.
