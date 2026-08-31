PY := .venv/bin/python
PIP := .venv/bin/pip

.DEFAULT_GOAL := help

.PHONY: help venv generate verify-clean test typecheck check clean

help:  ## Show the available targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-14s %s\n", $$1, $$2}'

venv:  ## Create .venv and install the pinned dependencies
	python3 -m venv .venv
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet -r requirements.txt

generate:  ## Generate the ten batches and the ground truth (seeded, reproducible)
	$(PY) -m generator.main

verify-clean:  ## Prove the clean base reconciles at 100% before anything is injected
	$(PY) -m generator.verify_clean

test:  ## Run the test suite
	$(PY) -m pytest tests/ -q

typecheck:  ## mypy over the source tree
	$(PY) -m mypy generator pipeline

check: verify-clean test typecheck  ## Everything a checkpoint has to pass

clean:  ## Remove generated data and caches
	rm -rf data/generated/batch_* data/truth/*.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache
