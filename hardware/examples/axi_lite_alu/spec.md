# Hardware Specification: `axi_lite_alu` (32-bit Registered Valid/Ready ALU)

## Target Node & PPA Budget
- **PDK**: SkyWater 130nm (`sky130A`, `sky130_fd_sc_hd`)
- **Target Clock**: 100 MHz (`CLOCK_PERIOD = 10.0 ns`, `WNS >= 0.0 ns`)
- **Max Core Area**: 25,000 um²
- **Max Total Power**: 5.0 mW

## Interface
- `clk` (input, 1-bit): Rising-edge system clock.
- `rst_n` (input, 1-bit): Active-low asynchronous reset.
- `valid_i` (input, 1-bit) / `ready_o` (output, 1-bit): Input request handshake.
- `op_i` (input, 3-bit): `0=ADD`, `1=SUB`, `2=AND`, `3=OR`, `4=XOR`, default `0`.
- `a_i`, `b_i` (input, 32-bit): Unsigned 32-bit operands.
- `valid_o` (output, 1-bit): Registered output valid (1-cycle latency).
- `result_o` (output, 32-bit): Registered 32-bit ALU result.
- `zero_o` (output, 1-bit): Asserted iff `result_o == 32'd0`.

## Formal & Functional Requirements
1. Immediately after reset (`!rst_n`), `valid_o == 0` and `result_o == 0`.
2. Whenever `valid_o == 1`, `zero_o == (result_o == 32'd0)` must hold mathematically across all reachable states (k-induction).
3. Non-vacuous reachability (`cover`): A cycle with `valid_o == 1 && result_o != 32'd0` must be reachable from reset.
