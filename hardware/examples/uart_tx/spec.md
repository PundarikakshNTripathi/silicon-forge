# Hardware Specification: `uart_tx` (8N1 UART transmitter)

## Target Node & PPA Budget
- **PDK**: SkyWater 130nm (`sky130A`, `sky130_fd_sc_hd`)
- **Target Clock**: 100 MHz (`CLOCK_PERIOD = 10.0 ns`)
- **Max Core Area**: 25,000 um² / **Max Power**: 5.0 mW

## Interface
- `clk`, `rst_n` (active-low async reset).
- `start_i` (1-bit): begin sending `data_i[7:0]` (ignored while `busy_o`).
- `tx_o`: serial output, **idles high**. Frame = start bit (0), 8 data bits LSB first, stop bit (1).
- `busy_o`: high while a frame is being transmitted. Fixed divider: 4 clock cycles per bit.

## Formal & Functional Requirements
1. After reset `tx_o == 1` and `busy_o == 0`.
2. Whenever `busy_o == 0`, `tx_o == 1` (idle line high).
3. A `start_i` while idle makes `busy_o` high within 2 cycles.
4. Non-vacuity (`cover`): `busy_o` is reachable.
