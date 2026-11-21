# SDC timing constraints, the essentials

SDC (Synopsys Design Constraints) is the language every STA tool, OpenSTA included, reads. Times are in nanoseconds in the
flows used here. Without constraints, timing analysis has nothing to check.

## create_clock defining the clock
`create_clock -name clk -period 10.0 [get_ports clk]` declares a 100 MHz clock on the port `clk`. Every flip-flop in the
design is timed against it. The period in the project (`clock.period_ns`) and the SDC must agree.

## set_input_delay set_output_delay port timing
`set_input_delay -clock clk 2.0 [get_ports data_in]` says the input arrives 2 ns after the clock edge, so the logic behind it
has `period - 2.0` ns. `set_output_delay -clock clk 2.0 [get_ports data_out]` reserves 2 ns outside the chip. Use
`-max` and `-min` when the early and late arrival differ. Do not apply an input delay to the clock port itself.

## set_clock_uncertainty margin for jitter and skew
`set_clock_uncertainty 0.25 [get_clocks clk]` subtracts a margin from every setup check so that the design is not closed with
zero spare time. Typical values are a few percent of the period for a pre-layout run.

## set_false_path paths that never matter
`set_false_path -from [get_ports rst_n]` removes paths that cannot be exercised in operation, such as an asynchronous reset
input or a static configuration register. Use it for a reason you can state; each false path hides a violation from the report.

## set_multicycle_path paths allowed more than one cycle
`set_multicycle_path 2 -setup -from [get_pins a/Q] -to [get_pins b/D]` allows two cycles, and the hold relationship must be
moved with `set_multicycle_path 1 -hold` on the same path. The RTL must really hold the data for two cycles.

## set_driving_cell set_load realistic port conditions
`set_driving_cell -lib_cell sky130_fd_sc_hd__buf_2 [all_inputs]` and `set_load 0.05 [all_outputs]` give the ports a realistic
drive and load so that slew and delay at the boundary are not optimistic.

## What the flow generates for you
If a project gives no SDC file, the flow constrains the clock from `CLOCK_PORT` and `CLOCK_PERIOD` and applies its default input
and output delays. A hand-written SDC replaces that default, so it must include the clock definition itself.
