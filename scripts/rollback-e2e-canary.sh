#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CANARY_ENV_FILE:-${ROOT}/deploy/canary.env}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[rollback-canary] missing ${ENV_FILE}" >&2
  exit 2
fi

compose=(
  docker compose
  --env-file "${ENV_FILE}"
  -f "${ROOT}/docker-compose.yml"
  -f "${ROOT}/compose.prod.yml"
  -f "${ROOT}/compose.canary.yml"
)

# Preserve volumes and Neo4j data by default. Explicit removal requires a
# second operator-controlled action after evidence has been reviewed.
"${compose[@]}" stop hermes-adapter worker api redis neo4j 2>/dev/null || true
"${compose[@]}" rm -f hermes-adapter worker api redis neo4j 2>/dev/null || true

echo "[rollback-canary] canary containers removed"
echo "[rollback-canary] named volumes and Neo4j data were preserved"
