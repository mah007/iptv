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
# The commit the admin's System page shows (ADR-0019); the installer writes it to .env.
APP_COMMIT ?= $(shell git rev-parse --short HEAD 2>/dev/null)
export APP_COMMIT

# Optional transcoder GPU overlays (ADR-0010): GPU=nvidia, GPU=intel or GPU=nvidia,intel,
# on the command line or in .env. Without them the transcoder encodes on the CPU.
GPU ?= $(shell sed -n 's/^GPU=//p' .env 2>/dev/null)
comma := ,
GPU_FILES := $(foreach g,$(subst $(comma), ,$(GPU)),-f docker/compose.gpu-$(g).yml)
# Opt-in monitoring overlay (ADR-0018): MONITORING=1 on the command line or in .env adds
# Prometheus, Grafana, Loki, Alloy, Alertmanager and the exporters (and the NVIDIA GPU
# exporter with GPU=nvidia). Pass it to down/ps/logs too, so they see those services.
MONITORING ?= $(shell sed -n 's/^MONITORING=//p' .env 2>/dev/null)
MONITORING_ON := $(filter 1 true yes,$(MONITORING))
MONITORING_FILES := $(if $(MONITORING_ON),-f docker/compose.monitoring.yml -f docker/compose.monitoring.dev.yml $(if $(findstring nvidia,$(GPU)),--profile gpu-nvidia))
# Opt-in CrowdSec (ADR-0020): CROWDSEC=1 on the command line or in .env adds the engine
# and Traefik's bouncer (docker/compose.crowdsec.yml). Pass it to down/ps/logs too.
CROWDSEC ?= $(shell sed -n 's/^CROWDSEC=//p' .env 2>/dev/null)
CROWDSEC_FILES := $(if $(filter 1 true yes,$(CROWDSEC)),-f docker/compose.crowdsec.yml)
# Opt-in read replica (M15, ADR-0022): REPLICA=1 on the command line or in .env adds a hot
# standby for reporting reads (docker/compose.replica.yml; with MONITORING=1 its exporter).
REPLICA ?= $(shell sed -n 's/^REPLICA=//p' .env 2>/dev/null)
REPLICA_ON := $(filter 1 true yes,$(REPLICA))
REPLICA_FILES := $(if $(REPLICA_ON),-f docker/compose.replica.yml $(if $(MONITORING_ON),-f docker/compose.replica.monitoring.yml))
COMPOSE := docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml $(GPU_FILES) $(MONITORING_FILES) $(CROWDSEC_FILES) $(REPLICA_FILES)
# Every backend container bind-mounts ./media (the libraries, git-ignored). Create it as
# the host user, or Docker would create it owned by root.
$(shell mkdir -p media)
RUN_BACKEND := $(COMPOSE) run --rm --no-deps -T web
RUN_FRONTEND := $(COMPOSE) run --rm --no-deps -T frontend

# Trivy pinned by digest: a moved or compromised tag can't change what runs.
TRIVY_IMAGE := aquasec/trivy:0.75.0@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa
IMAGES := smart-iptv/app:$(APP_VERSION) smart-iptv/media:$(APP_VERSION) smart-iptv/frontend:$(APP_VERSION) \
	smart-iptv/postgres:$(APP_VERSION) smart-iptv/postgres-backup:$(APP_VERSION) smart-iptv/traefik:$(APP_VERSION)
# Extra docker build flags; before tagging a milestone use BUILD_FLAGS="--pull --no-cache".
BUILD_FLAGS ?= --pull

.PHONY: help secrets up down ps logs migrate backup restore-test crowdsec-test hardening-test seed sample-media shell smoke test test-backend test-frontend \
	lint lint-backend lint-frontend fmt typecheck typecheck-backend typecheck-frontend \
	api-client api-client-check build smoke-images scan licenses ci ci-steps audit semgrep zap \
	media-ready compat compat-live mediamtx-test e2e-iptvnator e2e-admin e2e-portal visual visual-record \
	edge-test s3-dev edge-dev rollout rollout-test loadtest deploy \
	monitoring-test monitoring-dashboards monitoring-smoke monitoring-firedrill

help: ## List available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.env:
	@scripts/secrets.sh

secrets: ## Create .env with generated dev secrets, or append keys new in .env.example
	@scripts/secrets.sh

# The media token keys (ADR-0007): the edge refuses to start without them.
secrets/media_token_keys.json:
	@scripts/secrets.sh

