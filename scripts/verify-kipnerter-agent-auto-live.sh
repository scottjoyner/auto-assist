#!/usr/bin/env bash
set -euo pipefail

# Bounded live acceptance for the Kipnerter Agent Auto gateway.
# This script never changes Tailscale Serve, never prints router/admin secrets,
# recreates only assistx-api, makes no direct Auto-Router probe, and sends
# exactly one no-mutation Agent Auto chat smoke.

die() {
  FAILURE_REASON="$*"
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*"
}

for command_name in git docker curl tailscale python sha256sum; do
  command -v "$command_name" >/dev/null || die "$command_name is required"
done

EXPECTED_SHA="${EXPECTED_SHA:-}"
[[ -n "$EXPECTED_SHA" ]] || die "EXPECTED_SHA is required"
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || die "EXPECTED_SHA must be a full 40-character commit SHA"

ACTUAL_SHA="$(git rev-parse HEAD)"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] || die "checkout is $ACTUAL_SHA, expected $EXPECTED_SHA"
[[ -z "$(git status --porcelain)" ]] || die "worktree is dirty; refusing live recreate"

KIPNERTER_GATEWAY_URL="${KIPNERTER_GATEWAY_URL:-https://x1-370.tailcb8954.ts.net:8443}"
KIPNERTER_GATEWAY_URL="${KIPNERTER_GATEWAY_URL%/}"
[[ "$KIPNERTER_GATEWAY_URL" == https://* ]] || die "KIPNERTER_GATEWAY_URL must use HTTPS"

export ASSISTX_ENV_FILE="${ASSISTX_ENV_FILE:-.env}"
ASSISTX_PROD_COMPOSE_FILE="${ASSISTX_PROD_COMPOSE_FILE:-compose.prod.yml}"
EVIDENCE_ROOT="${EVIDENCE_ROOT:-artifacts/kipnerter-agent-auto-live}"

[[ -f docker-compose.yml ]] || die "run from the auto-assist repository root"
[[ -f "$ASSISTX_PROD_COMPOSE_FILE" ]] || die "missing $ASSISTX_PROD_COMPOSE_FILE"
[[ -f "$ASSISTX_ENV_FILE" ]] || die "missing $ASSISTX_ENV_FILE"

umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_DIR="$EVIDENCE_ROOT/$STAMP"
mkdir -p "$EVIDENCE_DIR"

RESULT="BLOCKED"
STAGE="predeploy_evidence"
FAILURE_REASON=""

write_report() {
  local rc="$1"
  python scripts/render-kipnerter-agent-auto-report.py \
    --evidence-dir "$EVIDENCE_DIR" \
    --result "$RESULT" \
    --stage "$STAGE" \
    --source-sha "$ACTUAL_SHA" \
    --gateway "$KIPNERTER_GATEWAY_URL" \
    --exit-code "$rc" \
    --failure-reason "$FAILURE_REASON" \
    --timestamp-utc "$STAMP"
}

on_exit() {
  local rc="$?"
  write_report "$rc" || true
}

trap on_exit EXIT

printf '%s\n' "$ACTUAL_SHA" > "$EVIDENCE_DIR/source-sha.txt"
printf '%s\n' "$KIPNERTER_GATEWAY_URL" > "$EVIDENCE_DIR/gateway-url.txt"

STAGE="capture_predeploy_serve"
note "Capturing pre-deploy Serve evidence"
tailscale serve status --json > "$EVIDENCE_DIR/tailscale-serve-before.json"
sha256sum "$EVIDENCE_DIR/tailscale-serve-before.json" > "$EVIDENCE_DIR/tailscale-serve-before.sha256"

if docker inspect assistx-api >/dev/null 2>&1; then
  docker inspect --format '{{.Image}}' assistx-api > "$EVIDENCE_DIR/api-image-before.txt"
  docker inspect --format '{{.Config.Image}}' assistx-api > "$EVIDENCE_DIR/api-image-name-before.txt"
else
  : > "$EVIDENCE_DIR/api-image-before.txt"
  : > "$EVIDENCE_DIR/api-image-name-before.txt"
fi

COMPOSE=(
  docker compose
  --env-file "$ASSISTX_ENV_FILE"
  -f docker-compose.yml
  -f "$ASSISTX_PROD_COMPOSE_FILE"
)

STAGE="build_api"
RESULT="BLOCKED"
note "Building only api from exact source $ACTUAL_SHA"
"${COMPOSE[@]}" build api

STAGE="recreate_api"
RESULT="BLOCKED"
note "Recreating only assistx-api"
"${COMPOSE[@]}" up -d --no-deps --force-recreate api

STAGE="api_health"
RESULT="BLOCKED"
note "Waiting for loopback health"
healthy=0
for _ in $(seq 1 60); do
  if curl --silent --show-error --fail http://127.0.0.1:8000/health       > "$EVIDENCE_DIR/health.json" 2> "$EVIDENCE_DIR/health.stderr"; then
    healthy=1
    break
  fi
  sleep 2
done
[[ "$healthy" -eq 1 ]] || die "AssistX api did not become healthy on loopback :8000"

docker inspect --format '{{.Image}}' assistx-api > "$EVIDENCE_DIR/api-image-after.txt"

RESULT="FAIL"
STAGE="loopback_containment"
note "Proving raw AssistX publication remains loopback-only"
docker inspect --format '{{range $binding := index .NetworkSettings.Ports "8000/tcp"}}{{println $binding.HostIp $binding.HostPort}}{{end}}'   assistx-api > "$EVIDENCE_DIR/api-port-bindings.txt"

python - "$EVIDENCE_DIR/api-port-bindings.txt" <<'PY'
from pathlib import Path
import sys

lines = [line.split() for line in Path(sys.argv[1]).read_text().splitlines() if line.strip()]
if not lines:
    raise SystemExit("no published 8000/tcp binding found")
for fields in lines:
    if len(fields) != 2:
        raise SystemExit(f"unexpected port binding: {fields!r}")
    host, port = fields
    if host not in {"127.0.0.1", "::1"} or port != "8000":
        raise SystemExit(f"unsafe AssistX publication: {host}:{port}")
PY

STAGE="credential_wiring"
note "Checking mobile-boundary configuration without revealing credentials"
docker exec -i assistx-api python - <<'PY' > "$EVIDENCE_DIR/api-runtime-config.json"
import json
import os

print(json.dumps({
    "trusted_auth_header": os.getenv("TRUSTED_AUTH_HEADER", ""),
    "router_token_present": bool(os.getenv("FLEET_ROUTER_BEARER_TOKEN", "")),
    "effective_hermes_provider": os.getenv("HERMES_PROVIDER", "assistx-router") or "assistx-router",
}, sort_keys=True))
PY

python - "$EVIDENCE_DIR/api-runtime-config.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("trusted_auth_header", "").lower() != "tailscale-user-login":
    raise SystemExit("TRUSTED_AUTH_HEADER is not Tailscale-User-Login")
if data.get("router_token_present") is not True:
    raise SystemExit("FLEET_ROUTER_BEARER_TOKEN is missing from assistx-api")
if data.get("effective_hermes_provider") != "assistx-router":
    raise SystemExit("effective Hermes provider is not assistx-router")
PY

STAGE="serve_immutability"
note "Proving Tailscale Serve topology did not change"
tailscale serve status --json > "$EVIDENCE_DIR/tailscale-serve-after.json"
sha256sum "$EVIDENCE_DIR/tailscale-serve-after.json" > "$EVIDENCE_DIR/tailscale-serve-after.sha256"
cmp -s "$EVIDENCE_DIR/tailscale-serve-before.json" "$EVIDENCE_DIR/tailscale-serve-after.json"   || die "Tailscale Serve topology changed; stop and review before any Agent Auto smoke"

STAGE="tailnet_identity"
note "Checking Tailnet identity through the existing route-scoped gateway"
whoami_status="$(
  curl --silent --show-error     --output "$EVIDENCE_DIR/whoami.json"     --write-out '%{http_code}'     "$KIPNERTER_GATEWAY_URL/api/v1/auth/whoami"
)"
printf '%s\n' "$whoami_status" > "$EVIDENCE_DIR/whoami-status.txt"
[[ "$whoami_status" == "200" ]] || die "Tailnet whoami returned HTTP $whoami_status"

python - "$EVIDENCE_DIR/whoami.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("authenticated") is not True or data.get("provider") != "tailscale":
    raise SystemExit("gateway did not authenticate this request through Tailscale")
PY

STAGE="executor_spoof_negative"
note "Checking executor-identity spoof rejection on loopback"
spoof_status="$(
  curl --silent --show-error     --output "$EVIDENCE_DIR/spoof-negative.json"     --write-out '%{http_code}'     -H 'x-assistx-executor-identity: spoofed-executor'     http://127.0.0.1:8000/api/v1/auth/whoami
)"
printf '%s\n' "$spoof_status" > "$EVIDENCE_DIR/spoof-negative-status.txt"
[[ "$spoof_status" == "401" ]] || die "spoofed executor identity returned HTTP $spoof_status, expected 401"

