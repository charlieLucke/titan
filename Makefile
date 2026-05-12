.PHONY: help install dev test test-fast lint format typecheck check clean run pre-commit

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies and pre-commit hooks
	uv sync
	uv run pre-commit install

dev:  ## Start development server (configure per project)
	@echo "Edit Makefile: define your dev command here"
	@echo "Example: uv run uvicorn titan.main:app --reload"

test:  ## Run tests with coverage
	uv run pytest

test-fast:  ## Run only fast tests (skip slow + integration)
	uv run pytest -m "not slow and not integration"

lint:  ## Run linter (no auto-fix)
	uv run ruff check .

format:  ## Auto-format and auto-fix lint issues
	uv run ruff format .
	uv run ruff check --fix .

typecheck:  ## Run mypy strict type checker
	uv run mypy src tests

check: lint typecheck test  ## Run full quality gate (lint + types + tests)

pre-commit:  ## Run all pre-commit hooks on all files
	uv run pre-commit run --all-files

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

run:  ## Run the main module
	uv run python -m titan
