#pragma once

#include <string>

namespace siliconforge {

struct PpaTelemetry {
    double wns_ns = 0.0;
    double tns_ns = 0.0;
    double area_um2 = 0.0;
    double total_power_mw = 0.0;
    int cell_count = 0;
    int drc_violations = 0;
    bool lvs_clean = true;
    bool parsed_timing = false;
    bool parsed_area = false;
    bool parsed_power = false;
};

// Parses concatenated or single OpenSTA / Yosys / OpenROAD / Magic / Netgen logs.
PpaTelemetry parse_eda_logs(const std::string& log_text);

// Serializes PpaTelemetry to compact valid JSON.
std::string to_json(const PpaTelemetry& t);

}  // namespace siliconforge
