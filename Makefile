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
