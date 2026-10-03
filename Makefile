# Smart IPTV developer commands. `make` lists them.
# Everything runs in containers; the host needs Docker, make, curl, openssl and python3.

SHELL := /bin/bash
# Fail recipes on any error, including inside pipelines (make licenses pipes into the gate).
.SHELLFLAGS := -eu -o pipefail -c
MAKEFLAGS += --no-print-directory
.DEFAULT_GOAL := help

export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

# One image tag for compose and the image targets: APP_VERSION from .env, else "dev".
APP_VERSION ?= $(or $(shell sed -n 's/^APP_VERSION=//p' .env 2>/dev/null),dev)
export APP_VERSION

COMPOSE := docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml
RUN_BACKEND := $(COMPOSE) run --rm --no-deps -T web
RUN_FRONTEND := $(COMPOSE) run --rm --no-deps -T frontend

# Trivy pinned by digest: a moved or compromised tag can't change what runs.
TRIVY_IMAGE := aquasec/trivy:0.75.0@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa
IMAGES := smart-iptv/app:$(APP_VERSION) smart-iptv/frontend:$(APP_VERSION)
# Extra docker build flags; before tagging a milestone use BUILD_FLAGS="--pull --no-cache".
BUILD_FLAGS ?= --pull

.PHONY: help secrets up down ps logs migrate seed shell smoke test test-backend test-frontend \
	lint lint-backend lint-frontend fmt typecheck typecheck-backend typecheck-frontend \
	api-client api-client-check build smoke-images scan licenses ci ci-steps

help: ## List available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.env:
	@scripts/secrets.sh

secrets: ## Create .env with generated dev secrets, or append keys new in .env.example
	@scripts/secrets.sh

up: .env ## Build and start the dev stack, wait for healthchecks, apply migrations
	$(COMPOSE) up --build --detach --wait --wait-timeout 300
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

seed: ## Load demo data (idempotent); prints a new admin's password once. args=--reset-admin-password
	$(COMPOSE) exec -T web python manage.py seed_demo $(args)

shell: ## Django shell in the web container
	$(COMPOSE) exec web python manage.py shell

smoke: ## Check routing, isolation and readiness of the running stack through Traefik
	@scripts/smoke.sh

test: test-backend test-frontend ## Run all tests

PYTEST_COVERAGE := --cov=apps --cov-report=term-missing:skip-covered --cov-fail-under=85

test-backend: ## pytest on the running stack's stores; full run enforces 85% coverage, t="..." runs a subset, lane=1..3 isolates parallel runs
	$(COMPOSE) run --rm -T $(if $(lane),-e TEST_LANE=$(lane) )web pytest $(if $(t),$(t),$(PYTEST_COVERAGE))

test-frontend: ## Vitest for every frontend package
	$(RUN_FRONTEND) pnpm test

lint: lint-backend lint-frontend ## Lint and format-check everything

lint-backend:
	$(RUN_BACKEND) ruff check .
	$(RUN_BACKEND) ruff format --check .
	$(RUN_BACKEND) python manage.py makemigrations --check --dry-run

lint-frontend:
	$(RUN_FRONTEND) pnpm lint

fmt: ## Format and auto-fix backend and frontend
	$(RUN_BACKEND) ruff format .
	$(RUN_BACKEND) ruff check --fix .
	$(RUN_FRONTEND) pnpm fmt

typecheck: typecheck-backend typecheck-frontend ## mypy (backend) and tsc (frontend)

typecheck-backend: ## mypy
	$(RUN_BACKEND) mypy .

typecheck-frontend: ## tsc for every frontend package
	$(RUN_FRONTEND) pnpm typecheck

# The admin API's OpenAPI schema and the Orval client generated from it (ADR-0004).
# Generated files are committed and never edited by hand.
API_DIR := frontend/packages/api
API_SCHEMA := $(API_DIR)/openapi/admin.yaml

api-client: ## Regenerate the admin OpenAPI schema and the typed client in frontend/packages/api
	@mkdir -p $(dir $(API_SCHEMA))
	@# Logs go to stderr, so stdout is exactly the schema; a failed run keeps the old file.
	$(RUN_BACKEND) python manage.py spectacular --urlconf config.urls_admin --validate --fail-on-warn \
	  > $(API_SCHEMA).tmp || { rm -f $(API_SCHEMA).tmp; exit 1; }
	@mv $(API_SCHEMA).tmp $(API_SCHEMA)
	@# Prettier-format the schema (it's committed and checked like any file), then run
	@# Orval. Both use the node_modules that `make up` installed: pnpm must not
	@# re-install them here, which would re-link them to another store.
	$(RUN_FRONTEND) sh -c 'pnpm --config.verify-deps-before-run=false exec prettier --write \
	  --log-level warn $(patsubst frontend/%,%,$(API_SCHEMA)) \
	  && pnpm --config.verify-deps-before-run=false --filter @smart-iptv/api generate'

