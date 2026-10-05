# AQROS developer Makefile. Run `make help` for the list of targets.
.DEFAULT_GOAL := help
.PHONY: help install scaffold fmt fmt-check lint lint-fix typecheck test check \
        precommit run docker-build docker-up docker-down clean \
        migrate migrate-check migrate-down up migrate-and-up

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# Every database container, started before the services. Several services seed
# rows during startup and cannot boot against an empty schema, so the order is
# databases → migrate → services.
DATABASES = auth-db audit-ledger-db market-data-db feature-store-db \
            dataset-builder-db training-pipeline-db model-registry-db \
            backtesting-engine-db risk-engine-db portfolio-db oms-db \
            paper-trading-engine-db live-trading-engine-db redis

install: ## Create the venv and install all workspace packages + dev tools
	uv sync --all-packages

up: ## Build and start the whole stack (all trust-ladder profiles)
	@echo "==> Building images"
	docker compose --profile trading --profile backtest --profile audit \
		--profile live --profile paper --profile objectstore build
	@echo "==> Starting databases (services wait for these)"
	docker compose up -d $(DATABASES)
	@echo "==> Applying migrations"
	$(MAKE) --no-print-directory migrate
	@echo "==> Starting services"
	docker compose --profile trading --profile backtest --profile audit \
		--profile live --profile paper --profile objectstore up -d
	@echo
	@echo "Stack is up. Next steps:"
	@echo "  make status                      # health of every service"
	@echo "  curl localhost:8000/v1/topology  # what exists and what is public"
	@echo "  curl localhost:8000/v1/health/platform"

migrate: ## Apply Alembic migrations to every running DB-owning service
	uv run python scripts/migrate.py

migrate-check: ## Report which DB-owning services are up (no changes)
	uv run python scripts/migrate.py --check

migrate-down: ## DESTRUCTIVE: roll every running service back to base
	uv run python scripts/migrate.py --downgrade

status: ## Show health of every service in the stack
	@uv run python scripts/status.py

migrate-and-up: ## Alias for `up`
	$(MAKE) up

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
