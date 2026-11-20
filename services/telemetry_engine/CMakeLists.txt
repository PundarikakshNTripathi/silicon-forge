cmake_minimum_required(VERSION 3.16)
project(siliconforge_telemetry LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(sf_telemetry_core STATIC
    src/ppa_parser.cpp
    src/rtl_static_check.cpp
)
target_include_directories(sf_telemetry_core PUBLIC include)

add_executable(sf-telemetry src/main.cpp)
target_link_libraries(sf-telemetry PRIVATE sf_telemetry_core)

enable_testing()
add_executable(test_telemetry tests/test_telemetry.cpp)
target_link_libraries(test_telemetry PRIVATE sf_telemetry_core)
add_test(NAME TelemetryUnitTests COMMAND test_telemetry)
