# Smart IPTV developer commands. `make` lists them.
# Everything runs in containers; the host needs Docker, make, openssl and python3.

SHELL := /bin/bash
.DEFAULT_GOAL := help

export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

COMPOSE := docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml
RUN_BACKEND := $(COMPOSE) run --rm --no-deps -T web
RUN_FRONTEND := $(COMPOSE) run --rm --no-deps -T frontend

.PHONY: help secrets up down ps logs migrate shell smoke test test-backend test-frontend \
	lint lint-backend lint-frontend fmt typecheck build licenses

help: ## List available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.env:
	@scripts/secrets.sh

secrets: ## Create .env with generated dev secrets (never overwrites)
	@scripts/secrets.sh

up: .env ## Build and start the dev stack, wait for healthchecks, apply migrations
	$(COMPOSE) up --build --detach --wait
	$(COMPOSE) exec -T web python manage.py migrate --noinput
	@port=$$(grep -E '^HTTP_PORT=' .env | cut -d= -f2); domain=$$(grep -E '^DOMAIN=' .env | cut -d= -f2); \
	  suffix=$$([ "$$port" = "80" ] || echo ":$$port"); \
	  echo ""; echo "Smart IPTV is up:"; \
	  for h in admin app api tv traefik; do echo "  http://$$h.$$domain$$suffix"; done

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

ps: ## Show service status and health
	$(COMPOSE) ps

logs: ## Follow logs; one service with s=<name>, e.g. make logs s=web
	$(COMPOSE) logs --follow --tail=200 $(s)

migrate: ## Apply database migrations
	$(COMPOSE) exec -T web python manage.py migrate --noinput

shell: ## Django shell in the web container
	$(COMPOSE) exec web python manage.py shell

smoke: ## Check routing, isolation and readiness of the running stack through Traefik
	@scripts/smoke.sh

test: test-backend test-frontend ## Run all tests

PYTEST_COVERAGE := --cov=apps --cov-report=term-missing:skip-covered --cov-fail-under=85

test-backend: ## pytest on the running stack's stores; full run enforces 85% coverage, t="..." runs a subset
	$(COMPOSE) run --rm -T web pytest $(if $(t),$(t),$(PYTEST_COVERAGE))

test-frontend: ## Vitest for every frontend package
	$(RUN_FRONTEND) pnpm test

lint: lint-backend lint-frontend ## Lint and format-check everything

lint-backend:
	$(RUN_BACKEND) ruff check .
	$(RUN_BACKEND) ruff format --check .

lint-frontend:
	$(RUN_FRONTEND) pnpm lint

fmt: ## Format and auto-fix backend and frontend
	$(RUN_BACKEND) ruff format .
	$(RUN_BACKEND) ruff check --fix .
	$(RUN_FRONTEND) pnpm fmt

typecheck: ## mypy (backend) and tsc (frontend)
	$(RUN_BACKEND) mypy .
	$(RUN_FRONTEND) pnpm typecheck

build: ## Build the production images (app, frontend)
	docker build -f docker/app.Dockerfile --target runtime -t smart-iptv/app:$${APP_VERSION:-dev} .
	docker build -f docker/frontend.Dockerfile --target runtime -t smart-iptv/frontend:$${APP_VERSION:-dev} .

licenses: ## Licence gate (SPEC §1.2) on production dependencies; run after make build
	docker run --rm -i --entrypoint python smart-iptv/app:$${APP_VERSION:-dev} - python < scripts/license_gate.py
	$(RUN_FRONTEND) sh -c 'pnpm install --frozen-lockfile --store-dir /pnpm-store > /dev/null && pnpm -r licenses list --prod --json' \
	  | python3 scripts/license_gate.py npm