up: .env secrets/media_token_keys.json ## Build and start the dev stack, wait for healthchecks, apply migrations (MONITORING=1 adds the monitoring overlay)
	@# Append any key .env is missing (never changes one) and refresh the Docker secrets
	@# copied from it (monitoring, the backup service's token, ADR-0020).
	@scripts/secrets.sh > /dev/null
	$(COMPOSE) up --build --detach --wait --wait-timeout 300
	@# The app services use the restricted database role; migrations run as the owner.
	$(COMPOSE) exec -T -e DB_ROLE=owner web python manage.py migrate --noinput
	@port=$$(grep -E '^HTTP_PORT=' .env | cut -d= -f2); domain=$$(grep -E '^DOMAIN=' .env | cut -d= -f2); \
	  suffix=$$([ "$$port" = "80" ] || echo ":$$port"); \
	  echo ""; echo "Smart IPTV is up:"; \
	  for h in admin app api tv media traefik $(if $(MONITORING_ON),grafana mail); do echo "  http://$$h.$$domain$$suffix"; done

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

ps: ## Show service status and health
	$(COMPOSE) ps

logs: ## Follow logs; one service with s=<name>, e.g. make logs s=web
	$(COMPOSE) logs --follow --tail=200 $(s)

migrate: ## Apply database migrations (as the database owner; the app services use a restricted role)
	$(COMPOSE) exec -T -e DB_ROLE=owner web python manage.py migrate --noinput

hardening-test: ## Every production container drops capabilities, can't gain privileges, has a read-only root, a digest-pinned image and no Docker socket (G-36, G-39; no stack needed)
	python3 scripts/check_hardening.py

mediamtx-test: ## Live ingest sign-ins end to end: the production MediaMTX asks a throwaway web process (/internal/mediamtx-auth); publish, read and refusals (dev stack up)
	@scripts/mediamtx_test.sh

crowdsec-test: ## With CROWDSEC=1 up: a probe bans its own address and gets 403 from Traefik, the admin switch lets it pass, unban restores 200
	@scripts/crowdsec_test.sh

# --- M15 (ADR-0022): edges, the S3 origin ---
edge-test: ## The media edge end to end in throwaway containers: tokens (njs vs reference), local, live, S3 (presigned, height-mapped), a standalone edge (TLS, control channel, rotation, G-17), CDN pulls, fail-closed, logs (no stack needed)
	bash streaming/tests/run.sh

