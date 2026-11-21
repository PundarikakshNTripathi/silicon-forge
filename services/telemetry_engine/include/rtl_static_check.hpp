// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once

#include <string>
#include <vector>

namespace siliconforge {

struct LintFinding {
    std::string rule_id;
    std::string severity;  // "ERROR" or "WARNING"
    int line_number = 0;
    std::string message;
};

struct RtlLintReport {
    bool passed = true;
    int error_count = 0;
    int warning_count = 0;
    std::vector<LintFinding> findings;
};

// Runs fast static anti-hallucination & synthesizability checks on SystemVerilog RTL.
RtlLintReport lint_systemverilog(const std::string& rtl_source);

// Serializes RtlLintReport to compact valid JSON.
std::string to_json(const RtlLintReport& report);

}  // namespace siliconforge
