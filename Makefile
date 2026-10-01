PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin
CHURN  := examples/churn/pipeline.py

.PHONY: install test lint format demo rerun ui serve record docker clean

$(BIN)/conveyor: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e '.[dev]'
	@touch $@

install: $(BIN)/conveyor

test: install
	$(BIN)/pytest -q

lint: install
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: install
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

demo: install
	$(BIN)/conveyor run $(CHURN)

# Run again and fail unless every step came from the cache.
rerun: install
	$(BIN)/conveyor run $(CHURN) --quiet
	$(BIN)/conveyor show latest --json | $(BIN)/python scripts/assert_cached.py

ui: install
	$(BIN)/conveyor ui

serve: install
	$(BIN)/conveyor serve churn

record: install
	$(BIN)/python scripts/record_demo.py

docker:
	docker build -t conveyor .

clean:
	rm -rf .conveyor .pytest_cache .ruff_cache build dist *.egg-info