S3_DEV := $(COMPOSE) -f docker/compose.s3-dev.yml
s3-dev: ## Dev: SeaweedFS as the S3 origin (bucket `media`, publisher and read-only edge keys in secrets/s3-dev/), saved in the admin's settings, everything published
	@mkdir -p secrets/s3-dev && chmod 700 secrets/s3-dev
	@for name in publisher_access edge_access; do test -s secrets/s3-dev/$$name || \
	  printf 'SIPTV%s' "$$(openssl rand -hex 8 | tr a-f A-F)" > secrets/s3-dev/$$name; done
	@for name in publisher_secret edge_secret; do test -s secrets/s3-dev/$$name || \
	  openssl rand -base64 30 | tr -d '\n/+=' > secrets/s3-dev/$$name; done
	@chmod 644 secrets/s3-dev/*
	$(S3_DEV) up -d --wait seaweedfs
	@for i in $$(seq 30); do $(S3_DEV) exec -T seaweedfs sh -c 'echo "s3.bucket.create -name media" | weed shell' >/dev/null 2>&1 && break; sleep 2; done
	@for pair in "storage.s3_endpoint=http://seaweedfs:8333" "storage.s3_bucket=media" "storage.s3_prefix=renditions" \
	  "storage.s3_region=us-east-1" "storage.s3_path_style=true" "storage.s3_enabled=true"; do \
	  printf '%s' "$${pair#*=}" | $(COMPOSE) exec -T web python manage.py set_setting "$${pair%%=*}" --stdin >/dev/null; done
	@$(COMPOSE) exec -T web python manage.py set_setting storage.s3_access_key --stdin < secrets/s3-dev/publisher_access >/dev/null
	@$(COMPOSE) exec -T web python manage.py set_setting storage.s3_secret_key --stdin < secrets/s3-dev/publisher_secret >/dev/null
	@$(COMPOSE) exec -T web python manage.py set_setting storage.s3_edge_access_key --stdin < secrets/s3-dev/edge_access >/dev/null
	@$(COMPOSE) exec -T web python manage.py set_setting storage.s3_edge_secret_key --stdin < secrets/s3-dev/edge_secret >/dev/null
	$(COMPOSE) exec -T worker python manage.py publish_media --all
	$(COMPOSE) exec -T worker python manage.py publish_media --status

EDGE_DEV := EDGE_ID=dev-edge EDGE_DOMAIN=edge.localhost EDGE_CONTROL_URL=http://traefik \
	docker compose --project-directory . -p smart-iptv-edge -f docker/compose.edge.yml -f docker/compose.edge.dev.yml
edge-dev: ## Dev: a standalone edge (project smart-iptv-edge) on 127.0.0.1:18090 reading the dev SeaweedFS, registered as `dev-edge` in the admin; canary=<user> limits it to those customers; EDGE_DEV_DOWN=1 removes it
	@if [ -n "$(EDGE_DEV_DOWN)" ]; then $(EDGE_DEV) down; exit 0; fi; \
	mkdir -p secrets/dev-edge && chmod 700 secrets/dev-edge; \
	$(COMPOSE) exec -T web python manage.py edges_dev_edge --edge-id dev-edge \
	  --base-url http://127.0.0.1:18090 $(if $(canary),--canary $(canary)) > secrets/dev-edge/edge_token; \
	chmod 644 secrets/dev-edge/edge_token; \
	$(EDGE_DEV) up -d --force-recreate --wait edge

rollout: ## Dev: roll the web service with no downtime (new replicas, health-gated; old ones drained, then stopped; scripts/rollout.sh). s=frontend rolls the apps' server
	scripts/rollout.sh --service $(or $(s),web) -- --project-directory . -f docker/compose.yml -f docker/compose.dev.yml $(GPU_FILES) $(MONITORING_FILES) $(CROWDSEC_FILES) $(REPLICA_FILES)

rollout-test: ## Dev proof of zero downtime: a steady load (Xtream catalog, APIs, sign-in POSTs) while web rolls twice; fails on any failed request
	@xc_user=$$(sed -n 's/^COMPAT_XC_USER=//p' .env); xc_pass=$$(sed -n 's/^COMPAT_XC_PASS=//p' .env); \
	XC_AUTH="$$xc_user:$$xc_pass" python3 loadtests/rollout_probe.py --base http://127.0.0.1:$$(sed -n 's/^HTTP_PORT=//p' .env) \
	  --domain $$(sed -n 's/^DOMAIN=//p' .env) --seconds 150 --threads 12 & probe=$$!; \
	sleep 10; $(MAKE) rollout; sleep 15; $(MAKE) rollout; wait $$probe

loadtest: ## Locust against the local dev stack only (tiers=100,500,1000 by default; 5000 needs ~10 GB free): web on production gunicorn meanwhile, throwaway customers, memory guard, slot check; results in loadtests/results/ (docs/benchmarks/load.md)
	LT_COMPOSE="$(COMPOSE)" python3 loadtests/run_load.py --tiers $(or $(tiers),100,500,1000) $(args)

deploy: ## Update the server: ssh DEPLOY_HOST (e.g. root@203.0.113.10) and run install.sh --update there (pull, migrate once, roll web); the tree must be pushed
	@test -n "$(DEPLOY_HOST)" || { echo "make deploy needs DEPLOY_HOST=user@server" >&2; exit 2; }
	@test -z "$$(git status --porcelain --untracked-files=no)" || { echo "commit first: the server pulls what is pushed" >&2; exit 1; }
	@git fetch -q origin && test -z "$$(git log --oneline origin/$$(git branch --show-current)..HEAD)" || \
	  { echo "push first: the server pulls origin/$$(git branch --show-current)" >&2; exit 1; }
	ssh -o BatchMode=yes $(DEPLOY_HOST) 'sudo /opt/iptv/scripts/install.sh --update --yes'
# --- end M15 ---

backup: ## Back up now: database (pgBackRest), configuration, artwork and pg_dump (restic), offsite when set; waits for the results
	$(COMPOSE) exec -T web python manage.py backup_now --wait

restore-test: ## Restore drill: the latest backup into a throwaway PostgreSQL, migrations and row counts checked, restore time reported
	$(COMPOSE) exec -T web python manage.py restore_drill --wait

seed: ## Load demo data (idempotent); prints a new admin's password once. args=--reset-admin-password
	$(COMPOSE) exec -T web python manage.py seed_demo $(args)

sample-media: ## Generate legal synthetic test media into ./media (FFmpeg; idempotent)
	@scripts/sample_media.sh media

media-ready: ## Sample media scanned and transcoded: wait for a ready movie and series (adds the sample libraries if missing)
	@scripts/sample_media.sh media
	$(COMPOSE) exec -T web python manage.py media_ready --timeout 600

live-ready: ## The live test channel and its guide (DEBUG): created, live and recording 3 min of catch-up (fast once it records)
	@scripts/sample_media.sh media
	$(COMPOSE) exec -T web python manage.py live_demo --wait 420

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

# Type-aware ESLint holds every package's TypeScript program at once (about 1.8 GB by
# ADR-0024); Node's default heap in the 3 GB frontend container is 1.7 GB.
ESLINT_HEAP := -e NODE_OPTIONS=--max-old-space-size=2560

lint-frontend:
	$(COMPOSE) run --rm --no-deps -T $(ESLINT_HEAP) frontend pnpm lint

fmt: ## Format and auto-fix backend and frontend
	$(RUN_BACKEND) ruff format .
	$(RUN_BACKEND) ruff check --fix .
	$(COMPOSE) run --rm --no-deps -T $(ESLINT_HEAP) frontend pnpm fmt

typecheck: typecheck-backend typecheck-frontend ## mypy (backend) and tsc (frontend)

typecheck-backend: ## mypy
	$(RUN_BACKEND) mypy .

typecheck-frontend: ## tsc for every frontend package
	$(RUN_FRONTEND) pnpm typecheck

# The admin API's OpenAPI schema and the Orval client generated from it (ADR-0004).
# Generated files are committed and never edited by hand.
API_DIR := frontend/packages/api
API_SCHEMA := $(API_DIR)/openapi/admin.yaml
# The customer API's schema and client (C1, ADR-0013).
PORTAL_API_DIR := frontend/packages/api-portal
PORTAL_API_SCHEMA := $(PORTAL_API_DIR)/openapi/portal.yaml

api-client: ## Regenerate the admin and customer OpenAPI schemas and their typed clients (frontend/packages/api, api-portal)
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
	@# The customer API (ADR-0013): the portal URLconf's schema and its client, the same way.
	@mkdir -p $(dir $(PORTAL_API_SCHEMA))
	$(RUN_BACKEND) python manage.py spectacular --urlconf config.urls_portal \
	  --custom-settings config.urls_portal.SPECTACULAR_SETTINGS --validate --fail-on-warn \
	  > $(PORTAL_API_SCHEMA).tmp || { rm -f $(PORTAL_API_SCHEMA).tmp; exit 1; }
	@mv $(PORTAL_API_SCHEMA).tmp $(PORTAL_API_SCHEMA)
	$(RUN_FRONTEND) sh -c 'pnpm --config.verify-deps-before-run=false exec prettier --write \
	  --log-level warn $(patsubst frontend/%,%,$(PORTAL_API_SCHEMA)) \
	  && pnpm --config.verify-deps-before-run=false --filter @smart-iptv/api-portal generate'

# Checksums of the schema and client sources (node_modules excluded), to compare generations.
API_SUMS = find $(API_DIR) $(PORTAL_API_DIR) \( -path $(API_DIR)/node_modules -o -path $(PORTAL_API_DIR)/node_modules \) -prune -o -type f -print0 | sort -z | xargs -0 sha256sum

api-client-check: ## Fail if the schema or client in the tree differs from a fresh generation
	@# Compares content before and after regenerating, so it also works on uncommitted
	@# work (ALLOW_DIRTY=1); make ci's clean-tree check covers "committed".
	@before="$$(mktemp)"; after="$$(mktemp)"; trap 'rm -f "$$before" "$$after"' EXIT; \
	$(API_SUMS) > "$$before"; \
	$(MAKE) --no-print-directory api-client; \
	$(API_SUMS) > "$$after"; \
	if ! cmp -s "$$before" "$$after"; then \
	  echo "The API clients in $(API_DIR) or $(PORTAL_API_DIR) are stale; run make api-client and commit. Changed:" >&2; \
	  diff "$$before" "$$after" | sed -n 's/^[<>] [0-9a-f]*  /  /p' | sort -u >&2; exit 1; \
	fi
	@echo "API client is up to date."

# --- Xtream contract (compat/, ADR-0008) ------------------------------------------------
# The live checks sign in as the contract-check account: COMPAT_XC_USER/COMPAT_XC_PASS in
# .env (make secrets adds them). The values are exported to the tools, never echoed.
COMPAT_TOOL := uvx --python 3.13 --with 'jsonschema==4.26.0' python compat/validate.py
XC_CREDENTIALS = export XC_USER="$$(sed -n 's/^COMPAT_XC_USER=//p' .env)" \
	  XC_PASS="$$(sed -n 's/^COMPAT_XC_PASS=//p' .env)"; \
	test -n "$$XC_USER" && test -n "$$XC_PASS" || \
	  { echo "COMPAT_XC_USER and COMPAT_XC_PASS are missing from .env: run make secrets" >&2; exit 1; }
TV_URL = http://tv.$$(sed -n 's/^DOMAIN=//p' .env)$$(port=$$(sed -n 's/^HTTP_PORT=//p' .env); \
	[ "$$port" = "80" ] || echo ":$$port")
IPTVNATOR_IMAGE := 4gray/iptvnator:0.24.0@sha256:c5c33df50735741ba169cae83bf04e2fe356d33cb2ed7612295add2ba0038674
IPTVNATOR_NAME := smart-iptv-e2e-iptvnator

compat: ## Xtream contract suite on the schemas and golden fixtures (no stack needed)
	$(COMPAT_TOOL)

compat-live: ## Contract suite against the running Xtream host, play URLs included, and a 720p plan's .mp4 (a capped compat MP4; needs make media-ready)
	@$(XC_CREDENTIALS); \
	$(COMPOSE) exec -T -e XC_USER -e XC_PASS web python manage.py xtream_contract_account; \
	export XC_CAPPED_USER="$$XC_USER-720" XC_CAPPED_PASS="$$XC_PASS"; \
	XC_USER="$$XC_CAPPED_USER" $(COMPOSE) exec -T -e XC_USER -e XC_PASS web \
	  python manage.py xtream_contract_account --max-quality 720 --wait-capped 300; \
	$(COMPAT_TOOL) --live "$(TV_URL)" --play

e2e-iptvnator: ## IPTVnator (Docker, driven by Playwright) signs in, browses and plays from the Xtream host
	@$(XC_CREDENTIALS); \
	$(COMPOSE) exec -T -e XC_USER -e XC_PASS web python manage.py xtream_contract_account; \
	docker rm -f $(IPTVNATOR_NAME) > /dev/null 2>&1 || true; \
	trap 'docker rm -f $(IPTVNATOR_NAME) > /dev/null 2>&1 || true' EXIT; \
	docker run --rm -d --name $(IPTVNATOR_NAME) -p 127.0.0.1:4333:80 \
	  --add-host tv.localhost:host-gateway \
	  -e CLIENT_URL=http://127.0.0.1:4333 -e IPTVNATOR_PROXY_ALLOW_PRIVATE_NETWORKS=1 \
	  $(IPTVNATOR_IMAGE) > /dev/null; \
	for i in $$(seq 60); do curl -fsS -o /dev/null http://127.0.0.1:4333/api/health && break; sleep 1; done; \
	curl -fsS -o /dev/null http://127.0.0.1:4333/api/health || { echo "IPTVnator did not start" >&2; exit 1; }; \
	uvx --python 3.13 --with playwright==1.63.0 python compat/iptvnator_e2e.py \
	  --app http://127.0.0.1:4333 --server "$(TV_URL)" --artifacts dist/iptvnator-e2e

# --- Admin end-to-end suite (frontend/apps/admin/e2e, ADR-0015) --------------------------
# Playwright with the host's Chrome against the dev stack, like the IPTVnator journey. The
# end-to-end admin gets a fresh password every run (never stored); its TOTP codes come from
# the DEBUG-only totp_code command. Results and traces go to dist/admin-e2e.
ADMIN_URL = http://admin.$$(sed -n 's/^DOMAIN=//p' .env)$$(port=$$(sed -n 's/^HTTP_PORT=//p' .env); \
	[ "$$port" = "80" ] || echo ":$$port")
E2E_TOTP_COMMAND = ["docker","compose","--project-directory","$(CURDIR)","-f","$(CURDIR)/docker/compose.yml","-f","$(CURDIR)/docker/compose.dev.yml","exec","-T","web","python","manage.py","totp_code"]

e2e-admin: ## Admin journeys (MFA sign-in, customer, device, scan, review, kill) and axe checks in Playwright
	@export E2E_ADMIN_USER=e2e-admin E2E_ADMIN_PASS="$$(openssl rand -hex 16)"; \
	$(COMPOSE) exec -T -e E2E_ADMIN_USER -e E2E_ADMIN_PASS web python manage.py e2e_admin_account --open-review; \
	export ADMIN_URL="$(ADMIN_URL)" TV_URL="$(TV_URL)" E2E_ARTIFACTS="$(CURDIR)/dist/admin-e2e" \
	  E2E_TOTP_COMMAND='$(E2E_TOTP_COMMAND)' E2E_MONITORING="$(MONITORING_ON)" E2E_MAIL_URL="$(MAIL_URL)"; \
	cd frontend/apps/admin && node_modules/.bin/playwright test --config e2e/playwright.config.ts

# --- Portal end-to-end journey (frontend/apps/portal/e2e, ADR-0016) -----------------------
# Sign in, search in Arabic, play The Matrix, continue watching, sign out, with axe checks;
# the host's Chrome against the dev stack. The customer gets a fresh password every run
# (never stored) and an empty history. Results and traces go to dist/portal-e2e.
PORTAL_URL = http://app.$$(sed -n 's/^DOMAIN=//p' .env)$$(port=$$(sed -n 's/^HTTP_PORT=//p' .env); \
	[ "$$port" = "80" ] || echo ":$$port")

e2e-portal: ## Portal journeys (sign in, Arabic search, play, continue watching, sign out; live TV and catch-up; a full plan; my password; two-factor sign-in; the setup card) and axe checks in Playwright (needs media-ready and live-ready)
	@export E2E_PORTAL_USER=e2e-portal E2E_PORTAL_PASS="$$(openssl rand -hex 16)"; \
	$(COMPOSE) exec -T -e E2E_PORTAL_USER -e E2E_PORTAL_PASS web python manage.py e2e_portal_account; \
	export PORTAL_URL="$(PORTAL_URL)" E2E_ARTIFACTS="$(CURDIR)/dist/portal-e2e" \
	  E2E_TOTP_COMMAND='$(E2E_TOTP_COMMAND)'; \
	cd frontend/apps/portal && node_modules/.bin/playwright test --config e2e/playwright.config.ts

# --- Visual regression (frontend/apps/*/e2e/visual, ADR-0024) -----------------------------
# Key admin and portal pages against committed baselines, in en/light and ar/dark at
# desktop and 390 px, with the host's Chrome. Pages replay recorded API fixtures under a
# fixed clock, so only the SPAs come from the dev stack. UPDATE=1 rewrites the baselines
# that changed; RECORD=1 records the fixtures again from the dev stack first (needs
# media-ready and live-ready; the end-to-end admin and customer sign in, fresh passwords).
VISUAL_SNAPSHOTS = $(if $(UPDATE),--update-snapshots=changed,)

visual: ## Visual regression of key admin and portal pages (en/light, ar/dark; desktop, 390 px); UPDATE=1 rewrites baselines, RECORD=1 re-records fixtures
	@if [ -n "$(RECORD)" ]; then $(MAKE) --no-print-directory visual-record; fi
	@export ADMIN_URL="$(ADMIN_URL)" E2E_ARTIFACTS="$(CURDIR)/dist/admin-visual"; \
	cd frontend/apps/admin && node_modules/.bin/playwright test --config e2e/visual.config.ts $(VISUAL_SNAPSHOTS)
	@export PORTAL_URL="$(PORTAL_URL)" E2E_ARTIFACTS="$(CURDIR)/dist/portal-visual"; \
	cd frontend/apps/portal && node_modules/.bin/playwright test --config e2e/visual.config.ts $(VISUAL_SNAPSHOTS)

visual-record: ## Record the visual suites' API fixtures from the dev stack (make visual RECORD=1 runs this first)
	rm -rf frontend/apps/admin/e2e/visual/fixtures frontend/apps/portal/e2e/visual/fixtures
	@export E2E_ADMIN_USER=e2e-admin E2E_ADMIN_PASS="$$(openssl rand -hex 16)"; \
	$(COMPOSE) exec -T -e E2E_ADMIN_USER -e E2E_ADMIN_PASS web python manage.py e2e_admin_account; \
	export ADMIN_URL="$(ADMIN_URL)" E2E_ARTIFACTS="$(CURDIR)/dist/admin-visual" VISUAL_RECORD=1 \
	  E2E_TOTP_COMMAND='$(E2E_TOTP_COMMAND)'; \
	cd frontend/apps/admin && node_modules/.bin/playwright test --config e2e/visual.config.ts
	@export E2E_PORTAL_USER=e2e-portal E2E_PORTAL_PASS="$$(openssl rand -hex 16)"; \
	$(COMPOSE) exec -T -e E2E_PORTAL_USER -e E2E_PORTAL_PASS web python manage.py e2e_portal_account; \
	export PORTAL_URL="$(PORTAL_URL)" E2E_ARTIFACTS="$(CURDIR)/dist/portal-visual" VISUAL_RECORD=1; \
	cd frontend/apps/portal && node_modules/.bin/playwright test --config e2e/visual.config.ts

# --- Monitoring (docker/compose.monitoring*.yml, monitoring/, ADR-0018) -------------------
# The tools run in the images the overlay pins; ruff matches the backend's version.
MONITORING_RUFF := uvx ruff@$(shell sed -n '/^name = "ruff"$$/{n;s/^version = "\(.*\)"$$/\1/p}' backend/uv.lock)
MAIL_URL = http://mail.$$(sed -n 's/^DOMAIN=//p' .env)$$(port=$$(sed -n 's/^HTTP_PORT=//p' .env); \
	[ "$$port" = "80" ] || echo ":$$port")

monitoring-test: ## Monitoring configs, alert rule tests, dashboard queries and the log redaction pipeline (Docker, no stack needed)
	cd monitoring && $(MONITORING_RUFF) check . && $(MONITORING_RUFF) format --check .
	python3 monitoring/tests/check_configs.py
	python3 monitoring/tests/test_log_pipeline.py

monitoring-dashboards: ## Regenerate the Grafana dashboards (monitoring/grafana/build_dashboards.py)
	python3 monitoring/grafana/build_dashboards.py

monitoring-smoke: ## With MONITORING=1 up: targets up, rules loaded, admin-only Grafana, a request id traced in Loki
	@domain=$$(sed -n 's/^DOMAIN=//p' .env); port=$$(sed -n 's/^HTTP_PORT=//p' .env); \
	python3 monitoring/tests/smoke.py --domain "$${domain:-localhost}" --port "$${port:-80}"

