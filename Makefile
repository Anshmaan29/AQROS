# AQROS developer Makefile. Run `make help` for the list of targets.
.DEFAULT_GOAL := help
.PHONY: help install scaffold fmt fmt-check lint lint-fix typecheck test check \
        precommit run docker-build docker-up docker-down clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv and install all workspace packages + dev tools
	uv sync --all-packages

scaffold: ## (Re)generate service/area skeletons from scripts/scaffold_services.py
	uv run python scripts/scaffold_services.py

fmt: ## Format the codebase with Black
	uv run black .

fmt-check: ## Check formatting without modifying files
	uv run black --check .

lint: ## Lint with Ruff
	uv run ruff check .

lint-fix: ## Lint and auto-fix with Ruff
	uv run ruff check --fix .

typecheck: ## Static type-check with MyPy (strict)
	bash scripts/typecheck.sh

test: ## Run the test suite
	uv run pytest

check: lint fmt-check typecheck test ## Run the full quality gate (CI-equivalent)

precommit: ## Run all pre-commit hooks against all files
	uv run pre-commit run --all-files

run: ## Run a service locally, e.g. `make run SERVICE=market-data`
	uv run python -m aqros_$(subst -,_,$(SERVICE)).main

docker-build: ## Build all service images via docker-compose
	docker compose build

docker-up: ## Start the full stack (health endpoints) in the background
	docker compose up -d

docker-down: ## Stop and remove the stack
	docker compose down

clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage dist build