# Checksums of the schema and client sources (node_modules excluded), to compare generations.
API_SUMS = find $(API_DIR) -path $(API_DIR)/node_modules -prune -o -type f -print0 | sort -z | xargs -0 sha256sum

api-client-check: ## Fail if the schema or client in the tree differs from a fresh generation
	@# Compares content before and after regenerating, so it also works on uncommitted
	@# work (ALLOW_DIRTY=1); make ci's clean-tree check covers "committed".
	@before="$$(mktemp)"; after="$$(mktemp)"; trap 'rm -f "$$before" "$$after"' EXIT; \
	$(API_SUMS) > "$$before"; \
	$(MAKE) --no-print-directory api-client; \
	$(API_SUMS) > "$$after"; \
	if ! cmp -s "$$before" "$$after"; then \
	  echo "The API client in $(API_DIR) is stale; run make api-client and commit. Changed:" >&2; \
	  diff "$$before" "$$after" | sed -n 's/^[<>] [0-9a-f]*  /  /p' | sort -u >&2; exit 1; \
	fi
	@echo "API client is up to date."

build: ## Build the production images (app, frontend), pulling fresh base images
	docker build $(BUILD_FLAGS) -f docker/app.Dockerfile --target runtime -t smart-iptv/app:$(APP_VERSION) .
	docker build $(BUILD_FLAGS) -f docker/frontend.Dockerfile --target runtime -t smart-iptv/frontend:$(APP_VERSION) .

smoke-images: ## Start the production images (env from .env.example), check --deploy, wait for health
	@scripts/smoke_images.sh

scan: ## Trivy: fixable HIGH/CRITICAL CVEs and secrets in the images, secrets in the repo; after make build
	@for image in $(IMAGES); do \
	  echo "Trivy image: $$image"; \
	  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro -v iptv-trivy-cache:/root/.cache $(TRIVY_IMAGE) \
	    image --quiet --scanners vuln,secret --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "$$image" || exit 1; \
	done
	@echo "Trivy secrets: repository"
	@docker run --rm -v "$(CURDIR)":/repo:ro -v iptv-trivy-cache:/root/.cache $(TRIVY_IMAGE) \
	  fs --quiet --scanners secret --exit-code 1 --skip-files .env \
	  --skip-dirs .git --skip-dirs '**/node_modules' --skip-dirs '**/.venv' --skip-dirs '**/dist' /repo

licenses: ## Licence gate (SPEC §1.2) on production dependencies; run after make build
	python3 scripts/license_gate.py --self-test
	docker run --rm -i --entrypoint python smart-iptv/app:$(APP_VERSION) - python < scripts/license_gate.py
	$(RUN_FRONTEND) sh -c 'pnpm install --frozen-lockfile --store-dir /pnpm-store > /dev/null && pnpm -r licenses list --prod --json' \
	  | python3 scripts/license_gate.py npm

# The project has no hosted CI (ADR-0003): this is the gate. Run it before pushing
# and before tagging a milestone; it stops at the first failing step.
ci: ## Full quality gate on the committed tree (ALLOW_DIRTY=1 to check uncommitted work)
	@test -z "$(t)" || { echo "make ci always runs the full suite; drop t=$(t)" >&2; exit 1; }
	@test -n "$(ALLOW_DIRTY)" || test -z "$$(git status --porcelain)" || \
	  { echo "make ci checks what you push: commit or stash first (or ALLOW_DIRTY=1)" >&2; exit 1; }
	@$(MAKE) ci-steps || { echo "make ci FAILED. Service status and recent logs:" >&2; \
	  $(COMPOSE) ps >&2 || true; $(COMPOSE) logs --no-color --tail=60 >&2 || true; exit 1; }
	@echo ""
	@echo "Quality gate passed for $$(git rev-parse --short HEAD)$(if $(ALLOW_DIRTY), plus uncommitted changes,): stack, smoke, lint, types, API client, tests, images, Trivy, licences."

ci-steps:
	$(MAKE) up
	$(MAKE) smoke
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) api-client-check
	$(MAKE) test
	$(MAKE) build
	$(MAKE) smoke-images
	$(MAKE) scan
	$(MAKE) licenses
