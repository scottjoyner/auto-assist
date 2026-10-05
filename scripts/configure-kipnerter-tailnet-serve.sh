#!/usr/bin/env bash
set -euo pipefail

API_PORT="${ASSISTX_API_PORT:-8000}"
SERVE_PORT="${KIPNERTER_GATEWAY_SERVE_PORT:-8443}"
TRUSTED_HEADER="${TRUSTED_AUTH_HEADER:-}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

for command_name in tailscale curl; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required"
done

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

# A backend that trusts Tailscale identity headers must not be directly reachable
# from LAN/tailnet clients, because direct clients could forge those headers.
if command -v ss >/dev/null 2>&1; then
  listeners="$(ss -ltnH 2>/dev/null | awk -v port=":${API_PORT}" '$4 ~ port"$" {print $4}' || true)"
  if grep -Eq "^(0\.0\.0\.0|\*|\[::\]):${API_PORT}$" <<<"$listeners"; then
    echo "ERROR: AssistX port ${API_PORT} is not localhost-only:" >&2
    echo "$listeners" >&2
    fail "set ASSISTX_API_BIND=127.0.0.1 and recreate the api container first"
  fi
  if [[ -n "$listeners" ]] && ! grep -Eq "^(127\.0\.0\.1|\[::1\]):${API_PORT}$" <<<"$listeners"; then
    echo "ERROR: unexpected AssistX listener for port ${API_PORT}:" >&2
    echo "$listeners" >&2
    fail "only 127.0.0.1 or ::1 listeners are accepted for trusted identity mode"
  fi
fi

curl --fail --silent --show-error --max-time 5 \
  "http://127.0.0.1:${API_PORT}/health" >/dev/null

tailscale status >/dev/null

serve_before="$(sudo tailscale serve status 2>&1 || true)"
printf '%s\n' "$serve_before"

# The live x1-370 topology already uses Tailscale Serve HTTPS :8443 for a
# Nextcloud root mount. That root is intentionally preserved. More-specific
# path mounts coexist with it and are the only AssistX routes added here.
# Standard host TLS :443 belongs to Caddy and must not be claimed by this helper.
#
# Refuse only when the whole AssistX backend is already exposed at a Serve root,
# or when one of the exact mobile paths is already mapped to a different target.
root_proxy="$(grep -E '\|-- /[[:space:]]+proxy http://127\.0\.0\.1:' <<<"$serve_before" || true)"
if grep -Eq "proxy http://127\.0\.0\.1:${API_PORT}([/[:space:]]|$)" <<<"$root_proxy"; then
  echo "$root_proxy" >&2
  fail "AssistX is already exposed as a whole Serve root; remove that unsafe mapping explicitly before continuing"
fi

check_path_conflict() {
  local path="$1"
  local target="$2"
  local existing
  existing="$(grep -F -- "-- ${path}" <<<"$serve_before" || true)"
  if [[ -n "$existing" ]] && ! grep -Fq "proxy ${target}" <<<"$existing"; then
    echo "$existing" >&2
    fail "Serve path ${path} is already owned by another target; refusing to overwrite it"
  fi
}

# The mobile surface, declared once. It used to be spelled out separately in the
  # conflict checks and the serve invocations below, so adding a route meant editing
  # two blocks in this file plus the verifier and its tests. That is how
  # /api/v1/model/chat/completions came to be served by FastAPI, tested, and still
  # unreachable from the phone: the per-model route existed and nothing checked that
  # the edge published it. One list means one edit, and the tests assert the list.
  MOBILE_PATHS=(
    /health
    /api/v1/auth/whoami
    /api/v1/runtime/catalog
    /api/v1/agent/chat/completions
    /api/v1/model/chat/completions
  )

  local path
  for path in "${MOBILE_PATHS[@]}"; do
    check_path_conflict "$path" "http://127.0.0.1:${API_PORT}${path}"
  done

  # Route-scoped mobile mounts only. --bg persists the Serve config across
  # command exit/reboot. Existing root/path/Funnel state is left untouched.
  for path in "${MOBILE_PATHS[@]}"; do
    sudo tailscale serve --https="${SERVE_PORT}" "--set-path=${path}" --bg \
      "http://127.0.0.1:${API_PORT}${path}"
  done

echo
sudo tailscale serve status

echo
cat <<EOF
Kipnerter Tailnet gateway configured with a route-scoped Serve surface.
Backend: http://127.0.0.1:${API_PORT}
Serve HTTPS port: ${SERVE_PORT}
Identity header: ${TRUSTED_HEADER}
Published AssistX paths:
  /health
  /api/v1/auth/whoami
  /api/v1/runtime/catalog
  /api/v1/agent/chat/completions

Existing unrelated Serve roots and Funnel mappings were not reset or replaced.

From an enrolled user device, verify:
  GET https://<this-node>.<tailnet>.ts.net:${SERVE_PORT}/api/v1/auth/whoami
  -> authenticated=true, provider=tailscale

Then verify:
  POST https://<this-node>.<tailnet>.ts.net:${SERVE_PORT}/api/v1/agent/chat/completions

Do not expose port ${API_PORT} directly, do not add an AssistX root Serve mapping,
and never use Funnel for this gateway.
EOF
