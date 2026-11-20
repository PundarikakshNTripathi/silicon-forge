# SiliconForge Production Yosys Synthesis Script (Sky130A / ASAP7)
yosys -import

set design_name   [expr {[info exists ::env(DESIGN_NAME)] ? $::env(DESIGN_NAME) : "axi_lite_alu"}]
set rtl_file      [expr {[info exists ::env(RTL_FILE)] ? $::env(RTL_FILE) : "rtl.sv"}]
set liberty_file  [expr {[info exists ::env(LIBERTY_FILE)] ? $::env(LIBERTY_FILE) : "sky130_fd_sc_hd__tt_025C_1v80.lib"}]
set out_netlist   [expr {[info exists ::env(OUT_NETLIST)] ? $::env(OUT_NETLIST) : "synth_netlist.v"}]
set synth_strat   [expr {[info exists ::env(SYNTH_STRATEGY)] ? $::env(SYNTH_STRATEGY) : "AREA 0"}]

read_verilog -sv $rtl_file
hierarchy -check -top $design_name

# High-level synthesis & optimization
proc
opt_expr
opt_clean
check
opt -nodffe -nosdff
fsm
opt
wreduce
peepopt
opt_clean
alumacc
share
opt
memory -nomap
opt_clean
memory_map
opt -full

# Technology mapping
techmap
opt -fast
dfflibmap -liberty $liberty_file
abc -liberty $liberty_file -D [expr {[info exists ::env(CLOCK_PERIOD_PS)] ? $::env(CLOCK_PERIOD_PS) : 10000}]
opt_clean -purge

# Statistics & gate-level netlist export
stat -liberty $liberty_file
check -assert
write_verilog -noattr -noexpr -nohex -nodec $out_netlist
