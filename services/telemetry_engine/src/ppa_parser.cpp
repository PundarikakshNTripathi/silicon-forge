// SPDX-License-Identifier: AGPL-3.0-or-later
#include "ppa_parser.hpp"

#include <algorithm>
#include <iomanip>
#include <regex>
#include <sstream>
#include <string>

namespace siliconforge {

PpaTelemetry parse_eda_logs(const std::string& log_text) {
    PpaTelemetry out;
    std::istringstream iss(log_text);
    std::string line;

    // Regex patterns covering OpenSTA, Yosys stat, OpenROAD report_design_area, Magic/KLayout DRC, Netgen LVS
    const std::regex re_wns(R"(wns\s+(-?[0-9]+(?:\.[0-9]+)?))", std::regex::icase);
    const std::regex re_tns(R"(tns\s+(-?[0-9]+(?:\.[0-9]+)?))", std::regex::icase);
    const std::regex re_area(R"((?:Design area|Chip area for module[^:]*:)\s*([0-9]+(?:\.[0-9]+)?))", std::regex::icase);
    const std::regex re_cells(R"(Number of cells:\s*([0-9]+))", std::regex::icase);
    // Yosys >= 0.4x `stat`: "   903  8.7E+03 cells"
    const std::regex re_cells_stat(R"(^\s*([0-9]+)\s+[0-9.eE+-]+\s+cells\b)");
    // LibreLane/OpenROAD per-corner metrics: "Writing metric timing__setup__wns__corner:nom_ss_100C_1v60: -2.87"
    const std::regex re_corner_wns(R"(timing__setup__wns(?:__corner:\S+)?:\s*(-?[0-9.eE+-]+))");
    const std::regex re_corner_tns(R"(timing__setup__tns(?:__corner:\S+)?:\s*(-?[0-9.eE+-]+))");
    const std::regex re_power_total(R"(Total\s+[0-9.eE+-]+\s+[0-9.eE+-]+\s+[0-9.eE+-]+\s+([0-9.eE+-]+)\s+100\.0%)", std::regex::icase);
    const std::regex re_power_mw(R"(Total Power:\s*([0-9]+(?:\.[0-9]+)?)\s*mW)", std::regex::icase);
    const std::regex re_drc(R"((?:DRC violations|Total DRC errors|COUNT):\s*([0-9]+))", std::regex::icase);
    const std::regex re_lvs_fail(R"(Circuits do not match|LVS\s+FAIL|Netlists do not match)", std::regex::icase);
    const std::regex re_lvs_pass(R"(Circuits match uniquely|LVS\s+PASS|Netlists match)", std::regex::icase);

    std::smatch m;
    while (std::getline(iss, line)) {
        if (std::regex_search(line, m, re_wns)) {
            out.wns_ns = std::stod(m[1].str());
            out.parsed_timing = true;
        }
        if (std::regex_search(line, m, re_tns)) {
            out.tns_ns = std::stod(m[1].str());
            out.parsed_timing = true;
        }
        if (std::regex_search(line, m, re_area)) {
            out.area_um2 = std::stod(m[1].str());
            out.parsed_area = true;
        }
        if (std::regex_search(line, m, re_cells)) {
            out.cell_count = std::stoi(m[1].str());
        } else if (std::regex_search(line, m, re_cells_stat)) {
            out.cell_count = std::stoi(m[1].str());
        }
        // Multi-corner signoff: keep the worst (minimum) setup slack / TNS across corners
        if (std::regex_search(line, m, re_corner_wns)) {
            const double v = std::stod(m[1].str());
            out.wns_ns = out.parsed_timing ? std::min(out.wns_ns, v) : v;
            out.parsed_timing = true;
        } else if (std::regex_search(line, m, re_corner_tns)) {
            const double v = std::stod(m[1].str());
            out.tns_ns = std::min(out.tns_ns, v);
            out.parsed_timing = true;
        }
        if (std::regex_search(line, m, re_power_total)) {
            // OpenSTA reports Watts; convert to mW
            // multi-corner reports repeat the Total row: keep the worst (largest) power
            out.total_power_mw = std::max(out.total_power_mw, std::stod(m[1].str()) * 1000.0);
            out.parsed_power = true;
        } else if (std::regex_search(line, m, re_power_mw)) {
            out.total_power_mw = std::stod(m[1].str());
            out.parsed_power = true;
        }
        if (std::regex_search(line, m, re_drc)) {
            out.drc_violations = std::stoi(m[1].str());
        }
        if (std::regex_search(line, m, re_lvs_fail)) {
            out.lvs_clean = false;
        } else if (std::regex_search(line, m, re_lvs_pass)) {
            out.lvs_clean = true;
        }
    }

    return out;
}

std::string to_json(const PpaTelemetry& t) {
    std::ostringstream oss;
    oss << std::fixed << std::setprecision(4)
        << "{"
        << "\"wns_ns\":" << t.wns_ns << ","
        << "\"tns_ns\":" << t.tns_ns << ","
        << "\"area_um2\":" << t.area_um2 << ","
        << "\"total_power_mw\":" << t.total_power_mw << ","
        << "\"cell_count\":" << t.cell_count << ","
        << "\"drc_violations\":" << t.drc_violations << ","
        << "\"lvs_clean\":" << (t.lvs_clean ? "true" : "false") << ","
        << "\"parsed_timing\":" << (t.parsed_timing ? "true" : "false") << ","
        << "\"parsed_area\":" << (t.parsed_area ? "true" : "false") << ","
        << "\"parsed_power\":" << (t.parsed_power ? "true" : "false")
        << "}";
    return oss.str();
}

}  // namespace siliconforge
