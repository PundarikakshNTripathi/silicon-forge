# Sky130A PDK Physical & Synthesis Rules
- **Node**: SkyWater 130nm (`sky130A`), standard cell library `sky130_fd_sc_hd`, nominal 1.8V.
- **Clock Budget**: Practical single-cycle logic depth at 100 MHz (`CLOCK_PERIOD: 10.0 ns`) is ~12–15 FO4 stages; avoid deep combinational multipliers without pipelining.
- **Allowed Cells**: All standard cells must start with `sky130_fd_sc_hd__` (e.g., `sky130_fd_sc_hd__clkbuf_4`, `sky130_fd_sc_hd__dfxtp_1`).
- **Metal Stack**: `li1`, `met1`–`met5`. Place horizontal pins on `met3`, vertical pins on `met2`.
- **Utilization**: Start `FP_CORE_UTIL` at 40–50% and `PL_TARGET_DENSITY_PCT` at 55–65% to avoid Sky130 routing congestion on `met1`/`met2`.
