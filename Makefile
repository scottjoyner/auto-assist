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
# project, network, ports, database, state, executor home, and image bundle. They
# never stop the old stack.
RECON_ENV_FILE ?= deploy/reconciliation.env
RECON_STATE_FILE ?= deploy/reconciliation/migration-state.yaml
RECON_DEPENDENCY_FILE ?= deploy/reconciliation/external-dependencies.yaml
RECON_FINAL_EVIDENCE_FILE ?= deploy/reconciliation/final-cutover-evidence.yaml
RECON_PROJECTION_MANIFEST ?= deploy/reconciliation/runtime-projection.yaml
RECON_RUNTIME_EVIDENCE ?= artifacts/runtime-projection-artifact-evidence.json
RECON_PROJECTION_APPROVAL_EVIDENCE ?= artifacts/runtime-projection-approval-evidence.json
RECON_REPORT_FILE ?= artifacts/reconciliation-report.md
RECON_ROUTER_ROOT ?= ../auto-router
RECON_TAILSCALE_OUTPUT ?= artifacts/reconciliation-tailnet-candidates.json
RECON_TAILSCALE_STATUS ?=
RECON_LAN_MAP ?= deploy/reconciliation/lan-runtime-map.json
RECON_EXECUTOR_RENDER ?= artifacts/reconciliation-render/assistx-executor.json
RECON_EXECUTOR_SCOPED_RENDER ?= artifacts/reconciliation-render/assistx-executor-scoped.json
RECON_IMAGE_DIR ?= artifacts/reconciliation-images
RECON_IMAGE_MANIFEST ?= $(RECON_IMAGE_DIR)/reconciliation-images.manifest.json
RECON_HERMES_HOME ?= $(CURDIR)/artifacts/reconciliation-hermes-home
RECON_HERMES_CONFIG ?= $(RECON_HERMES_HOME)/config.yaml
RECON_PROJECTION_EVIDENCE ?= artifacts/runtime-projection-evidence.json
RECON_DIRECT = docker compose --profile neo4j --env-file $(RECON_ENV_FILE) \
	-f docker-compose.yml -f compose.prod.yml -f compose.canary.yml
RECON_ROUTER = $(RECON_DIRECT) -f compose.reconciliation.yml
RECON_EXECUTOR = docker compose --profile neo4j --profile executor --env-file $(RECON_ENV_FILE) \
	-f docker-compose.yml -f compose.prod.yml -f compose.canary.yml \
	-f compose.reconciliation.yml
RECON_EXECUTOR_SCOPED = $(RECON_EXECUTOR) -f compose.executor-scope.yml

.PHONY: reconciliation-worktrees-plan reconciliation-worktrees-apply \
	reconciliation-init reconciliation-state-init reconciliation-state-validate \
	reconciliation-dependencies-validate reconciliation-final-evidence-validate \
	reconciliation-hermes-config-validate reconciliation-runtime-evidence-validate \
	reconciliation-runtime-projection-plan reconciliation-runtime-projection-approve \
	reconciliation-runtime-projection-verify reconciliation-cutover-gate \
	reconciliation-report reconciliation-unified-report reconciliation-preflight reconciliation-discover-tailnet \
	reconciliation-render-direct reconciliation-up-direct reconciliation-render-router \
	reconciliation-up-router reconciliation-render-executor \
	reconciliation-render-executor-scoped reconciliation-executor-containment-validate \
	reconciliation-executor-containment-scoped reconciliation-executor-up \
	reconciliation-executor-up-scoped reconciliation-images-capture \
	reconciliation-images-verify-offline reconciliation-airgap-gate \
	reconciliation-verify reconciliation-status reconciliation-down

reconciliation-worktrees-plan:
	@chmod +x scripts/bootstrap-reconciliation-worktrees.sh
	@./scripts/bootstrap-reconciliation-worktrees.sh

reconciliation-worktrees-apply:
	@chmod +x scripts/bootstrap-reconciliation-worktrees.sh
	@./scripts/bootstrap-reconciliation-worktrees.sh --apply

reconciliation-init:
	@test -f $(RECON_ENV_FILE) || cp deploy/reconciliation.env.example $(RECON_ENV_FILE)
	@test -f $(RECON_DEPENDENCY_FILE) || cp deploy/reconciliation/external-dependencies.example.yaml $(RECON_DEPENDENCY_FILE)
	@test -f $(RECON_FINAL_EVIDENCE_FILE) || cp deploy/reconciliation/final-cutover-evidence.example.yaml $(RECON_FINAL_EVIDENCE_FILE)
	@test -f $(RECON_PROJECTION_MANIFEST) || cp deploy/reconciliation/runtime-projection.example.yaml $(RECON_PROJECTION_MANIFEST)
	@mkdir -p artifacts/reconciliation-render $(RECON_IMAGE_DIR) $(RECON_HERMES_HOME)
	@test -f $(RECON_HERMES_CONFIG) || cp deploy/reconciliation/hermes-config.yaml.example $(RECON_HERMES_CONFIG)
	@chmod 700 $(RECON_HERMES_HOME)
	@chmod 600 $(RECON_ENV_FILE) $(RECON_DEPENDENCY_FILE) $(RECON_FINAL_EVIDENCE_FILE) $(RECON_PROJECTION_MANIFEST) $(RECON_HERMES_CONFIG)
	@chmod +x scripts/reconciliation-preflight.sh scripts/reconciliation-verify-offline.sh
	@chmod +x scripts/reconciliation-discover-tailnet.py scripts/validate-external-dependencies.py
	@chmod +x scripts/validate-reconciliation-state.py scripts/validate-final-cutover-evidence.py
	@chmod +x scripts/render-reconciliation-report.py scripts/approve-runtime-projection.py
	@chmod +x scripts/reconciliation-unified-report.py
	@chmod +x scripts/validate-runtime-evidence.py scripts/validate-executor-containment.py
	@chmod +x scripts/validate-hermes-external-config.py scripts/reconciliation-image-bundle.py
	@chmod +x scripts/verify-runtime-projection.py
	@$(MAKE) reconciliation-state-init
	@$(MAKE) reconciliation-hermes-config-validate
	@echo "Review and replace every change-me value in $(RECON_ENV_FILE) before startup."
	@echo "Populate $(RECON_DEPENDENCY_FILE) and $(RECON_FINAL_EVIDENCE_FILE) with current evidence."
	@echo "Review $(RECON_PROJECTION_MANIFEST); approval is dry-run unless APPLY_RUNTIME_PROJECTION=YES."
	@echo "Every runtime evidence_ref must exist under artifacts/ and match evidence_sha256."
	@echo "Hermes external config: $(RECON_HERMES_CONFIG)"
	@echo "Optionally copy deploy/reconciliation/lan-runtime-map.example.json to $(RECON_LAN_MAP) and replace every sample address."

reconciliation-state-init:
	@test -f $(RECON_STATE_FILE) || cp deploy/reconciliation/migration-state.example.yaml $(RECON_STATE_FILE)
	@chmod 600 $(RECON_STATE_FILE)
	@echo "Migration state ledger: $(RECON_STATE_FILE)"

reconciliation-state-validate:
	@python scripts/validate-reconciliation-state.py $(RECON_STATE_FILE)

reconciliation-dependencies-validate:
	@python scripts/validate-external-dependencies.py $(RECON_DEPENDENCY_FILE)

reconciliation-final-evidence-validate:
	@python scripts/validate-final-cutover-evidence.py $(RECON_FINAL_EVIDENCE_FILE)

reconciliation-hermes-config-validate:
	@test -f $(RECON_HERMES_CONFIG) || (echo "Missing $(RECON_HERMES_CONFIG); run make reconciliation-init" >&2; exit 2)
	@python scripts/validate-hermes-external-config.py $(RECON_HERMES_CONFIG)

reconciliation-runtime-evidence-validate:
	@test -f $(RECON_PROJECTION_MANIFEST) || (echo "Missing $(RECON_PROJECTION_MANIFEST); run make reconciliation-init" >&2; exit 2)
	@python scripts/validate-runtime-evidence.py $(RECON_PROJECTION_MANIFEST) \
		--root $(CURDIR) --output $(RECON_RUNTIME_EVIDENCE)

reconciliation-runtime-projection-plan: reconciliation-runtime-evidence-validate
	@python scripts/approve-runtime-projection.py $(RECON_PROJECTION_MANIFEST) \
		--evidence-output $(RECON_PROJECTION_APPROVAL_EVIDENCE)

reconciliation-runtime-projection-approve: reconciliation-runtime-evidence-validate
	@test "$(APPLY_RUNTIME_PROJECTION)" = "YES" || (echo "Set APPLY_RUNTIME_PROJECTION=YES after reviewing the dry run" >&2; exit 2)
	@test -f $(RECON_ENV_FILE) || (echo "Missing $(RECON_ENV_FILE); run make reconciliation-init" >&2; exit 2)
	@set -a; . $(RECON_ENV_FILE); set +a; \
		python scripts/approve-runtime-projection.py $(RECON_PROJECTION_MANIFEST) --apply \
			--evidence-output $(RECON_PROJECTION_APPROVAL_EVIDENCE)

reconciliation-runtime-projection-verify:
	@test -f $(RECON_ENV_FILE) || (echo "Missing $(RECON_ENV_FILE); run make reconciliation-init" >&2; exit 2)
	@set -a; . $(RECON_ENV_FILE); set +a; \
		python scripts/verify-runtime-projection.py \
			--assistx-url "$${RECONCILIATION_NEW_ASSISTX_URL:-http://127.0.0.1:18000}" \
			--router-url "$${RECONCILIATION_NEW_ROUTER_URL:-http://127.0.0.1:18088}" \
			--output $(RECON_PROJECTION_EVIDENCE)

reconciliation-cutover-gate: reconciliation-dependencies-validate reconciliation-hermes-config-validate reconciliation-runtime-evidence-validate reconciliation-executor-containment-validate reconciliation-images-verify-offline reconciliation-runtime-projection-verify reconciliation-final-evidence-validate
	@python scripts/validate-reconciliation-state.py --require-cutover $(RECON_STATE_FILE)

reconciliation-report:
	@python scripts/render-reconciliation-report.py $(RECON_STATE_FILE) --output $(RECON_REPORT_FILE)
	@echo "Rendered $(RECON_REPORT_FILE)"

reconciliation-unified-report:
	@python scripts/reconciliation-unified-report.py $(RECON_STATE_FILE) \
		$(if $(wildcard $(RECON_TAILSCALE_OUTPUT)),--candidates $(RECON_TAILSCALE_OUTPUT),) \
		--output artifacts/reconciliation-unified-report.json
	@echo "Rendered artifacts/reconciliation-unified-report.json"

reconciliation-preflight:
	@./scripts/reconciliation-preflight.sh

reconciliation-discover-tailnet:
	@args=""; \
	if [ -n "$(RECON_TAILSCALE_STATUS)" ]; then args="$$args --input $(RECON_TAILSCALE_STATUS)"; fi; \
	if [ -f "$(RECON_LAN_MAP)" ]; then args="$$args --lan-map $(RECON_LAN_MAP)"; fi; \
	python scripts/reconciliation-discover-tailnet.py $$args --output "$(RECON_TAILSCALE_OUTPUT)"

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

reconciliation-render-executor:
	@mkdir -p artifacts/reconciliation-render $(RECON_HERMES_HOME)
	@RECONCILIATION_HERMES_HOME=$(RECON_HERMES_HOME) $(RECON_EXECUTOR) config --format json > $(RECON_EXECUTOR_RENDER)
	@echo "Rendered $(RECON_EXECUTOR_RENDER)"

