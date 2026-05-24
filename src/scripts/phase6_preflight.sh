#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
AUTH_USER="${BASIC_AUTH_USER:-}"
AUTH_PASS="${BASIC_AUTH_PASS:-}"

required_env=(
  BASIC_AUTH_USER
  BASIC_AUTH_PASS
  PAPERCLIP_WEBHOOK_SECRET
  VOICE_WEBHOOK_SECRET
  WS_AUTH_TOKEN
  PAPERCLIP_API_TOKEN
)

echo "[preflight] Checking required environment variables..."
missing=0
for var in "${required_env[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "  - MISSING: ${var}"
    missing=1
  else
    echo "  - OK: ${var}"
  fi
done
if [[ "${missing}" -ne 0 ]]; then
  echo "[preflight] Missing required environment values."
  exit 1
fi

echo "[preflight] Checking API health..."
curl -fsS "${BASE_URL}/health" >/dev/null
echo "  - OK: ${BASE_URL}/health"

echo "[preflight] Checking ops status..."
curl -fsS -u "${AUTH_USER}:${AUTH_PASS}" "${BASE_URL}/api/ops/status" >/dev/null
echo "  - OK: ${BASE_URL}/api/ops/status"

echo "[preflight] Checking metrics endpoint..."
curl -fsS -u "${AUTH_USER}:${AUTH_PASS}" "${BASE_URL}/metrics" >/dev/null
echo "  - OK: ${BASE_URL}/metrics"

echo "[preflight] Complete."
