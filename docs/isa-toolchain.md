# ISA toolchain

Write an instruction set once, in `isa.yaml`, and get everything that has to agree with it:

| Generated | By | Why it matters |
|---|---|---|
| Assembler + disassembler | `siliconforge isa asm` / `disasm` | labels, `.equ`, `.org`, expressions, range-checked operands, every error with a line number |
| Verilog decoder + header | `siliconforge isa gen` | Verilog-2001, one-hot `is_<instr>` outputs and `illegal`, operand macros; no packages, so any Yosys flow takes it |
| Formal decoder check | same | an SBY harness proves the decoder is one-hot and that `illegal` means exactly "nothing matched" |
| Golden model | `Machine` (`isa run`) | executes real words decoded by the same code the disassembler uses; state writes wrap to hardware widths |
| Timing checker | `siliconforge isa check` | cycle gaps between pin events, proved from the control-flow graph and measured on the model |
| Documentation | `docs/isa.md` + one bit-field SVG per instruction | regenerated, so it cannot go stale |
| Standalone bundle | `tools/<name>_isa.py` | the assembler, model and checker in one standard-library file for cocotb tests and CI |

```bash
siliconforge isa gen hardware/isa/pio16/isa.yaml --out build/pio16
siliconforge isa asm hardware/isa/pio16/isa.yaml hardware/isa/pio16/uart_tx.asm --hex uart_tx.hex
siliconforge isa check hardware/isa/pio16/isa.yaml hardware/isa/pio16/uart_tx.asm
siliconforge isa run hardware/isa/pio16/isa.yaml program.asm --steps 40 --input pins=0x04
```

## Writing the spec

An encoding is a pattern of `word_bits` characters, MSB first: `0`/`1` are fixed bits the decoder matches, `-` is unused,
and a letter is an operand field (one contiguous run per letter).

```yaml
- name: jpin
  encoding: "1000 -ppp --- ttttt"
  asm: "jpin {p}, {t}"
  operands: {p: {name: p}, t: {name: t, kind: label}}
  flow: {kind: cond, target: t}
  exec: |
    if (inp.get("pins") >> p) & 1:
        s.pc = t
```

`cycles` is an expression over the operands (`"1 + n"`). `pin_event: true` marks the instructions the timing checker
measures between. `exec` runs on a state object `s` (the program counter already points at the next instruction, so a jump
just assigns `s.pc`) and an `inp` object for external inputs; `stall()` repeats the instruction next cycle. These snippets
are code from your own spec, so only load specs you trust.

The loader refuses ambiguous specs: if two encodings can match the same word, an operand is missing from the encoding, an
enum does not fit its field, or an `asm` template does not use exactly the operands the encoding has, you get an error
instead of a toolchain that quietly disagrees with itself.

## Timing checks

Bit-banged protocols fail on timing. In the assembly, `.expect N` on a pin event says it must happen exactly `N` cycles
after the previous one, and `.bound N` before a branch says a loop's branch is taken at most `N` times.

- **Static** analysis walks the control-flow graph and gives `[min, max]` cycles from each pin event to the next. If
  `min == max` the gap cannot jitter whatever the data is. A loop with no `.bound` has an open maximum.
- **Dynamic** analysis runs the golden model and measures the real gaps for chosen inputs.
- `.expect` is checked against both, and the report says whether the control-flow graph proves it or it was only
  observed. In the UART example every one of the 11 bit gaps is proven to be 16 cycles.

## What this does and does not cover

It proves the *toolchain* is self-consistent and the decoder is right. It does not prove your datapath implements the
semantics in `exec`: that is the job of lockstep co-simulation of the RTL against the golden model.
