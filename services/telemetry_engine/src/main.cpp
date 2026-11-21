// SPDX-License-Identifier: AGPL-3.0-or-later
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "ppa_parser.hpp"
#include "rtl_static_check.hpp"

namespace {

std::string read_file_or_stdin(const std::string& path) {
    if (path == "-") {
        std::ostringstream oss;
        oss << std::cin.rdbuf();
        return oss.str();
    }
    std::ifstream ifs(path);
    if (!ifs) {
        std::cerr << "Error: unable to open input file: " << path << "\n";
        std::exit(2);
    }
    std::ostringstream oss;
    oss << ifs.rdbuf();
    return oss.str();
}

void print_usage() {
    std::cerr << "Usage: sf-telemetry --mode <parse-logs|lint-rtl> --input <path|->\n";
}

}  // namespace

int main(int argc, char* argv[]) {
    std::string mode;
    std::string input_path = "-";

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--mode" && i + 1 < argc) {
            mode = argv[++i];
        } else if (arg == "--input" && i + 1 < argc) {
            input_path = argv[++i];
        } else if (arg == "--help" || arg == "-h") {
            print_usage();
            return 0;
        }
    }

    if (mode.empty()) {
        print_usage();
        return 1;
    }

    const std::string content = read_file_or_stdin(input_path);
    if (mode == "parse-logs") {
        auto metrics = siliconforge::parse_eda_logs(content);
        std::cout << siliconforge::to_json(metrics) << "\n";
        return 0;
    }
    if (mode == "lint-rtl") {
        auto report = siliconforge::lint_systemverilog(content);
        std::cout << siliconforge::to_json(report) << "\n";
        return report.passed ? 0 : 3;
    }

    std::cerr << "Unknown mode: " << mode << "\n";
    print_usage();
    return 1;
}
