// SPDX-License-Identifier: AGPL-3.0-or-later
#include "rtl_static_check.hpp"

#include <regex>
#include <sstream>
#include <string>

namespace siliconforge {

namespace {

std::string escape_json(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out += c; break;
        }
    }
    return out;
}

std::string strip_line_comment(const std::string& line) {
    const auto pos = line.find("//");
    if (pos == std::string::npos) return line;
    return line.substr(0, pos);
}

// Finds non-blocking assignments (`lhs <= rhs;`) in a stream of lines without mistaking the less-or-equal comparison for one.
// `<=` is an assignment only when it is the first assignment-like operator of a statement and sits outside parentheses,
// brackets and braces. So `if (count <= LIMIT)`, `y = (a <= b) ? c : d;` and `y = a <= b;` are comparisons, while `q <= d;`
// and `if (en) q <= d;` are assignments. State carries across lines because conditions and statements are often wrapped.
struct NonBlockingScanner {
    int depth = 0;
    bool blocking_seen = false;

    bool feed(const std::string& line) {
        bool found = false;
        for (std::size_t i = 0; i < line.size(); ++i) {
            const char c = line[i];
            const char next = i + 1 < line.size() ? line[i + 1] : '\0';
            const char prev = i > 0 ? line[i - 1] : '\0';
            if (c == '(' || c == '[' || c == '{') {
                ++depth;
            } else if (c == ')' || c == ']' || c == '}') {
                if (depth > 0) --depth;
            } else if (c == ';' && depth == 0) {
                blocking_seen = false;  // the statement ended
            } else if (depth == 0 && !blocking_seen) {
                if (c == '<' && next == '=' && prev != '<') {
                    found = true;
                    ++i;
                } else if (c == '=') {
                    if (next == '=') {
                        ++i;  // == or ===
                    } else if (prev != '!' && prev != '>' && prev != '<') {
                        blocking_seen = true;  // a plain `=`: whatever `<=` follows is part of the right-hand side
                    }
                }
            }
        }
        return found;
    }
};

}  // namespace

RtlLintReport lint_systemverilog(const std::string& rtl_source) {
    RtlLintReport report;
    std::istringstream iss(rtl_source);
    std::string raw_line;
    int line_no = 0;

    bool in_formal_ifdef = false;
    bool in_always_ff = false;
    bool in_always_comb = false;
    bool comb_has_if = false;
    bool comb_has_else = false;
    bool comb_has_default = false;
    int block_depth = 0;

    const std::regex re_delay(R"(#[0-9]+)");
    const std::regex re_initial(R"(\binitial\b)");
    const std::regex re_unsynth_sys(R"(\$(?:display|finish|stop|monitor|write)\b)");
    const std::regex re_blocking_assign(R"(\b[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*[^=])");
    NonBlockingScanner comb_scanner;
    const std::regex re_always_ff(R"(\balways_ff\b|\balways\s*@\s*\(\s*(?:posedge|negedge))");
    const std::regex re_always_comb(R"(\balways_comb\b|\balways\s*@\s*\(\s*\*\s*\))");
    const std::regex re_if(R"(\bif\s*\()");
    const std::regex re_else(R"(\belse\b)");
    const std::regex re_default_case(R"(\bdefault\s*:)");

    auto add_finding = [&](const std::string& id, const std::string& sev, int ln, const std::string& msg) {
        report.findings.push_back({id, sev, ln, msg});
        if (sev == "ERROR") {
            report.error_count++;
            report.passed = false;
        } else {
            report.warning_count++;
        }
    };

    while (std::getline(iss, raw_line)) {
        line_no++;
        const std::string line = strip_line_comment(raw_line);

        if (line.find("`ifdef FORMAL") != std::string::npos) {
            in_formal_ifdef = true;
            continue;
        }
        if (in_formal_ifdef && line.find("`endif") != std::string::npos) {
            in_formal_ifdef = false;
            continue;
        }
        if (in_formal_ifdef) continue;

        // Rule SF-RTL-001: Delay constructs are non-synthesizable
        if (std::regex_search(line, re_delay)) {
            add_finding("SF-RTL-001", "ERROR", line_no,
                        "Non-synthesizable #delay construct detected in RTL.");
        }
        // Rule SF-RTL-002: initial block outside FORMAL guard
        if (std::regex_search(line, re_initial)) {
            add_finding("SF-RTL-002", "ERROR", line_no,
                        "Non-synthesizable 'initial' block detected outside `ifdef FORMAL.");
        }
        // Rule SF-RTL-003: Simulation system tasks in RTL
        if (std::regex_search(line, re_unsynth_sys)) {
            add_finding("SF-RTL-003", "WARNING", line_no,
                        "Simulation system task ($display/$finish) found in synthesizable RTL.");
        }

        if (std::regex_search(line, re_always_ff)) {
            in_always_ff = true;
            block_depth = 0;
        } else if (std::regex_search(line, re_always_comb)) {
            in_always_comb = true;
            comb_scanner = NonBlockingScanner{};
            comb_has_if = false;
            comb_has_else = false;
            comb_has_default = false;
            block_depth = 0;
        }

        if (in_always_ff || in_always_comb) {
            if (line.find("begin") != std::string::npos) block_depth++;
            if (in_always_ff && std::regex_search(line, re_blocking_assign) &&
                line.find("<=") == std::string::npos &&
                line.find("==") == std::string::npos &&
                line.find("!=") == std::string::npos &&
                line.find(">=") == std::string::npos &&
                line.find("for") == std::string::npos &&
                line.find("int ") == std::string::npos) {
                add_finding("SF-RTL-004", "ERROR", line_no,
                            "Blocking assignment ('=') inside sequential always_ff block.");
            }
            if (in_always_comb) {
                if (comb_scanner.feed(line)) {
                    add_finding("SF-RTL-005", "ERROR", line_no,
                                "Non-blocking assignment ('<=') inside combinational always_comb block.");
                }
                if (std::regex_search(line, re_if)) comb_has_if = true;
                if (std::regex_search(line, re_else)) comb_has_else = true;
                if (std::regex_search(line, re_default_case)) comb_has_default = true;
            }
            if (line.find("end") != std::string::npos &&
                line.find("endmodule") == std::string::npos &&
                line.find("endcase") == std::string::npos) {
                block_depth--;
                if (block_depth <= 0) {
                    if (in_always_comb && comb_has_if && !comb_has_else && !comb_has_default) {
                        add_finding("SF-RTL-006", "WARNING", line_no,
                                    "Potential inferred latch in always_comb: 'if' without 'else' or default.");
                    }
                    in_always_ff = false;
                    in_always_comb = false;
                    block_depth = 0;
                }
            }
        }
    }

    return report;
}

std::string to_json(const RtlLintReport& report) {
    std::ostringstream oss;
    oss << "{"
        << "\"passed\":" << (report.passed ? "true" : "false") << ","
        << "\"error_count\":" << report.error_count << ","
        << "\"warning_count\":" << report.warning_count << ","
        << "\"findings\":[";
    for (size_t i = 0; i < report.findings.size(); ++i) {
        const auto& f = report.findings[i];
        if (i > 0) oss << ",";
        oss << "{"
            << "\"rule_id\":\"" << escape_json(f.rule_id) << "\","
            << "\"severity\":\"" << escape_json(f.severity) << "\","
            << "\"line_number\":" << f.line_number << ","
            << "\"message\":\"" << escape_json(f.message) << "\""
            << "}";
    }
    oss << "]}";
    return oss.str();
}

}  // namespace siliconforge