monitoring-firedrill: ## With MONITORING=1 up: fire the FireDrill alert and wait for its email (and the resolved one) in Mailpit
	@# Dev's alert recipient, unless one was set in the admin (Settings, Monitoring).
	@printf 'ops@localhost' | $(COMPOSE) exec -T web python manage.py set_setting monitoring.alert_emails --stdin --if-unset
	@since=$$(date +%s); \
	trap '$(COMPOSE) exec -T web python manage.py fire_drill --stop > /dev/null' EXIT; \
	$(COMPOSE) exec -T web python manage.py fire_drill --minutes 10; \
	python3 monitoring/tests/fire_drill.py --mailpit "$(MAIL_URL)" --since "$$since" --timeout 300; \
	$(COMPOSE) exec -T web python manage.py fire_drill --stop; trap - EXIT; \
	python3 monitoring/tests/fire_drill.py --mailpit "$(MAIL_URL)" --since "$$since" --resolved --timeout 420

build: ## Build the production images (app, media, frontend, postgres, postgres-backup, traefik), pulling fresh base images
	docker build $(BUILD_FLAGS) -f docker/app.Dockerfile --target runtime -t smart-iptv/app:$(APP_VERSION) .
	docker build $(BUILD_FLAGS) -f docker/app.Dockerfile --target media -t smart-iptv/media:$(APP_VERSION) .
	docker build $(BUILD_FLAGS) -f docker/frontend.Dockerfile --target runtime -t smart-iptv/frontend:$(APP_VERSION) .
	docker build $(BUILD_FLAGS) -f docker/postgres.Dockerfile --target postgres -t smart-iptv/postgres:$(APP_VERSION) .
	docker build $(BUILD_FLAGS) -f docker/postgres.Dockerfile --target backup -t smart-iptv/postgres-backup:$(APP_VERSION) .
	docker build $(BUILD_FLAGS) -f docker/traefik.Dockerfile -t smart-iptv/traefik:$(APP_VERSION) .

