# Hardware Specification: `up_down_counter` (8-bit loadable up/down counter)

## Target Node & PPA Budget
- **PDK**: SkyWater 130nm (`sky130A`, `sky130_fd_sc_hd`)
- **Target Clock**: 100 MHz (`CLOCK_PERIOD = 10.0 ns`)
- **Max Core Area**: 25,000 um²
- **Max Total Power**: 5.0 mW

## Interface
- `clk` (input, 1-bit): Rising-edge system clock.
- `rst_n` (input, 1-bit): Active-low asynchronous reset.
- `en_i` (input, 1-bit): Count enable.
- `load_i` (input, 1-bit): Synchronous load. **Load has priority over counting.**
- `up_i` (input, 1-bit): `1` counts up, `0` counts down.
- `load_val_i` (input, 8-bit): Value loaded when `load_i` is asserted.
- `count_o` (output, 8-bit): Registered counter value.
- `wrap_o` (output, 1-bit): Registered pulse, high for one cycle after the counter wrapped (255->0 counting up, 0->255 counting down).

## Formal & Functional Requirements
1. After reset, `count_o == 0` and `wrap_o == 0`.
2. The cycle after `load_i` (and no reset), `count_o` equals the `load_val_i` that was presented.
3. `wrap_o` is only ever high one cycle after an enabled, non-load count step.
4. Non-vacuity (`cover`): `wrap_o` is reachable.