cat > "$EVIDENCE_DIR/agent-auto-request.json" <<'JSON'
{
  "model": "fleet-auto",
  "stream": false,
  "messages": [
    {
      "role": "user",
      "content": "Connectivity check only. Do not use tools, access files, call external services, or modify state. Reply AGENT_AUTO_OK."
    }
  ]
}
JSON

STAGE="agent_auto_smoke"
note "Sending exactly one bounded Agent Auto smoke through Serve"
chat_status="$(
  curl --silent --show-error     --dump-header "$EVIDENCE_DIR/agent-auto-response.headers"     --output "$EVIDENCE_DIR/agent-auto-response.json"     --write-out '%{http_code}'     -H 'Content-Type: application/json'     --data-binary "@$EVIDENCE_DIR/agent-auto-request.json"     "$KIPNERTER_GATEWAY_URL/api/v1/agent/chat/completions"
)"
printf '%s\n' "$chat_status" > "$EVIDENCE_DIR/agent-auto-status.txt"
[[ "$chat_status" == "200" ]] || die "Agent Auto smoke returned HTTP $chat_status; stop without extra router probes"

executor="$(
  awk -F': *' 'tolower($1)=="x-kipnerter-agent-executor" {gsub("\r","",$2); print tolower($2)}'     "$EVIDENCE_DIR/agent-auto-response.headers" | tail -n 1
)"
[[ "$executor" == "hermes" ]] || die "Agent Auto response did not prove Hermes execution"

python - "$EVIDENCE_DIR/agent-auto-response.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
choices = data.get("choices") or []
content = ""
if choices:
    content = str((choices[0].get("message") or {}).get("content") or "").strip()
if not content:
    raise SystemExit("Agent Auto response contained no assistant content")
PY

RESULT="PASS"
STAGE="complete"
cat > "$EVIDENCE_DIR/result.txt" <<EOF
KIPNERTER_AGENT_AUTO_LIVE_PASS
source_sha=$ACTUAL_SHA
gateway=$KIPNERTER_GATEWAY_URL
serve_topology=unchanged
loopback_containment=pass
tailnet_whoami=pass
executor_spoof_negative=pass
agent_auto_http=200
agent_executor=hermes
EOF

note "PASS: Kipnerter Agent Auto live path is verified at $ACTUAL_SHA"
note "Evidence: $EVIDENCE_DIR"
