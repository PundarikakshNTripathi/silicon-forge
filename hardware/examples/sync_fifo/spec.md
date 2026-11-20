# Hardware Specification: `sync_fifo` (4-entry x 8-bit synchronous FIFO)

## Target Node & PPA Budget
- **PDK**: SkyWater 130nm (`sky130A`, `sky130_fd_sc_hd`)
- **Target Clock**: 100 MHz (`CLOCK_PERIOD = 10.0 ns`)
- **Max Core Area**: 25,000 um² / **Max Power**: 5.0 mW

## Interface
- `clk`, `rst_n` (active-low async reset).
- `wr_en_i` / `wr_data_i[7:0]`: write request (ignored while `full_o`).
- `rd_en_i` / `rd_data_o[7:0]`: read request (ignored while `empty_o`); `rd_data_o` shows the oldest entry combinationally.
- `full_o`, `empty_o`, `level_o[2:0]` (0..4 entries).

## Formal & Functional Requirements
1. After reset the FIFO is empty (`level_o == 0`).
2. `full_o` and `empty_o` are never high together.
3. `level_o <= 4`, `full_o == (level_o == 4)`, `empty_o == (level_o == 0)`.
4. Non-vacuity (`cover`): the FIFO can become full.
