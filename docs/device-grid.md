# Device Grid

Machines register themselves in Neo4j so the orchestrator can dispatch work to the right device based on capabilities and current load.

## Architecture

```
Machine boots  ──►  POST /api/devices/register  ──►  AgentDevice node in Neo4j
                       │
                       ▼
Every 60s       ──►  POST /api/devices/{id}/heartbeat  ──►  updates current_load, last_seen_at
                       │
                       ▼
Task needs doing ──►  create_dispatch() auto-selects lowest-loaded device
                       with matching capabilities via select_device_for_task()
```

## API

### Register a device

```
POST /api/devices/register
```

```json
{
  "device_id": "hermes-desktop-01",
  "hostname": "desktop-01.local",
  "platform": "linux/amd64",
  "capabilities": ["terminal", "research", "code"],
  "resources": {
    "llm_endpoint": "http://localhost:1234",
    "llm_model": "qwen2.5-35b",
    "ram_gb": 64,
    "gpu_vram_gb": 24,
    "disk_free_gb": 500
  },
  "max_concurrent_tasks": 2,
  "available_agents": ["hermes", "opencode"],
  "tags": ["local", "gpu", "fast"]
}
```

`device_id` must be unique. Re-registering the same id updates the record and resets the heartbeat timer.

### Heartbeat

```
POST /api/devices/{device_id}/heartbeat
```

```json
{
  "current_load": 1,
  "queue_depth": 0
}
```

Devices not seen in 5 minutes are excluded from dispatch selection.

### List devices

```
GET /api/devices
GET /api/devices/{device_id}
```

## Device Selection for Dispatch

When `create_dispatch()` is called without a `target_device_id`, it runs:

```
MATCH (d:AgentDevice)
WHERE d.last_seen_at_ts > (now() - 5min)
  AND d.current_load < d.max_concurrent_tasks
  AND all(cap IN $required_capabilities WHERE cap IN d.capabilities)
RETURN d
ORDER BY d.current_load / d.max_concurrent_tasks ASC
LIMIT 1
```

The selected device is stored as `target_device_id` on the Dispatch node.

## Boot Script Template

Save this as `/etc/assistx-agent-boot.sh` on each machine:

```bash
#!/usr/bin/env bash
set -euo pipefail

ASSISTX_URL="${ASSISTX_URL:-http://assistx.local:8000}"
DEVICE_ID="$(hostname)-hermes"

# Register on startup
curl -s -u "admin:change-me" -X POST "$ASSISTX_URL/api/devices/register" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "'"$DEVICE_ID"'",
    "hostname": "'"$(hostname)"'",
    "platform": "'"$(uname -sm)"'",
    "capabilities": ["terminal", "research", "code"],
    "resources": {
      "ram_gb": '"$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)"'
    },
    "max_concurrent_tasks": 2,
    "available_agents": ["hermes"],
    "tags": ["local"]
  }'
```

And a systemd timer for heartbeat (`/etc/systemd/system/assistx-heartbeat.service`):

```ini
[Unit]
Description=AssistX device heartbeat
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/curl -s -u "admin:change-me" -X POST \
  "http://assistx.local:8000/api/devices/$(hostname)-hermes/heartbeat" \
  -H "Content-Type: application/json" \
  -d '{"current_load":0,"queue_depth":0}'
```

With `/etc/systemd/system/assistx-heartbeat.timer`:

```ini
[Unit]
Description=AssistX heartbeat every 60s

[Timer]
OnBootSec=30
OnUnitActiveSec=60

[Install]
WantedBy=timers.target
```

## Neo4j Schema

```cypher
(:AgentDevice {
  id,                    // unique device id
  hostname,              // machine hostname
  platform,              // linux/amd64, darwin/arm64, etc.
  capabilities[],        // ["terminal", "research", "code", ...]
  resources_json,        // JSON: {cpu_cores, ram_gb, gpu, ...}
  max_concurrent_tasks,  // max parallel tasks this device can handle
  current_load,          // current running task count (updated by heartbeat)
  queue_depth,           // pending jobs waiting (updated by heartbeat)
  available_agents[],    // agents installed on this device
  tags[],                // free-form labels
  last_seen_at_ts,       // heartbeat timestamp
  created_at_ts,
  updated_at_ts
})
```

Indexed on: `hostname`, `last_seen_at_ts`, and (implicitly) `id`.
