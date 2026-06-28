.PHONY: help install dev-install test lint format clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install production dependencies
	pip install -e .

dev-install:  ## Install with dev dependencies
	pip install -e ".[dev]"

test:  ## Run all tests
	pytest -v

test-unit:  ## Run unit tests only
	pytest -v tests/unit/

test-integration:  ## Run integration tests only
	pytest -v tests/integration/

test-cov:  ## Run tests with coverage report
	pytest --cov=. --cov-report=term --cov-report=html

lint:  ## Run linter
	ruff check .

format:  ## Auto-format code
	ruff check --fix .
	ruff format .

clean:  ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/ 2>/dev/null || true
