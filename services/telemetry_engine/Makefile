CXX := g++
CXXFLAGS := -std=c++20 -O2 -Wall -Wextra -Iinclude

BUILD_DIR := build
TARGET := $(BUILD_DIR)/sf-telemetry
TEST_BIN := $(BUILD_DIR)/test_telemetry

SRCS := src/ppa_parser.cpp src/rtl_static_check.cpp
MAIN_SRC := src/main.cpp
TEST_SRC := tests/test_telemetry.cpp

.PHONY: all test clean

all: $(TARGET)

$(BUILD_DIR):
	@mkdir -p $(BUILD_DIR)

$(TARGET): $(BUILD_DIR) $(SRCS) $(MAIN_SRC) include/ppa_parser.hpp include/rtl_static_check.hpp
	$(CXX) $(CXXFLAGS) $(SRCS) $(MAIN_SRC) -o $(TARGET)

$(TEST_BIN): $(BUILD_DIR) $(SRCS) $(TEST_SRC) include/ppa_parser.hpp include/rtl_static_check.hpp
	$(CXX) $(CXXFLAGS) $(SRCS) $(TEST_SRC) -o $(TEST_BIN)

test: $(TEST_BIN)
	./$(TEST_BIN)

clean:
	rm -rf $(BUILD_DIR)