reconciliation-render-executor-scoped:
	@test -n "$$ASSISTX_EXECUTOR_WORKTREE" || (echo "ASSISTX_EXECUTOR_WORKTREE is required" >&2; exit 2)
	@mkdir -p artifacts/reconciliation-render $(RECON_HERMES_HOME)
	@RECONCILIATION_HERMES_HOME=$(RECON_HERMES_HOME) $(RECON_EXECUTOR_SCOPED) config --format json > $(RECON_EXECUTOR_SCOPED_RENDER)
	@echo "Rendered $(RECON_EXECUTOR_SCOPED_RENDER)"

reconciliation-executor-containment-validate: reconciliation-hermes-config-validate reconciliation-render-executor
	@RECONCILIATION_HERMES_HOME=$(RECON_HERMES_HOME) \
		python scripts/validate-executor-containment.py $(RECON_EXECUTOR_RENDER)

reconciliation-executor-containment-scoped: reconciliation-hermes-config-validate reconciliation-render-executor-scoped
	@RECONCILIATION_HERMES_HOME=$(RECON_HERMES_HOME) \
		ASSISTX_EXECUTOR_WORKTREE="$$ASSISTX_EXECUTOR_WORKTREE" \
		python scripts/validate-executor-containment.py $(RECON_EXECUTOR_SCOPED_RENDER)

reconciliation-executor-up: reconciliation-executor-containment-validate
	@RECONCILIATION_HERMES_HOME=$(RECON_HERMES_HOME) \
		$(RECON_EXECUTOR) up -d --build hermes-adapter

reconciliation-executor-up-scoped: reconciliation-executor-containment-scoped
	@RECONCILIATION_HERMES_HOME=$(RECON_HERMES_HOME) \
		ASSISTX_EXECUTOR_WORKTREE="$$ASSISTX_EXECUTOR_WORKTREE" \
		$(RECON_EXECUTOR_SCOPED) up -d --build hermes-adapter

reconciliation-images-capture:
	@mkdir -p $(RECON_IMAGE_DIR)
	@set -a; . $(RECON_ENV_FILE); set +a; \
		{ $(RECON_EXECUTOR) images -q; \
		  $(MAKE) --no-print-directory -s -C $(RECON_ROUTER_ROOT) reconciliation-image-ids; \
		} | sed '/^[[:space:]]*$$/d' | sort -u > $(RECON_IMAGE_DIR)/image-ids.txt
	@test -s $(RECON_IMAGE_DIR)/image-ids.txt || (echo "No AssistX or auto-router image IDs were resolved" >&2; exit 2)
	@python scripts/reconciliation-image-bundle.py capture \
		--image-id-file $(RECON_IMAGE_DIR)/image-ids.txt \
		--output-dir $(RECON_IMAGE_DIR)

reconciliation-images-verify-offline:
	@test -f $(RECON_IMAGE_MANIFEST) || (echo "Missing $(RECON_IMAGE_MANIFEST); run make reconciliation-images-capture" >&2; exit 2)
	@python scripts/reconciliation-image-bundle.py verify --manifest $(RECON_IMAGE_MANIFEST)

reconciliation-airgap-gate: reconciliation-hermes-config-validate reconciliation-executor-containment-validate reconciliation-images-verify-offline
	@echo "AIRGAP_CONTAINMENT_AND_AUTHORITY: PASS"

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
	@set -a; . $(RECON_ENV_FILE); set +a; \
		curl -fsS "$${RECONCILIATION_NEW_ASSISTX_URL:-http://127.0.0.1:18000}/health" | jq; \
		curl -fsS "$${RECONCILIATION_NEW_ROUTER_URL:-http://127.0.0.1:18088}/health" | jq; \
		curl -fsS -u "$${BASIC_AUTH_USER}:$${BASIC_AUTH_PASS}" \
			"$${RECONCILIATION_NEW_ASSISTX_URL:-http://127.0.0.1:18000}/api/control-room/overview" | jq '.overall_status, .summary'; \
		curl -fsS -H "X-Admin-Token: $${AUTO_ROUTER_ADMIN_TOKEN}" \
			"$${RECONCILIATION_NEW_ROUTER_URL:-http://127.0.0.1:18088}/admin/runtime-projection" | jq '.configured, .fresh, .current.generation, .current.checksum, .current.expires_in_ms, .last_error'

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
