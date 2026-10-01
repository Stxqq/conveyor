PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin
CHURN  := examples/churn/pipeline.py

.PHONY: install test lint format demo rerun ui serve record site docker clean

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

# The GitHub Pages demo: the same frontend `conveyor ui` serves, reading the
# recorded runs instead of the API.
site:
	rm -rf _site
	mkdir -p _site
	cp -R conveyor/ui/static/. _site/
	cp -R docs/runs _site/runs
	if [ -f .github/assets/social.png ]; then cp .github/assets/social.png _site/; fi

docker:
	docker build -t conveyor .

clean:
	rm -rf .conveyor .pytest_cache .ruff_cache build dist *.egg-info _site
