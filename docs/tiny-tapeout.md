# Tiny Tapeout (IHP SG13CMOS5L)

Support for designing and checking a [Tiny Tapeout](https://tinytapeout.com) project on the IHP 130 nm CMOS5L process,
the target of the Jane Street x Tiny Tapeout protocol-emulator competition. It scaffolds the project, checks it,
sizes it against the tile budget with the real PDK, and runs gate-level simulation. Hardening and the precheck stay
with the official flow, and this page is explicit about the boundary.

```bash
siliconforge tt scaffold my-chip --top tt_um_yourname_chip --title "My chip" --author "You" --tiles 4x4
cd my-chip && siliconforge init . --backend wsl --force
siliconforge tt check --synth       # info.yaml, ports, reset, latches, area against the tile budget
siliconforge tt synth               # real IHP cells and area, and every tile size
siliconforge tt gls                 # your cocotb tests on the gate-level netlist with the PDK cell models
siliconforge tt pack                # SUBMISSION.md checklist
siliconforge tt doctor              # Docker state and the LibreLane / PDK versions Tiny Tapeout pins
```

## One-time setup: the PDK (no Docker needed)

```bash
pip install "ciel>=3"
ciel enable --pdk-family ihp-sg13cmos5l --pdk-root ~/pdk 89d42ee6d2fd9b50b86a1aada6fee6673c3e6008
```

About 1 GB. That hash is the version the official `tt-gds-action@ttihp27a` precheck uses. SiliconForge reads the liberty
files and Verilog cell models from `$PDK_ROOT` (default `~/pdk`, inside WSL when that is your backend).

## What each command really does

| Command | Real or estimate | Notes |
|---|---|---|
| `tt check` | static | `info.yaml` rules (name prefix, tile size, 24 pins, sources exist), top-module ports, reset used, outputs driven, `initial`/`#` flagged. With `--synth`, also area and latches. |
| `tt synth` | **real**, post-synthesis | Yosys + `abc` against the real `sg13cmos5l` typical-corner liberty: true cell counts and area. Before placement, clock tree, hold fixing and routing. |
| tile budget | estimate | post-synthesis area times a 1.2 margin (an assumption, stated in the output) divided by the die area of each tile size (from Tiny Tapeout's `tile_sizes.yaml`). Under 50% is comfortable, 50 to 65% is tight. Measure after hardening. |
| `tt gls` | **real** simulation | Your own cocotb tests on SiliconForge's post-synthesis netlist with the PDK's UDP and standard-cell models. Catches synthesis mismatches and X/reset problems that RTL simulation hides. It is **not** the hardened netlist. |
| `tt harden` | needs Docker | The official flow runs LibreLane `3.1.0.dev4` in a container. The command checks the engine, and otherwise tells you to start Docker Desktop or push to GitHub, where the `gds` workflow hardens the project and runs the precheck. |

Not covered locally: timing signoff, DRC, LVS, the official precheck, SDF-annotated timing simulation. These need the
hardened layout. Do not read a green `tt check` as "will be accepted".

## What the worked example showed

Synthesizing the `pio16` teaching engine (`hardware/isa/pio16`) gave 1,723 cells and 43,544 um2, of which **63% is
flip-flops**: the 32 x 16 program memory built from registers dominates. At the 1.2 margin that needs a 2x2 tile, not 1x1.
Seeing this on day one, with the real library, is the point: a design that carries its memory in flip-flops spends its
tile budget on storage before it does any work, which is why the strongest public entries use latch arrays or SRAM macros.

## Notes found while building this

- The pinned flow for this PDK is newer than the LibreLane in the local AppImage (3.0.17), which is why local hardening
  goes through Docker rather than the bundled tools.
- Gate-level tests that change inputs in the same time step as the clock edge disagree between RTL and gate-level
  simulators (they order delta cycles differently). Drive and sample on the falling edge.
