#!/usr/bin/env bash
# Private trace ingress preflight. Never creates public Funnel.
set -euo pipefail
PORT="${FLEET_TRACE_SERVE_PORT:-8447}"
LOCAL_PORT="${FLEET_TRACE_LOCAL_PORT:-8794}"

fail() { printf 'DENY: %s\n' "$*" >&2; exit 2; }
[[ "$PORT" =~ ^[0-9]+$ && "$LOCAL_PORT" =~ ^[0-9]+$ ]] || fail "invalid ports"
[[ "$PORT" -ge 1024 && "$PORT" -le 65535 ]] || fail "invalid Serve port"
[[ "$LOCAL_PORT" -ge 1024 && "$LOCAL_PORT" -le 65535 ]] || fail "invalid local port"
command -v tailscale >/dev/null || fail "Tailscale CLI missing"
command -v ss >/dev/null || fail "ss missing"
command -v curl >/dev/null || fail "curl missing"

listeners="$(ss -ltnH 2>/dev/null | awk -v port=":$LOCAL_PORT" '$4 ~ port"$" {print $4}')"
[[ -n "$listeners" ]] || fail "trace ingress is not running locally"
while IFS= read -r socket; do
  [[ "$socket" == "127.0.0.1:$LOCAL_PORT" || "$socket" == "[::1]:$LOCAL_PORT" ]] ||
    fail "trace backend has non-loopback socket"
done <<< "$listeners"
curl --fail --silent --show-error --max-time 3 \
  "http://127.0.0.1:$LOCAL_PORT/healthz" >/dev/null ||
    fail "localhost trace health check failed"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/verify_private_fleet_trace_serve.py" \
  --port "$PORT" --local-port "$LOCAL_PORT" ||
    fail "Serve route conflicts with existing path or Funnel"

if [[ "${1:-}" != "--apply" ]]; then
  echo "DRY RUN ONLY: no tailnet routes changed."
  echo "Rerun with --apply after reviewing grants and the ingress identity ACL."
  exit 0
fi
[[ "${FLEET_TRACE_APPLY_APPROVED:-}" == "YES" ]] ||
  fail "FLEET_TRACE_APPLY_APPROVED must equal YES for explicit route change"
tailscale serve --bg --https="$PORT" \
  --accept-app-caps=nez.ninja/cap/fleet-trace-write,nez.ninja/cap/fleet-trace-read \
  "http://127.0.0.1:$LOCAL_PORT"
python3 "$SCRIPT_DIR/verify_private_fleet_trace_serve.py" \
  --port "$PORT" --local-port "$LOCAL_PORT"
echo "Configured PRIVATE tailnet-only trace ingress. No Funnel created."
