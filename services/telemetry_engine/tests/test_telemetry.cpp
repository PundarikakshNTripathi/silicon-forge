#include <cassert>
#include <cmath>
#include <iostream>
#include <string>

#include "ppa_parser.hpp"
#include "rtl_static_check.hpp"

int main() {
    // Test 1: OpenSTA + Yosys + Magic + Netgen log parsing
    const std::string sample_log = R"(
Number of cells:                1420
Chip area for module '\axi_lite_alu': 18450.250000
wns -0.18
tns -1.42
Total 0.0012 0.0008 0.0001 0.0021 100.0%
Total DRC errors: 0
Circuits match uniquely.
)";
    auto ppa = siliconforge::parse_eda_logs(sample_log);
    assert(ppa.parsed_timing);
    assert(ppa.parsed_area);
    assert(ppa.parsed_power);
    assert(std::fabs(ppa.wns_ns - (-0.18)) < 1e-4);
    assert(std::fabs(ppa.tns_ns - (-1.42)) < 1e-4);
    assert(std::fabs(ppa.area_um2 - 18450.25) < 1e-3);
    assert(std::fabs(ppa.total_power_mw - 2.1) < 1e-3);
    assert(ppa.cell_count == 1420);
    assert(ppa.drc_violations == 0);
    assert(ppa.lvs_clean);

    // Test 2: Clean RTL passes static lint
    const std::string clean_rtl = R"(
module good_alu (input logic clk, input logic rst_n, input logic [7:0] a, output logic [7:0] y);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) y <= 8'd0;
    else y <= a;
  end
endmodule
)";
    auto clean_rep = siliconforge::lint_systemverilog(clean_rtl);
    assert(clean_rep.passed);
    assert(clean_rep.error_count == 0);

    // Test 3: Buggy RTL triggers blocking-in-seq and #delay errors
    const std::string buggy_rtl = R"(
module bad_alu (input logic clk, input logic a, output logic y);
  initial y = 0;
  always_ff @(posedge clk) begin
    #5 y = a;
  end
endmodule
)";
    auto buggy_rep = siliconforge::lint_systemverilog(buggy_rtl);
    assert(!buggy_rep.passed);
    assert(buggy_rep.error_count >= 3);

    std::cout << "[PASS] All C++20 telemetry_engine unit tests passed.\n";
    return 0;
}
