BACKEND := backend
FRONTEND := frontend
PY := cd $(BACKEND) && uv run
NPM := cd $(FRONTEND) && npm run
COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help setup install up down ps logs psql db-reset migrate downgrade migration history \
	models api web dev test test-live test-web lint lint-web format typecheck typecheck-web \
	fix check gen-api image image-multiarch clean

help: ## List available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## First-time setup: env files, dependencies, services, migrations
	cp -n $(BACKEND)/.env.example $(BACKEND)/.env || true
	cp -n $(FRONTEND)/.env.example $(FRONTEND)/.env || true
	$(MAKE) install up migrate

install: ## Install backend and frontend dependencies
	cd $(BACKEND) && uv sync
	cd $(FRONTEND) && npm install

up: ## Start Postgres and Redis and wait until healthy
	$(COMPOSE) up -d --wait db redis

down: ## Stop all containers (data is kept)
	$(COMPOSE) down

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Follow Postgres and Redis logs
	$(COMPOSE) logs -f db redis

psql: ## Open a psql shell on the dev database
	$(COMPOSE) exec db psql -U postgres -d medcite

db-reset: ## Delete all local data, recreate services, re-run migrations
	$(COMPOSE) down -v
	$(MAKE) up migrate

migrate: ## Apply all migrations
	$(PY) alembic upgrade head

downgrade: ## Roll back one migration
	$(PY) alembic downgrade -1

migration: ## Create an empty migration: make migration name="add foo"
	@test -n "$(name)" || (echo 'usage: make migration name="describe the change"' && exit 1)
	$(PY) alembic revision -m "$(name)"

history: ## Show migration history
	$(PY) alembic history --verbose

models: ## Download MedCPT weights into HF_HOME
	$(PY) python -m app.models.download

api: ## Run the API with reload on :8000
	$(PY) uvicorn app.main:app --reload --port 8000

web: ## Run the frontend dev server on :5173
	$(NPM) dev

dev: ## Run API and frontend together (Ctrl+C stops both)
	$(MAKE) -j2 api web

test: ## Backend tests (integration tests skip if services are down)
	$(PY) pytest

test-live: ## Live Groq checks (uses your rate limits)
	$(PY) pytest -m live -v

test-web: ## Frontend tests
	$(NPM) test

lint: ## Backend lint and format check
	$(PY) ruff check .
	$(PY) ruff format --check .

lint-web: ## Frontend lint and format check
	$(NPM) lint
	$(NPM) format:check

format: ## Auto-format backend and frontend
	$(PY) ruff format .
	$(NPM) format

fix: ## Auto-fix safe lint issues, then format
	$(PY) ruff check --fix .
	$(MAKE) format

typecheck: ## Backend type check
	$(PY) mypy

typecheck-web: ## Frontend type check
	$(NPM) typecheck

check: lint typecheck test lint-web typecheck-web test-web ## Everything CI would run

gen-api: ## Regenerate frontend API types (API must be running)
	$(NPM) gen:api

image: ## Build the API image for this machine
	docker build -t medcite-api ./$(BACKEND)

image-multiarch: ## Build the API image for amd64 and arm64
	docker buildx build --platform linux/amd64,linux/arm64 -t medcite-api ./$(BACKEND)

clean: ## Remove caches and build output (keeps models and node_modules)
	find $(BACKEND) -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
	rm -rf $(FRONTEND)/dist