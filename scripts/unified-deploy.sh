#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ASSISTX_DEPLOY_ENV_FILE:-deploy/reconciliation.env}"
STATE_FILE="${ASSISTX_MIGRATION_STATE_FILE:-deploy/reconciliation/migration-state.yaml}"
EDGE_FILE="${ASSISTX_EDGE_EXPOSURE_FILE:-deploy/reconciliation/edge-exposure.yaml}"
COMPOSE_FILES=(docker-compose.yml compose.prod.yml compose.production.reconciled.yml)

compose_args=()
for file in "${COMPOSE_FILES[@]}"; do
  compose_args+=( -f "$file" )
done

usage() {
  cat <<'EOF'
Usage: bash scripts/unified-deploy.sh <doctor|capture|plan|validate|apply|rollback-plan>

This is the canonical operator-facing deployment entry point for the reconciled
AssistX stack. It is fail-closed. `apply` requires a passing cutover ledger and an
explicit ASSISTX_UNIFIED_DEPLOY_APPROVED=YES acknowledgement.
EOF
}

require_file() {
  [[ -f "$1" ]] || { echo "BLOCKED: missing required file: $1" >&2; exit 2; }
}

cmd_doctor() {
  local failed=0
  for bin in docker python3 sha256sum; do
    if command -v "$bin" >/dev/null 2>&1; then
      printf 'PASS binary %s\n' "$bin"
    else
      printf 'FAIL binary %s\n' "$bin"
      failed=1
    fi
  done
  for file in "${COMPOSE_FILES[@]}" deploy/reconciliation/README.md scripts/validate-reconciliation-state.py; do
    if [[ -f "$file" ]]; then
      printf 'PASS file %s\n' "$file"
    else
      printf 'FAIL file %s\n' "$file"
      failed=1
    fi
  done
  if command -v tailscale >/dev/null 2>&1; then
    tailscale status >/dev/null 2>&1 && echo 'PASS tailscale status' || echo 'WARN tailscale installed but status unavailable'
  else
    echo 'WARN tailscale command not installed'
  fi
  if command -v caddy >/dev/null 2>&1; then
    caddy version || true
  else
    echo 'WARN caddy command not installed on this host'
  fi
  [[ "$failed" -eq 0 ]]
}

cmd_capture() {
  bash scripts/reconciliation-capture-runtime.sh
}

cmd_plan() {
  echo 'Compose sequence:'
  printf '  %s\n' "${COMPOSE_FILES[@]}"
  echo "Environment: $ENV_FILE"
  echo "Migration ledger: $STATE_FILE"
  echo "Edge exposure contract: $EDGE_FILE"
  printf '\nRender command:\n  docker compose --env-file %q' "$ENV_FILE"
  printf ' -f %q' "${COMPOSE_FILES[@]}"
  printf ' config\n'
  printf '\nApply command (guarded by this wrapper):\n  docker compose --env-file %q' "$ENV_FILE"
  printf ' -f %q' "${COMPOSE_FILES[@]}"
  printf ' up -d --remove-orphans\n'
}

cmd_validate() {
  require_file "$ENV_FILE"
  require_file "$STATE_FILE"
  python3 scripts/validate-reconciliation-state.py "$STATE_FILE" --require-cutover
  docker compose --env-file "$ENV_FILE" "${compose_args[@]}" config >/dev/null
  echo 'PASS reconciled Compose render'

  if [[ -f "$EDGE_FILE" ]]; then
    echo "PASS edge exposure contract present: $EDGE_FILE"
  else
    echo "WARN edge exposure contract not yet populated: $EDGE_FILE"
  fi

  local caddy_config="${ASSISTX_CADDY_CONFIG:-}"
  if [[ -n "$caddy_config" ]]; then
    require_file "$caddy_config"
    command -v caddy >/dev/null 2>&1 || { echo 'BLOCKED: caddy config supplied but caddy binary unavailable' >&2; exit 2; }
    caddy validate --config "$caddy_config"
    echo 'PASS Caddy validation'
  fi
}

cmd_apply() {
  [[ "${ASSISTX_UNIFIED_DEPLOY_APPROVED:-}" == "YES" ]] || {
    echo 'BLOCKED: set ASSISTX_UNIFIED_DEPLOY_APPROVED=YES only after operator review.' >&2
    exit 2
  }
  cmd_validate
  docker compose --env-file "$ENV_FILE" "${compose_args[@]}" up -d --remove-orphans
  echo 'Deployment command completed. Run doctor and service-specific smoke checks before declaring success.'
}

cmd_rollback_plan() {
  require_file "$STATE_FILE"
  echo "Rollback authority is recorded in: $STATE_FILE"
  echo 'This wrapper intentionally does not invent or execute rollback commands.'
  echo 'Review the ledger, final cutover packet, and captured previous-config checksums.'
}

case "${1:-}" in
  doctor) cmd_doctor ;;
  capture) cmd_capture ;;
  plan) cmd_plan ;;
  validate) cmd_validate ;;
  apply) cmd_apply ;;
  rollback-plan) cmd_rollback_plan ;;
  *) usage; exit 2 ;;
esac
