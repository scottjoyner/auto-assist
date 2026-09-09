#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ASSISTX_ENV_FILE:-.env}"
EXPECTED_SOURCE_SHA="${KIPNERTER_GATEWAY_SOURCE_SHA:-}"
ALLOW_DIRTY="${KIPNERTER_GATEWAY_ALLOW_DIRTY:-0}"
CADDY_FENCE_CONFIRMED="${KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED:-0}"
API_PORT="${ASSISTX_API_PORT:-8000}"
IDENTITY_PROBE="${KIPNERTER_GATEWAY_IDENTITY_PROBE:-0}"
AGENT_SMOKE="${KIPNERTER_GATEWAY_AGENT_SMOKE:-0}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

for command_name in git docker curl python3 tailscale; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required"
done

docker compose version >/dev/null 2>&1 || fail "docker compose v2 is required"
[[ -f "$ENV_FILE" ]] || fail "AssistX environment file does not exist: $ENV_FILE"
[[ -f scripts/configure-kipnerter-tailnet-serve.sh ]] || \
  fail "scripts/configure-kipnerter-tailnet-serve.sh is missing"
[[ -f scripts/verify-kipnerter-tailnet-gateway.sh ]] || \
  fail "scripts/verify-kipnerter-tailnet-gateway.sh is missing"

actual_sha="$(git rev-parse HEAD)"
if [[ -n "$EXPECTED_SOURCE_SHA" && "$actual_sha" != "$EXPECTED_SOURCE_SHA" ]]; then
  fail "checkout ${actual_sha} does not match required backend SHA ${EXPECTED_SOURCE_SHA}"
fi

if [[ "$ALLOW_DIRTY" != "1" ]] && \
   (! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]); then
  fail "working tree is dirty; exact-source deployment refused (set KIPNERTER_GATEWAY_ALLOW_DIRTY=1 only for controlled recovery)"
fi

if [[ "$CADDY_FENCE_CONFIRMED" != "1" ]]; then
  fail "legacy x1-370 Caddy Tailscale-header fence is not confirmed; deploy/reload scottjoyner/Sophia#13 first, verify it strips Tailscale-User-* headers on /assistx/*, then set KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED=1"
fi

export ASSISTX_ENV_FILE="$ENV_FILE"
export ASSISTX_API_BIND=127.0.0.1
export ASSISTX_API_PORT="$API_PORT"
export TRUSTED_AUTH_HEADER=Tailscale-User-Login
export KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE=0
export KIPNERTER_AGENT_TIMEOUT="${KIPNERTER_AGENT_TIMEOUT:-300}"

backup="${ENV_FILE}.pre-kipnerter-gateway.$(date -u +%Y%m%dT%H%M%SZ)"
cp -p "$ENV_FILE" "$backup"
chmod 600 "$backup" 2>/dev/null || true

echo "environment_backup=$backup"
echo "backend_source_sha=$actual_sha"
echo "legacy_caddy_fence_confirmed=true"

python3 - "$ENV_FILE" <<'PY'
from __future__ import annotations

import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
forced = {
    "ASSISTX_API_BIND": "127.0.0.1",
    "ASSISTX_API_PORT": os.environ.get("ASSISTX_API_PORT", "8000"),
    "TRUSTED_AUTH_HEADER": "Tailscale-User-Login",
    "KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE": "0",
    "KIPNERTER_AGENT_TIMEOUT": os.environ.get("KIPNERTER_AGENT_TIMEOUT", "300"),
}

allowed_logins = os.environ.get("KIPNERTER_TAILNET_ALLOWED_LOGINS")
if allowed_logins is not None:
    forced["KIPNERTER_TAILNET_ALLOWED_LOGINS"] = allowed_logins.strip()

seen: set[str] = set()
out: list[str] = []
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith("#") and "=" in line:
        key = line.split("=", 1)[0].strip()
        if key in forced:
            if key not in seen:
                out.append(f"{key}={forced[key]}")
                seen.add(key)
            continue
    out.append(line)

missing = [key for key in forced if key not in seen]
if missing:
    if out and out[-1] != "":
        out.append("")
    out.append("# ---- Kipnerter Tailnet mobile gateway ----")
    for key in missing:
        out.append(f"{key}={forced[key]}")

path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

chmod 600 "$ENV_FILE" 2>/dev/null || true

compose=(docker compose --env-file "$ENV_FILE")
"${compose[@]}" config >/dev/null

# Recreate the API so both the trusted-header configuration and loopback-only
# host port publication take effect. Dependencies are started if needed but are
# not force-recreated.
"${compose[@]}" up -d --build --force-recreate api

healthy=0
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 3 \
      "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done
[[ "$healthy" == "1" ]] || fail "AssistX API did not become healthy on loopback port ${API_PORT}"

TRUSTED_AUTH_HEADER="$TRUSTED_AUTH_HEADER" \
ASSISTX_API_PORT="$API_PORT" \
  bash scripts/configure-kipnerter-tailnet-serve.sh

TRUSTED_AUTH_HEADER="$TRUSTED_AUTH_HEADER" \
ASSISTX_API_PORT="$API_PORT" \
KIPNERTER_GATEWAY_IDENTITY_PROBE="$IDENTITY_PROBE" \
KIPNERTER_GATEWAY_AGENT_SMOKE="$AGENT_SMOKE" \
  bash scripts/verify-kipnerter-tailnet-gateway.sh

cat <<EOF

Kipnerter gateway deployment wiring completed.
backend_source_sha=${actual_sha}
environment_backup=${backup}
legacy_caddy_fence_confirmed=true

The server-side transport gate is configured. Release authority still requires
an enrolled physical iPhone to prove /api/v1/auth/whoami and an Agent Auto chat
through the HTTPS Serve URL before RC2 TestFlight upload is enabled.

Manual rollback if needed:
  cp '${backup}' '${ENV_FILE}'
  docker compose --env-file '${ENV_FILE}' up -d --build --force-recreate api
EOF
