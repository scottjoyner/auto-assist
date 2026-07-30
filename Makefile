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
