.PHONY: install dev test lint format smoke build docker-up docker-down canary-init canary-validate canary-deploy canary-rollback

install:
	python -m pip install -e .

dev:
	uvicorn assistx.api:app --host 0.0.0.0 --port 8000 --reload

test:
	pytest -q

lint:
	ruff check src tests

format:
	ruff check --fix src tests

smoke:
	python -m compileall src
	pytest -q

build:
	docker compose build

docker-up:
	docker compose up --build

docker-down:
	docker compose down

CANARY_ENV_FILE ?= deploy/canary.env

canary-init:
	@PYTHONPATH=src python scripts/init-canary-env.py

canary-validate:
	@PYTHONPATH=src python -m assistx.deployment_canary \
		--env-file $(CANARY_ENV_FILE) --validate-env

canary-deploy:
	@CANARY_ENV_FILE=$(CANARY_ENV_FILE) scripts/deploy-e2e-canary.sh

canary-rollback:
	@CANARY_ENV_FILE=$(CANARY_ENV_FILE) scripts/rollback-e2e-canary.sh

# Live reconciliation shadow deployment. These targets use a separate Compose
# project, network, ports, database, and state. They never stop the old stack.
RECON_ENV_FILE ?= deploy/reconciliation.env
RECON_STATE_FILE ?= deploy/reconciliation/migration-state.yaml
RECON_ROUTER_ROOT ?= ../auto-router
RECON_DIRECT = docker compose --profile neo4j --env-file $(RECON_ENV_FILE) \
	-f docker-compose.yml -f compose.prod.yml -f compose.canary.yml
RECON_ROUTER = $(RECON_DIRECT) -f compose.reconciliation.yml

.PHONY: reconciliation-init reconciliation-state-init reconciliation-state-validate \
	reconciliation-cutover-gate reconciliation-preflight reconciliation-render-direct \
	reconciliation-up-direct reconciliation-render-router reconciliation-up-router \
	reconciliation-executor-up reconciliation-verify reconciliation-status \
	reconciliation-down

reconciliation-init:
	@test -f $(RECON_ENV_FILE) || cp deploy/reconciliation.env.example $(RECON_ENV_FILE)
	@chmod 600 $(RECON_ENV_FILE)
	@chmod +x scripts/reconciliation-preflight.sh scripts/reconciliation-verify-offline.sh
	@chmod +x scripts/validate-reconciliation-state.py
	@$(MAKE) reconciliation-state-init
	@echo "Review and replace every change-me value in $(RECON_ENV_FILE) before startup."

reconciliation-state-init:
	@test -f $(RECON_STATE_FILE) || cp deploy/reconciliation/migration-state.example.yaml $(RECON_STATE_FILE)
	@chmod 600 $(RECON_STATE_FILE)
	@echo "Migration state ledger: $(RECON_STATE_FILE)"

reconciliation-state-validate:
	@python scripts/validate-reconciliation-state.py $(RECON_STATE_FILE)

reconciliation-cutover-gate:
	@python scripts/validate-reconciliation-state.py --require-cutover $(RECON_STATE_FILE)

reconciliation-preflight:
	@./scripts/reconciliation-preflight.sh

reconciliation-render-direct:
	@mkdir -p artifacts/reconciliation-render
	@$(RECON_DIRECT) config > artifacts/reconciliation-render/assistx-direct.yaml
	@echo "Rendered artifacts/reconciliation-render/assistx-direct.yaml"

reconciliation-up-direct:
	@$(RECON_DIRECT) up -d --build neo4j redis api worker

reconciliation-render-router:
	@mkdir -p artifacts/reconciliation-render
	@$(RECON_ROUTER) config > artifacts/reconciliation-render/assistx-router.yaml
	@echo "Rendered artifacts/reconciliation-render/assistx-router.yaml"

reconciliation-up-router:
	@$(RECON_ROUTER) up -d --build --force-recreate api worker

reconciliation-executor-up:
	@docker compose --profile neo4j --profile executor --env-file $(RECON_ENV_FILE) \
		-f docker-compose.yml -f compose.prod.yml -f compose.canary.yml \
		-f compose.reconciliation.yml up -d --build hermes-adapter

reconciliation-verify:
	@RECONCILIATION_ENV_FILE=$(RECON_ENV_FILE) \
	 RECONCILIATION_NEW_ASSISTX_URL=http://127.0.0.1:18000 \
	 RECONCILIATION_NEW_ROUTER_URL=http://127.0.0.1:18088 \
	 ./scripts/reconciliation-verify-offline.sh \
	 artifacts/reconciliation-render/assistx-router.yaml \
	 $(RECON_ROUTER_ROOT)/artifacts-reconciliation/router-rendered.yaml \
	 $(RECON_ROUTER_ROOT)/config/providers.reconciliation.yaml \
	 $(RECON_ROUTER_ROOT)/config/policies.yaml

reconciliation-status:
	@$(RECON_ROUTER) ps
	@curl -fsS http://127.0.0.1:18000/health | jq
	@curl -fsS http://127.0.0.1:18088/health | jq

reconciliation-down:
	@$(RECON_ROUTER) down
	@echo "Stopped only the assistx_reconciliation Compose project; evidence and named volumes are preserved."

# Go-live checks (original targets preserved)
.PHONY: go-live-check go-live-preflight go-live-smoke go-live-gate
BASE_URL ?= http://localhost:8000

go-live-preflight:
	@BASE_URL=$(BASE_URL) src/scripts/phase6_preflight.sh

go-live-smoke:
	@BASE_URL=$(BASE_URL) src/scripts/phase6_callback_smoke.sh

go-live-gate:
	@BASE_URL=$(BASE_URL) src/scripts/phase6_canary_gate.sh

go-live-check: go-live-preflight go-live-smoke go-live-gate
	@echo "Go-live checks passed."
