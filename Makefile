# Thin wrapper over the cross-platform task runner (scripts/dev.py).
# On Windows without make, call `python scripts/dev.py <task>` directly.
PY ?= python3

.PHONY: all build-cpp test test-cpp test-py lint demo doctor docker-up clean

all: build-cpp test

build-cpp test test-cpp test-py lint demo doctor:
	@$(PY) scripts/dev.py $@

docker-up:
	docker compose -f docker/docker-compose.yml up --build

clean:
	rm -rf services/telemetry_engine/build runs .pytest_cache .ruff_cache
