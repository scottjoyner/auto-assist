#!/usr/bin/env bash
set -euo pipefail

API_PORT="${ASSISTX_API_PORT:-8000}"
TRUSTED_HEADER="${TRUSTED_AUTH_HEADER:-}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "ERROR: tailscale CLI is not installed on this host" >&2
  exit 2
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required" >&2
  exit 2
fi

if [[ "$TRUSTED_HEADER" != "Tailscale-User-Login" ]]; then
  cat >&2 <<'EOF'
ERROR: TRUSTED_AUTH_HEADER must be exactly Tailscale-User-Login before enabling
Kipnerter Tailnet SSO.

Set this in the AssistX production environment together with:
  ASSISTX_API_BIND=127.0.0.1
  TRUSTED_AUTH_HEADER=Tailscale-User-Login
  KIPNERTER_TAILNET_ALLOWED_LOGINS=<comma-separated allowed login(s)>   # recommended
Then recreate the AssistX api container and rerun this script.
EOF
  exit 3
fi

# Tailscale explicitly recommends a localhost-only backend when identity headers
# are used for authentication. Refuse to create the Serve proxy if the host port
# is still reachable directly from LAN/tailnet clients, because those clients
# could forge Tailscale-User-* headers.
if command -v ss >/dev/null 2>&1; then
  listeners="$(ss -ltnH 2>/dev/null | awk -v port=":${API_PORT}" '$4 ~ port"$" {print $4}' || true)"
  if grep -Eq "^(0\.0\.0\.0|\*|\[::\]):${API_PORT}$" <<<"$listeners"; then
    echo "ERROR: AssistX port ${API_PORT} is not localhost-only:" >&2
    echo "$listeners" >&2
    echo "Set ASSISTX_API_BIND=127.0.0.1 and recreate the api container first." >&2
    exit 4
  fi
  if [[ -n "$listeners" ]] && ! grep -Eq "^(127\.0\.0\.1|\[::1\]):${API_PORT}$" <<<"$listeners"; then
    echo "ERROR: unexpected AssistX listener for port ${API_PORT}:" >&2
    echo "$listeners" >&2
    echo "Only 127.0.0.1 or ::1 listeners are accepted for trusted identity mode." >&2
    exit 4
  fi
fi

curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:${API_PORT}/health" >/dev/null

tailscale status >/dev/null

# Modern Tailscale Serve proxies an HTTPS tailnet URL to 127.0.0.1:<port>.
# --bg persists the configuration after this command exits. This is Serve only;
# never use Funnel for the identity-trusting mobile gateway.
sudo tailscale serve --bg "${API_PORT}"

echo
sudo tailscale serve status

echo
cat <<EOF
Kipnerter Tailnet gateway configured.
Backend: http://127.0.0.1:${API_PORT}
Identity header: ${TRUSTED_HEADER}

From an enrolled user device, verify:
  GET https://<this-node>.<tailnet>.ts.net/api/v1/auth/whoami
  -> authenticated=true, provider=tailscale

Then verify:
  POST https://<this-node>.<tailnet>.ts.net/api/v1/agent/chat/completions

Do not expose port ${API_PORT} directly while TRUSTED_AUTH_HEADER is enabled.
EOF
