#!/usr/bin/env bash
set -euo pipefail

# Read-only host/runtime capture for deployment reconciliation.
# This script does not restart, reload, stop, start, mutate, prune, or deploy anything.

STAMP="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT="${RECON_CAPTURE_DIR:-artifacts/reconciliation-runtime/$STAMP}"
mkdir -p "$OUT"

capture() {
  local name="$1"
  shift
  {
    printf '# command:'
    printf ' %q' "$@"
    printf '\n# captured_at: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    "$@"
  } >"$OUT/$name.txt" 2>&1 || true
}

capture shell-uname uname -a
capture os-release cat /etc/os-release
capture docker-version docker version
capture docker-info docker info
capture docker-ps docker ps --no-trunc
capture docker-compose-ls docker compose ls
capture docker-network-ls docker network ls
capture docker-volume-ls docker volume ls
capture docker-system-df docker system df
capture listening-sockets ss -lntup
capture routes ip route
capture addresses ip address
capture tailscale-version tailscale version
capture tailscale-status tailscale status --json
capture tailscale-ip tailscale ip
capture caddy-version caddy version
capture caddy-systemd systemctl status caddy --no-pager
capture caddy-unit systemctl cat caddy
capture caddy-process ps -ef

# Capture likely proxy config paths, but never private key/certificate contents.
for path in /etc/caddy/Caddyfile /etc/caddy/caddy.json ./Caddyfile ./deploy/caddy/Caddyfile; do
  if [[ -f "$path" ]]; then
    safe_name="$(printf '%s' "$path" | tr '/.' '__')"
    cp "$path" "$OUT/caddy-config-${safe_name}.txt"
    sha256sum "$path" >"$OUT/caddy-config-${safe_name}.sha256"
  fi
done

# Compose render is read-only. Do not fail the whole collection if unresolved env
# prevents one candidate from rendering on this host.
for compose_file in docker-compose.yml compose.prod.yml compose.production.reconciled.yml compose.reconciliation.yml; do
  if [[ -f "$compose_file" ]]; then
    safe_name="${compose_file//./_}"
    docker compose -f "$compose_file" config >"$OUT/${safe_name}-config.txt" 2>&1 || true
    sha256sum "$compose_file" >"$OUT/${safe_name}.sha256"
  fi
done

# Record repository identity when run from a checkout.
{
  git rev-parse --show-toplevel
  git rev-parse HEAD
  git status --short
  git remote -v
} >"$OUT/git-state.txt" 2>&1 || true

cat >"$OUT/README.txt" <<EOF
Read-only reconciliation capture.
Created: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Directory: $OUT

Review every artifact before committing. Do NOT commit secrets, private keys,
certificates, auth keys, tokens, environment files, or raw Docker inspect output
that contains secrets. Caddy config is captured because it is specifically needed
for reconciliation; redact credentials if the working config embeds any.
EOF

printf 'Captured read-only reconciliation evidence in %s\n' "$OUT"
