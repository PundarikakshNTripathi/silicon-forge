# SiliconForge Production OpenROAD Floorplan -> Placement -> CTS -> Routing -> STA Script
set design_name  $::env(DESIGN_NAME)
set netlist      $::env(NETLIST_FILE)
set sdc_file     $::env(SDC_FILE)
set tech_lef     $::env(TECH_LEF)
set cell_lef     $::env(CELL_LEF)
set liberty_file $::env(LIBERTY_FILE)
set core_util    [expr {[info exists ::env(FP_CORE_UTIL)] ? $::env(FP_CORE_UTIL) : 45}]
set pl_density   [expr {[info exists ::env(PL_TARGET_DENSITY)] ? $::env(PL_TARGET_DENSITY) : 0.60}]
set grt_adj      [expr {[info exists ::env(GRT_ADJUSTMENT)] ? $::env(GRT_ADJUSTMENT) : 0.30}]

read_lef $tech_lef
read_lef $cell_lef
read_liberty $liberty_file
read_verilog $netlist
link_design $design_name
read_sdc $sdc_file

# 1. Floorplanning
initialize_floorplan -utilization $core_util -aspect_ratio 1.0 -core_space 4.0 -site unithd
make_tracks
place_pins -hor_layers met3 -ver_layers met2

# 2. Global & Detailed Placement
global_placement -density $pl_density -routability_driven -timing_driven
detailed_placement
check_placement -verbose

# 3. Clock Tree Synthesis & Hold/Setup Repair
clock_tree_synthesis -root_buf sky130_fd_sc_hd__clkbuf_16 -buf_list "sky130_fd_sc_hd__clkbuf_4 sky130_fd_sc_hd__clkbuf_8"
repair_clock_nets
repair_timing -setup -hold
detailed_placement

# 4. Global & Detailed Routing
set_global_routing_layer_adjustment met1-met5 $grt_adj
global_route -congestion_iterations 50 -allow_congestion
detailed_route -output_drc route_drc.rpt

# 5. Signoff Timing, Area & Power Telemetry (parsed by C++20 sf-telemetry)
report_wns
report_tns
report_design_area
report_power
write_def "${design_name}_routed.def"
