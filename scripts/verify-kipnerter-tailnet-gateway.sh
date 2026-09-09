#!/usr/bin/env bash
set -euo pipefail

API_PORT="${ASSISTX_API_PORT:-8000}"
TRUSTED_HEADER="${TRUSTED_AUTH_HEADER:-}"
IDENTITY_PROBE="${KIPNERTER_GATEWAY_IDENTITY_PROBE:-1}"
AGENT_SMOKE="${KIPNERTER_GATEWAY_AGENT_SMOKE:-0}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

for command_name in tailscale curl python3; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required"
done

[[ "$TRUSTED_HEADER" == "Tailscale-User-Login" ]] || \
  fail "TRUSTED_AUTH_HEADER must be exactly Tailscale-User-Login"

if command -v ss >/dev/null 2>&1; then
  listeners="$(ss -ltnH 2>/dev/null | awk -v port=":${API_PORT}" '$4 ~ port"$" {print $4}' || true)"
  [[ -n "$listeners" ]] || fail "no AssistX listener found on port ${API_PORT}"

  if grep -Eq "^(0\\.0\\.0\\.0|\\*|\\[::\\]):${API_PORT}$" <<<"$listeners"; then
    echo "$listeners" >&2
    fail "AssistX port ${API_PORT} is exposed on a wildcard listener"
  fi

  while IFS= read -r listener; do
    [[ -z "$listener" ]] && continue
    if ! grep -Eq "^(127\\.0\\.0\\.1|\\[::1\\]):${API_PORT}$" <<<"$listener"; then
      echo "$listeners" >&2
      fail "unexpected non-loopback AssistX listener: ${listener}"
    fi
  done <<<"$listeners"
fi

curl --fail --silent --show-error --max-time 5 \
  "http://127.0.0.1:${API_PORT}/health" >/dev/null

dns_name="$(tailscale status --json | python3 -c '
import json, sys
value = json.load(sys.stdin)
name = str((value.get("Self") or {}).get("DNSName") or "").strip().rstrip(".")
if not name:
    raise SystemExit("Tailscale status did not report Self.DNSName")
print(name)
')"

gateway_url="https://${dns_name}"
serve_status="$(tailscale serve status 2>&1 || true)"
printf '%s\n' "$serve_status"

if ! grep -Fq "$gateway_url" <<<"$serve_status"; then
  fail "Tailscale Serve does not report ${gateway_url}"
fi

if [[ "$IDENTITY_PROBE" == "1" ]]; then
  whoami_file="$(mktemp)"
  trap 'rm -f "${whoami_file:-}" "${agent_headers:-}" "${agent_body:-}"' EXIT

  whoami_status="$(curl --silent --show-error --max-time 10 \
    --output "$whoami_file" --write-out '%{http_code}' \
    "${gateway_url}/api/v1/auth/whoami")"
  [[ "$whoami_status" == "200" ]] || {
    cat "$whoami_file" >&2 || true
    fail "whoami returned HTTP ${whoami_status}"
  }

  python3 - "$whoami_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
if value.get("authenticated") is not True:
    raise SystemExit(f"whoami did not authenticate Tailnet identity: {value}")
if value.get("provider") != "tailscale":
    raise SystemExit(f"whoami provider is not tailscale: {value}")
login = str(value.get("login") or "").strip()
if not login:
    raise SystemExit(f"whoami did not return a login: {value}")
print(f"tailnet_identity={login}")
PY
fi

if [[ "$AGENT_SMOKE" == "1" ]]; then
  agent_headers="$(mktemp)"
  agent_body="$(mktemp)"
  agent_status="$(curl --silent --show-error --max-time 330 \
    --dump-header "$agent_headers" \
    --output "$agent_body" \
    --write-out '%{http_code}' \
    --request POST \
    --header 'Content-Type: application/json' \
    --header 'Accept: application/json' \
    --data '{"model":"hermes-agent","stream":false,"messages":[{"role":"user","content":"Reply with exactly: kipnerter gateway smoke ok"}]}' \
    "${gateway_url}/api/v1/agent/chat/completions")"

  [[ "$agent_status" == "200" ]] || {
    cat "$agent_body" >&2 || true
    fail "agent smoke returned HTTP ${agent_status}"
  }

  grep -Eiq '^X-Kipnerter-Agent-Executor:[[:space:]]*hermes[[:space:]]*$' "$agent_headers" || {
    cat "$agent_headers" >&2 || true
    fail "agent smoke response did not prove Hermes execution"
  }

  python3 - "$agent_body" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
choices = value.get("choices") or []
message = (choices[0] if choices else {}).get("message") or {}
content = str(message.get("content") or "").strip()
if not content:
    raise SystemExit(f"agent smoke returned no assistant content: {value}")
print("agent_executor=hermes")
print("agent_response_nonempty=true")
PY
fi

cat <<EOF
kipnerter_gateway_ready=true
kipnerter_gateway_url=${gateway_url}
assistx_backend=http://127.0.0.1:${API_PORT}
identity_probe=${IDENTITY_PROBE}
agent_smoke=${AGENT_SMOKE}
EOF
