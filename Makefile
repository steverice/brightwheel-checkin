.PHONY: lint format test test-integ check help

PY := build_shortcuts.py make_diagrams.py make_icons.py make_og_image.py tools/ tests/

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

lint: ## Run linters (ruff check + ruff format check + ty)
	uv run ruff check $(PY)
	uv run ruff format --check $(PY)
	uv run ty check $(PY)

format: ## Auto-fix formatting and lint issues
	uv run ruff format $(PY)
	uv run ruff check --fix $(PY)

test: ## Run unit tests (no simulator)
	uv run python -m pytest

test-integ: ## Run the simulator suite (./test.sh)
	./test.sh

check: lint test ## Run all checks (lint + unit tests)
