.PHONY: all build-cpp test-cpp test-py test demo clean docker-up

CPP_DIR := services/telemetry_engine
CPP_BIN := $(CPP_DIR)/build/sf-telemetry

all: build-cpp test

build-cpp:
	@echo "[Build] Compiling C++20 Telemetry & Static RTL Linter Engine..."
	@$(MAKE) -C $(CPP_DIR)

test-cpp: build-cpp
	@echo "[Test] Running C++20 Telemetry Engine unit tests..."
	@$(MAKE) -C $(CPP_DIR) test

test-py: build-cpp
	@echo "[Test] Running Python Orchestrator, Guardrails, Formal IR, RL, and E2E tests..."
	@PYTHONPATH=. python3 -m unittest discover -s tests -p "test_*.py" -v

test: test-cpp test-py

demo: build-cpp
	@echo "[Demo] Running SiliconForge autonomous Spec-to-GDSII demo..."
	@bash scripts/run_demo.sh

docker-up:
	docker compose -f docker/docker-compose.yml up --build

clean:
	@$(MAKE) -C $(CPP_DIR) clean
	@rm -rf runs/ .pytest_cache/ **/__pycache__/
