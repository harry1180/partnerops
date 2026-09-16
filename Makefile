.PHONY: up down logs ps check-api check-web check-all fmt migrate seed build

COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml

up: ## build and start the full local stack
	$(COMPOSE) up --build -d
down: ## stop and remove containers (volumes preserved)
	$(COMPOSE) down
ps:
	$(COMPOSE) ps
logs:
	$(COMPOSE) logs -f --tail=100 api

fmt: ## auto-format / lint-fix backend + frontend
	cd apps/api && ruff format app workers tests 2>/dev/null || ruff format app
	cd apps/api && ruff check --fix app
	pnpm -r --if-present lint

check-api: ## ruff + mypy + pytest
	cd apps/api && ruff check app
	cd apps/api && mypy app
	cd apps/api && python -m pytest -q

check-web: ## typecheck + unit tests + production build
	pnpm -r --if-present typecheck
	pnpm -r --if-present test
	pnpm --filter @cloudpartnerops/web build

check-all: check-api check-web

migrate: ## run alembic migrations (host dev)
	cd apps/api && alembic upgrade head

seed: ## idempotent demo seed (local/test env only)
	cd apps/api && python -m app.seed

build:
	pnpm --filter @cloudpartnerops/web build
