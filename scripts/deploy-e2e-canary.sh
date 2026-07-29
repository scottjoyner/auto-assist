#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CANARY_ENV_FILE:-${ROOT}/deploy/canary.env}"
EVIDENCE_ROOT="${CANARY_EVIDENCE_ROOT:-${ROOT}/artifacts/deployment-canary}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_DIR="${EVIDENCE_ROOT}/${STAMP}"
COMPOSE=(
  docker compose
  --env-file "${ENV_FILE}"
  -f "${ROOT}/docker-compose.yml"
  -f "${ROOT}/compose.prod.yml"
  -f "${ROOT}/compose.canary.yml"
)

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[deploy-canary] missing ${ENV_FILE}" >&2
  echo "Copy deploy/canary.env.example and replace every placeholder." >&2
  exit 2
fi
command -v docker >/dev/null
docker compose version >/dev/null
compose_version="$(docker compose version --short | sed 's/^v//')"
if [[ "$(printf '%s\n' "2.24.4" "${compose_version}" | sort -V | head -n1)" != "2.24.4" ]]; then
  echo "[deploy-canary] Docker Compose 2.24.4+ is required; found ${compose_version}" >&2
  exit 2
fi
mkdir -p "${EVIDENCE_DIR}"

while IFS='=' read -r key value; do
  [[ -z "${key}" || "${key}" == \#* ]] && continue
  if [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    export "${key}=${value}"
  fi
done < "${ENV_FILE}"
export ASSISTX_ENV_FILE="${ENV_FILE}"

export CANARY_SOURCE_REVISION
CANARY_SOURCE_REVISION="$(git -C "${ROOT}" rev-parse HEAD)"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

python -m assistx.deployment_canary \
  --validate-env \
  --stages "${CANARY_STAGES:-observe,cache}"

if [[ "${CANARY_EXECUTE_IMPROVEMENT:-false}" == "true" ]] \
  && [[ "${CANARY_START_HERMES:-false}" != "true" ]]; then
  echo "[deploy-canary] improvement execution requires CANARY_START_HERMES=true" >&2
  exit 2
fi

if [[ -n "$(git -C "${ROOT}" status --porcelain)" ]] \
  && [[ "${CANARY_ALLOW_DIRTY_SOURCE:-false}" != "true" ]]; then
  echo "[deploy-canary] source checkout is dirty; refusing deployment" >&2
  exit 2
fi

{
  echo "source_revision=${CANARY_SOURCE_REVISION}"
  echo "started_at=${STAMP}"
  echo "stages=${CANARY_STAGES:-observe,cache}"
} > "${EVIDENCE_DIR}/deployment.txt"

"${COMPOSE[@]}" config >/dev/null
"${COMPOSE[@]}" config --services > "${EVIDENCE_DIR}/compose.services.txt"
"${COMPOSE[@]}" config --images > "${EVIDENCE_DIR}/compose.images.txt"
sha256sum \
  "${ROOT}/docker-compose.yml" \
  "${ROOT}/compose.prod.yml" \
  "${ROOT}/compose.canary.yml" \
  > "${EVIDENCE_DIR}/compose-inputs.sha256"
"${COMPOSE[@]}" ps --all --format json \
  > "${EVIDENCE_DIR}/containers.before.json" 2>/dev/null || true

services=(redis api worker)
if [[ "${CANARY_MANAGED_NEO4J:-false}" == "true" ]]; then
  services=(neo4j "${services[@]}")
fi
if [[ "${CANARY_START_HERMES:-false}" == "true" ]]; then
  services+=(hermes-adapter)
fi

capture_failure() {
  status=$?
  "${COMPOSE[@]}" ps --all > "${EVIDENCE_DIR}/containers.failed.txt" 2>&1 || true
  "${COMPOSE[@]}" logs --no-color --tail 500 \
    > "${EVIDENCE_DIR}/containers.failed.log" 2>&1 || true
  echo "[deploy-canary] failed; evidence preserved at ${EVIDENCE_DIR}" >&2
  exit "${status}"
}
trap capture_failure ERR

"${COMPOSE[@]}" build api worker
"${COMPOSE[@]}" up -d "${services[@]}"

deadline=$((SECONDS + ${CANARY_STARTUP_TIMEOUT_SECONDS:-180}))
until curl -fsS "${CANARY_BASE_URL:-http://127.0.0.1:18000}/health" \
  > "${EVIDENCE_DIR}/health.json"; do
  if (( SECONDS >= deadline )); then
    echo "[deploy-canary] API did not become healthy before timeout" >&2
    exit 1
  fi
  sleep 3
done

canary_args=(
  --base-url "${CANARY_BASE_URL:-http://127.0.0.1:18000}"
  --stages "${CANARY_STAGES:-observe,cache}"
  --node-id "${CANARY_NODE_ID}"
  --evidence "${EVIDENCE_DIR}/canary-report.json"
  --poll-seconds "${CANARY_POLL_SECONDS:-180}"
)
if [[ "${CANARY_EXECUTE_IMPROVEMENT:-false}" == "true" ]]; then
  canary_args+=(--execute-improvement)
fi
if [[ "${CANARY_EXECUTE_RECOVERY:-false}" == "true" ]]; then
  canary_args+=(--execute-recovery)
fi

python -m assistx.deployment_canary "${canary_args[@]}"

"${COMPOSE[@]}" ps --all > "${EVIDENCE_DIR}/containers.after.txt"
"${COMPOSE[@]}" logs --no-color --tail 500 \
  > "${EVIDENCE_DIR}/containers.after.log"
curl -fsS -u "${BASIC_AUTH_USER}:${BASIC_AUTH_PASS}" \
  "${CANARY_BASE_URL:-http://127.0.0.1:18000}/metrics" \
  > "${EVIDENCE_DIR}/metrics.prom"

trap - ERR
echo "[deploy-canary] PASS"
echo "[deploy-canary] evidence=${EVIDENCE_DIR}"
echo "[deploy-canary] rollback: CANARY_ENV_FILE=${ENV_FILE} scripts/rollback-e2e-canary.sh"