smoke-images: ## Start the production images (env from .env.example), check --deploy, wait for health
	@scripts/smoke_images.sh

scan: ## Trivy: fixable HIGH/CRITICAL CVEs and secrets in the images, secrets in the repo; after make build
	@for image in $(IMAGES); do \
	  echo "Trivy image: $$image"; \
	  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro -v iptv-trivy-cache:/root/.cache \
	    -v "$(CURDIR)/.trivyignore.yaml:/trivyignore.yaml:ro" $(TRIVY_IMAGE) \
	    image --quiet --scanners vuln,secret --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 \
	    --ignorefile /trivyignore.yaml "$$image" || exit 1; \
	done
	@echo "Trivy secrets: repository"
	@docker run --rm -v "$(CURDIR)":/repo:ro -v iptv-trivy-cache:/root/.cache $(TRIVY_IMAGE) \
	  fs --quiet --scanners secret --exit-code 1 --skip-files .env \
	  --skip-dirs .git --skip-dirs '**/node_modules' --skip-dirs '**/.venv' --skip-dirs '**/dist' --skip-dirs '**/.pytest_cache' /repo

licenses: ## Licence gate (SPEC §1.2) on production dependencies; run after make build
	python3 scripts/license_gate.py --self-test
	docker run --rm -i --entrypoint python smart-iptv/app:$(APP_VERSION) - python < scripts/license_gate.py
	$(RUN_FRONTEND) sh -c 'pnpm install --frozen-lockfile --store-dir /pnpm-store > /dev/null && pnpm -r licenses list --prod --json' \
	  | python3 scripts/license_gate.py npm

# --- Supply chain and web scanning (M14a, ADR-0021) --------------------------------------
# Separate processes in pinned containers: Semgrep CE (LGPL-2.1) and ZAP (Apache-2.0) are
# never imported. Image sizes (amd64, compressed): Semgrep about 430 MB, zap-bare about
# 505 MB (1.2 GB unpacked); both are kept between runs.
PIP_AUDIT_VERSION := 2.10.1
SEMGREP_IMAGE := semgrep/semgrep:1.178.0-nonroot@sha256:2124de200043ce3ded427d02e924ff3b59d017020614d28c2bb7ce6656bec5d4
ZAP_IMAGE := zaproxy/zap-bare:20260807@sha256:fe8fa5157a9601b2db26531340717923bda544f2f2081ec3edc779baed7321eb

audit: ## Known vulnerabilities in production dependencies: pip-audit (PyPI advisories, OSV) and pnpm audit; dated exceptions in scripts/audit-ignore.toml
	python3 scripts/audit_deps.py --self-test
	$(RUN_BACKEND) sh -c 'uv export --frozen --no-dev --no-emit-project -q -o /tmp/requirements.txt && \
	  uv tool run -q --from pip-audit==$(PIP_AUDIT_VERSION) pip-audit -r /tmp/requirements.txt \
	    --require-hashes --disable-pip --progress-spinner off -f json || true' \
	  | python3 scripts/audit_deps.py pip
	$(RUN_FRONTEND) sh -c 'pnpm audit --prod --json || true' | python3 scripts/audit_deps.py npm

