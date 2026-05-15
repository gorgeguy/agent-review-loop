.PHONY: install lint format shadow typecheck test test-cov ci clean

install:  ## Install dependencies and pre-commit hooks
	uv sync
	uv run pre-commit install

lint:  ## Run linter
	uv run --frozen ruff check .

format:  ## Format code
	uv run --frozen ruff format .
	uv run --frozen ruff check . --fix

typecheck:  ## Run type checker
	uv run --frozen pyright

test:  ## Run tests
	uv run --frozen pytest

test-cov:  ## Run tests with coverage
	uv run --frozen pytest --cov --cov-report=term-missing

shadow:  ## Run shadow scan
	uv run --frozen pre-commit run py-shadow-scan --all-files

ci: lint shadow typecheck test  ## Run all checks (what CI runs)

clean:  ## Remove build artifacts
	rm -rf dist/ build/ *.egg-info/ .pytest_cache/ htmlcov/ .coverage .pyright/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
