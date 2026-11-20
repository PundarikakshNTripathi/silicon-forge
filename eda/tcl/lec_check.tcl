# SiliconForge Formal Logical Equivalence Check (RTL vs Post-Synthesis Gate Netlist)
yosys -import

set design_name  $::env(DESIGN_NAME)
set golden_rtl   $::env(GOLDEN_RTL)
set gate_netlist $::env(GATE_NETLIST)
set cell_models  $::env(STDCELL_VERILOG)

# Load golden RTL
read_verilog -sv $golden_rtl
prep -top $design_name
design -stash gold

# Load synthesized gate netlist
read_verilog $cell_models
read_verilog $gate_netlist
prep -top $design_name
design -stash gate

# Construct miter & prove equivalence via SAT/induction
design -copy-from gold -as gold $design_name
design -copy-from gate -as gate $design_name
equiv_make gold gate equiv
prep -top equiv
equiv_simple -undef
equiv_induct -undef
equiv_status -assert
