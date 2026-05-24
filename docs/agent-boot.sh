#!/usr/bin/env bash
set -euo pipefail

ASSISTX_URL="${ASSISTX_URL:-http://assistx.local:8000}"
DEVICE_ID="$(hostname)-hermes"

curl -s -u "admin:change-me" -X POST "$ASSISTX_URL/api/devices/register" \
  -H "Content-Type: application/json" \
  -d "$(cat <<EOF
{
  "device_id": "$DEVICE_ID",
  "hostname": "$(hostname)",
  "platform": "$(uname -sm)",
  "capabilities": ["terminal", "research", "code"],
  "resources": {
    "ram_gb": $(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)
  },
  "max_concurrent_tasks": 2,
  "available_agents": ["hermes"],
  "tags": ["local"]
}
EOF
)"
