// Unit tests for sf-telemetry. Uses an always-on CHECK macro: assert() is compiled out under -DNDEBUG
// (CMake Release), which previously made these tests pass vacuously.
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <string>

#include "ppa_parser.hpp"
#include "rtl_static_check.hpp"

static int g_failures = 0;

#define CHECK(cond)                                                                    \
    do {                                                                               \
        if (!(cond)) {                                                                 \
            std::cerr << "CHECK FAILED " << __FILE__ << ":" << __LINE__ << "  " #cond "\n"; \
            ++g_failures;                                                              \
        }                                                                              \
    } while (0)

#define CHECK_NEAR(a, b, tol) CHECK(std::fabs((a) - (b)) < (tol))

static void test_legacy_log_format() {
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
    CHECK(ppa.parsed_timing);
    CHECK(ppa.parsed_area);
    CHECK(ppa.parsed_power);
    CHECK_NEAR(ppa.wns_ns, -0.18, 1e-4);
    CHECK_NEAR(ppa.tns_ns, -1.42, 1e-4);
    CHECK_NEAR(ppa.area_um2, 18450.25, 1e-3);
    CHECK_NEAR(ppa.total_power_mw, 2.1, 1e-3);
    CHECK(ppa.cell_count == 1420);
    CHECK(ppa.drc_violations == 0);
    CHECK(ppa.lvs_clean);
}

// Excerpts from real Yosys 0.7x `stat` and LibreLane/OpenROAD multi-corner reports.
static void test_real_tool_log_format() {
    const std::string real_log = R"(
      903  8.7E+03 cells
   Chip area for module '\axi_lite_alu': 8698.342400
Writing metric timing__setup__wns__corner:nom_tt_025C_1v80: 2.3576075092822975
Writing metric timing__setup__wns__corner:nom_ss_100C_1v60: -2.74559760476037
Writing metric timing__setup__wns__corner:nom_ff_n40C_1v95: 4.458865847196226
Writing metric timing__setup__tns__corner:nom_tt_025C_1v80: 0.0
Writing metric timing__setup__tns__corner:nom_ss_100C_1v60: -13.235921178159826
Total                5.767137e-04 3.679634e-04 4.809801e-09 9.446820e-04 100.0%
Total                1.000000e-03 3.679634e-04 4.809801e-09 1.132800e-03 100.0%
)";
    auto ppa = siliconforge::parse_eda_logs(real_log);
    CHECK(ppa.cell_count == 903);
    CHECK_NEAR(ppa.area_um2, 8698.3424, 1e-3);
    CHECK_NEAR(ppa.wns_ns, -2.74559760476037, 1e-6);   // worst corner wins
    CHECK_NEAR(ppa.tns_ns, -13.235921178159826, 1e-6);
    CHECK_NEAR(ppa.total_power_mw, 1.1328, 1e-4);      // worst (largest) corner power, W -> mW
}

static void test_lint() {
    const std::string clean_rtl = R"(
module good_alu (input logic clk, input logic rst_n, input logic [7:0] a, output logic [7:0] y);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) y <= 8'd0;
    else y <= a;
  end
endmodule
)";
    auto clean_rep = siliconforge::lint_systemverilog(clean_rtl);
    CHECK(clean_rep.passed);
    CHECK(clean_rep.error_count == 0);

    const std::string buggy_rtl = R"(
module bad_alu (input logic clk, input logic a, output logic y);
  initial y = 0;
  always_ff @(posedge clk) begin
    #5 y = a;
  end
endmodule
)";
    auto buggy_rep = siliconforge::lint_systemverilog(buggy_rtl);
    CHECK(!buggy_rep.passed);
    CHECK(buggy_rep.error_count >= 3);
}

int main() {
    test_legacy_log_format();
    test_real_tool_log_format();
    test_lint();
    if (g_failures != 0) {
        std::cerr << g_failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "[PASS] All sf-telemetry unit tests passed.\n";
    return 0;
}
