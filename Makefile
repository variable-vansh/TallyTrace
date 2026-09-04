PY := .venv/bin/python
PIP := .venv/bin/pip

.DEFAULT_GOAL := help

.PHONY: help venv generate verify-clean reconcile learn claims reporting ask llm-fixtures resolutions ui-data demo score whatif ceilings test typecheck check clean

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

reconcile:  ## Run the deterministic matcher across all ten batches
	$(PY) -m pipeline.run

learn:  ## Run the learning loop across the ten batches; writes data/rules.json + data/learning.json
	$(PY) -m pipeline.learn

claims:  ## Show the claims queue at the end of the corpus, sorted by expiry
	$(PY) -m pipeline.claims.cli --offline

ask:  ## Ask the metric registry a question:  make ask q="how much are we still chasing?"
	$(PY) -m tools.ask --offline --yes "$(q)"

resolutions:  ## Rebuild the operator work log from tools/operator_notes.py
	$(PY) -m tools.write_resolutions

reporting:  ## Rebuild data/questions.json + data/pins.json from tools/operator_questions.py
	$(PY) -m tools.write_reporting --offline

llm-fixtures:  ## Populate data/llm_cache. With ANTHROPIC_API_KEY set this calls the API.
	$(PY) -m tools.write_llm_fixtures

ui-data:  ## Build the JSON the React UI reads
	$(PY) -m tools.build_ui_data --offline

score:  ## Score the pipeline against the answer key; writes EXCEPTIONS.md + data/score.json
	$(PY) -m harness.score --offline

whatif:  ## Score under a different ceiling without changing policy:  make whatif ceiling=3000
	$(PY) -m harness.score --offline --max-variance-inr "$(ceiling)"

ceilings:  ## Score every candidate ceiling and write the curve the threshold control reads
	$(PY) -m tools.ceiling_sweep --offline

demo: generate score ui-data  ## Everything, end to end, offline. Run it twice: the numbers do not move.

reproduce:  ## Prove it: run `make demo` twice from scratch and diff the artifacts
	$(PY) -m tools.reproduce

test:  ## Run the test suite
	$(PY) -m pytest tests/ -q

typecheck:  ## mypy over the source tree
	$(PY) -m mypy generator pipeline harness tools

check: verify-clean test typecheck  ## Everything a checkpoint has to pass

clean:  ## Remove generated data and caches
	rm -rf data/generated/batch_* data/truth/*.json data/reconciliation.json data/score.json
	rm -f data/learning.json data/rules.json ui/public/tallytrace.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache
