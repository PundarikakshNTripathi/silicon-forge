# OpenROAD / OpenSTA Verified Tcl Recipes
- **Floorplan**: `initialize_floorplan -utilization <pct> -aspect_ratio 1.0 -core_space 4.0 -site unithd`
- **Placement**: `global_placement -density <0.4..0.85> -routability_driven -timing_driven` followed by `detailed_placement`.
- **CTS & Timing Repair**: `clock_tree_synthesis`, `repair_clock_nets`, `repair_timing -setup -hold`.
- **Routing**: `set_global_routing_layer_adjustment met1-met5 <adj>`, `global_route -congestion_iterations 50`, `detailed_route`.
- **Telemetry**: `report_wns`, `report_tns`, `report_design_area`, `report_power`.
- **Forbidden**: Do NOT emit commercial-only commands (`compile_ultra`, `place_opt_design`, `opt_design`).