semgrep: ## The project's own Semgrep rules (.semgrep/rules.yml): their tests, then the code, offline
	@for target in rules.py rules.ts; do \
	  out="$$(docker run --rm -v "$(CURDIR):/src:ro" -w /src $(SEMGREP_IMAGE) semgrep --test --metrics=off \
	    --disable-version-check --config .semgrep/rules.yml .semgrep/$$target 2>&1)" || \
	    { echo "$$out" >&2; echo "Semgrep rule tests failed (.semgrep/$$target)" >&2; exit 1; }; \
	done; echo "Semgrep rule tests: ok"
	docker run --rm -v "$(CURDIR):/src:ro" -w /src $(SEMGREP_IMAGE) semgrep scan --config .semgrep/rules.yml \
	  --metrics=off --disable-version-check --error --no-git-ignore --quiet backend frontend/apps frontend/packages scripts

# ZAP's Automation Framework (security/zap/plan.yaml) against the running dev stack: passive
# rules on every host; any Low-or-higher alert the plan's filters don't explain fails.
# Reports: dist/zap/out/zap-report.{html,json}.
ZAP_DOMAIN = $$(sed -n 's/^DOMAIN=//p' .env)
ZAP_PORT = $$(sed -n 's/^HTTP_PORT=//p' .env)
zap: ## ZAP baseline (passive) against the dev stack's admin, portal, API, Xtream and media hosts
	@rm -rf dist/zap && mkdir -p dist/zap/out && cp security/zap/plan.yaml dist/zap/plan.yaml
	@domain="$(ZAP_DOMAIN)"; port="$(ZAP_PORT)"; base="$$domain$$([ "$$port" = 80 ] || echo ":$$port")"; \
	hosts=""; for h in admin app api tv media; do hosts="$$hosts --add-host $$h.$$domain:host-gateway"; done; \
	set +e; docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp $$hosts \
	  -e ZAP_BASE="$$base" -e ZAP_BASE_RE="$${base//./\\.}" \
	  -v "$(CURDIR)/dist/zap:/zap/wrk:rw" $(ZAP_IMAGE) zap.sh -cmd -silent -notel -autorun /zap/wrk/plan.yaml \
	  | grep -v -e 'requesting URL' -e ' set ' -e 'Added global filter'; \
	status=$${PIPESTATUS[0]}; set -e; \
	[ "$$status" = 0 ] || echo "ZAP found alerts the plan doesn't accept: see dist/zap/out/zap-report.html" >&2; \
	exit "$$status"

# The project has no hosted CI (ADR-0003): this is the gate. Run it before pushing
# and before tagging a milestone; it stops at the first failing step.
ci: ## Full quality gate on the committed tree (ALLOW_DIRTY=1 to check uncommitted work)
	@test -z "$(t)" || { echo "make ci always runs the full suite; drop t=$(t)" >&2; exit 1; }
	@test -n "$(ALLOW_DIRTY)" || test -z "$$(git status --porcelain)" || \
	  { echo "make ci checks what you push: commit or stash first (or ALLOW_DIRTY=1)" >&2; exit 1; }
	@$(MAKE) ci-steps || { echo "make ci FAILED. Service status and recent logs:" >&2; \
	  $(COMPOSE) ps >&2 || true; $(COMPOSE) logs --no-color --tail=60 >&2 || true; exit 1; }
	@echo ""
	@echo "Quality gate passed for $$(git rev-parse --short HEAD)$(if $(ALLOW_DIRTY), plus uncommitted changes,): stack, smoke, lint, Semgrep, dependency audit, ZAP baseline, Xtream contract, media edges, monitoring configs and log redaction, types, API client, tests, live Xtream checks, IPTVnator, admin and portal E2E with axe, visual regression, images, Trivy, licences."

ci-steps:
	$(MAKE) up
	$(MAKE) smoke
	$(MAKE) lint
	$(MAKE) semgrep
	$(MAKE) audit
	$(MAKE) zap
	$(MAKE) compat
	$(MAKE) edge-test
	$(MAKE) monitoring-test
	$(MAKE) hardening-test
	$(MAKE) typecheck
	$(MAKE) api-client-check
	$(MAKE) test
	$(MAKE) restore-test
	$(MAKE) media-ready
	$(MAKE) live-ready
	$(MAKE) compat-live
	$(MAKE) mediamtx-test
	$(MAKE) e2e-iptvnator
	$(MAKE) e2e-admin
	$(MAKE) e2e-portal
	$(MAKE) visual
	$(MAKE) build
	$(MAKE) smoke-images
	$(MAKE) scan
	$(MAKE) licenses
