#!/bin/bash
# register-node.sh - Register a node with the AssistX swarm
# Usage: ./register-node.sh <node_id> <display_name> <tailscale_ip> <os> <arch> [lan_ip] [roles...]
# Example: ./register-node.sh falcon "Falcon (demo-1)" 100.65.68.58 linux x86_64

ASSISTX_URL="${ASSISTX_URL:-http://172.20.0.5:8000}"
ASSISTX_USER="${ASSISTX_USER:-admin}"
ASSISTX_PASS="${ASSISTX_PASS:-change-me}"

NODE_ID="${1:?Usage: register-node.sh <node_id> <display_name> <tailscale_ip> <os> <arch> [lan_ip] [roles...]}"
DISPLAY_NAME="${2:?Missing display_name}"
TAILSCALE_IP="${3:?Missing tailscale_ip}"
OS="${4:?Missing os}"
ARCH="${5:?Missing arch}"
LAN_IP="${6:-}"
ROLES="${7:-hermes_agent,model_endpoint}"

# Build JSON payload
PAYLOAD=$(cat <<EOF
{
  "node_id": "${NODE_ID}",
  "hostname": "${NODE_ID}",
  "display_name": "${DISPLAY_NAME}",
  "status": "online",
  "roles": [$(echo "$ROLES" | sed 's/,/", "/g' | sed 's/^/"/' | sed 's/$/"/')],
  "tailscale_ip": "${TAILSCALE_IP}",
  "lan_ip": $(if [ -n "$LAN_IP" ]; then echo "\"$LAN_IP\""; else echo "null"; fi),
  "os": "${OS}",
  "arch": "${ARCH}"
}
EOF
)

# Register node
RESPONSE=$(curl -s -u "$ASSISTX_USER:$ASSISTX_PASS" \
  -X POST \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "${ASSISTX_URL}/api/swarm/nodes/register")

echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
