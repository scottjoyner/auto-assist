#!/usr/bin/env bash
# Collect a non-secret baseline of the currently running stack before migration.
# This script is read-only. It does not stop services, load models, alter Git state,
# inspect container environment values, or write to Neo4j.
set -euo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${RECONCILIATION_EVIDENCE_DIR:-artifacts/reconciliation-preflight}/${STAMP}"
REPO_ROOT="${RECONCILIATION_REPO_ROOT:-/home/scott/git}"
mkdir -p "$OUT"
chmod 700 "$OUT"

log() { printf '[preflight] %s\n' "$*"; }

capture() {
  local name="$1"; shift
  {
    printf '# command:'
    printf ' %q' "$@"
    printf '\n# captured_at: %s\n' "$(date -u +%FT%TZ)"
    "$@"
  } >"$OUT/$name.txt" 2>&1 || true
}

capture_shell() {
  local name="$1"; shift
  {
    printf '# command: %s\n# captured_at: %s\n' "$*" "$(date -u +%FT%TZ)"
    bash -lc "$*"
  } >"$OUT/$name.txt" 2>&1 || true
}

log "writing read-only evidence to $OUT"

{
  echo "captured_at=$(date -u +%FT%TZ)"
  echo "hostname=$(hostname 2>/dev/null || true)"
  echo "user=$(id -un 2>/dev/null || true)"
  echo "kernel=$(uname -srmo 2>/dev/null || true)"
  echo "repo_root=$REPO_ROOT"
} >"$OUT/host-summary.env"

for cmd in git docker curl jq python3 ss tailscale lms lms-agent; do
  if command -v "$cmd" >/dev/null 2>&1; then
    printf '%s=%s\n' "$cmd" "$(command -v "$cmd")" >>"$OUT/commands.env"
  else
    printf '%s=MISSING\n' "$cmd" >>"$OUT/commands.env"
  fi
done

capture git-version git --version
capture docker-version docker version
capture docker-compose-version docker compose version
capture docker-compose-projects docker compose ls --all
capture docker-containers docker ps -a --format '{{json .}}'
capture docker-images docker image ls --digests --format '{{json .}}'
capture docker-volumes docker volume ls --format '{{json .}}'
capture docker-networks docker network ls --format '{{json .}}'
capture listening-sockets ss -ltnp
capture disk-space df -hT
capture memory free -h

if command -v tailscale >/dev/null 2>&1; then
  capture tailscale-status-json tailscale status --json
  capture tailscale-ipv4 tailscale ip -4
fi

# Health probes are intentionally unauthenticated and bounded. A 401/403 is useful
# evidence that the service exists and requires credentials.
for spec in \
  "old-assistx:${RECONCILIATION_OLD_ASSISTX_URL:-http://127.0.0.1:8000}" \
  "old-router:${RECONCILIATION_OLD_ROUTER_URL:-http://127.0.0.1:8088}" \
  "new-assistx:${RECONCILIATION_NEW_ASSISTX_URL:-http://127.0.0.1:18000}" \
  "new-router:${RECONCILIATION_NEW_ROUTER_URL:-http://127.0.0.1:18088}"; do
  name="${spec%%:*}"
  url="${spec#*:}"
  capture_shell "health-${name}" "curl -sS -i --max-time 5 '${url%/}/health'"
  capture_shell "models-${name}" "curl -sS -i --max-time 5 '${url%/}/v1/models'"
done

# Record only branch/status/revision. Do not capture remotes because credentials can
# be embedded in HTTPS remote URLs.
repos=(
  auto-assist auto-router auto-assign hermes-agent fleet-llm-profiles
  fleet-inference-configs fleet-resilience lms ai-research-vault
)
for repo in "${repos[@]}"; do
  path="$REPO_ROOT/$repo"
  if [ -d "$path/.git" ] || git -C "$path" rev-parse --git-dir >/dev/null 2>&1; then
    {
      echo "path=$path"
      git -C "$path" status -sb
      printf 'head='; git -C "$path" rev-parse HEAD
      printf 'branch='; git -C "$path" branch --show-current
    } >"$OUT/git-$repo.txt" 2>&1 || true
  else
    printf 'missing_or_not_git=%s\n' "$path" >"$OUT/git-$repo.txt"
  fi
done

# The official LM Studio CLI is the physical-process source. Hosts are a comma-
# separated operator-provided list; do not infer physical ownership from localhost.
if command -v lms >/dev/null 2>&1; then
  capture lms-server-status lms server status
  IFS=',' read -r -a lms_hosts <<<"${RECONCILIATION_LMS_HOSTS:-}"
  for host in "${lms_hosts[@]}"; do
    host="$(echo "$host" | xargs)"
    [ -n "$host" ] || continue
    safe="$(echo "$host" | tr -c 'A-Za-z0-9._-' '_')"
    capture "lms-ps-$safe" lms ps --json --host "$host"
    capture "lms-ls-$safe" lms ls --json --host "$host"
  done
fi

# Hash the evidence files so later migration steps can prove the baseline did not
# change silently. The manifest does not include itself.
(
  cd "$OUT"
  find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum
) >"$OUT/SHA256SUMS"

cat >"$OUT/README.txt" <<EOF
This directory is a read-only migration baseline.
It intentionally excludes environment values, Docker inspect environment arrays,
Neo4j query results containing application data, private keys, and API tokens.
Review failures in individual files before proceeding. A command failure is not
proof that a component is absent.
EOF

log "complete: $OUT"
